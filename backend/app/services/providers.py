from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlmodel import Session, select

from ..models import AuditEvent, ModelProfile, ProviderCredential, utc_now
from ..providers import LiteLLMProvider
from ..security.crypto import (
    CredentialCryptoError,
    decrypt_credential_secret,
    encrypt_credential_secret,
    read_master_key_file,
)

CONNECTION_TEST_NOTICE = (
    "This sends a minimal request to the configured model and may incur provider charges."
)
CONNECTION_TEST_TIMEOUT_SECONDS = 20.0
_CONNECTION_TEST_LIMIT = asyncio.Semaphore(4)

_PROVIDER_ALIASES = {
    "google": "gemini",
    "google-ai": "gemini",
    "google_ai": "gemini",
}
_PROVIDER_MODEL_RULES: dict[str, tuple[re.Pattern[str], ...]] = {
    "anthropic": (
        re.compile(r"^(?:anthropic/)?claude-[a-z0-9][a-z0-9._-]*$", re.I),
    ),
    "openai": (
        re.compile(r"^(?:openai/)?(?:gpt|o[134])-[a-z0-9][a-z0-9._-]*$", re.I),
    ),
    "gemini": (
        re.compile(r"^(?:gemini/)?gemini-[a-z0-9][a-z0-9._-]*$", re.I),
    ),
    "deepseek": (
        re.compile(r"^(?:deepseek/)?deepseek-[a-z0-9][a-z0-9._-]*$", re.I),
    ),
    "ollama": (
        re.compile(r"^ollama/[a-z0-9][a-z0-9._:/-]*$", re.I),
    ),
    "openrouter": (
        re.compile(r"^openrouter/[a-z0-9][a-z0-9._:/-]*$", re.I),
    ),
}
_PROVIDER_HOSTS: dict[str, frozenset[str]] = {
    "anthropic": frozenset({"api.anthropic.com"}),
    "openai": frozenset({"api.openai.com"}),
    "gemini": frozenset({"generativelanguage.googleapis.com"}),
    "deepseek": frozenset({"api.deepseek.com"}),
    "openrouter": frozenset({"openrouter.ai"}),
    "ollama": frozenset({"127.0.0.1", "localhost", "::1"}),
}
_GENERATION_PARAM_KEYS = frozenset(
    {
        "cache",
        "cache_control",
        "drop_params",
        "frequency_penalty",
        "logprobs",
        "max_retries",
        "max_tokens",
        "num_retries",
        "presence_penalty",
        "reasoning_effort",
        "response_format",
        "seed",
        "stop",
        "temperature",
        "thinking",
        "timeout",
        "top_k",
        "top_logprobs",
        "top_p",
    }
)
_SECRETISH_PARAM_RE = re.compile(r"(?:api[_-]?key|secret|token|authorization|password)", re.I)


class ProviderConfigurationError(ValueError):
    pass


class CredentialNotFoundError(LookupError):
    pass


class ModelProfileNotFoundError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class ProviderRuntime:
    profile_id: int
    provider_name: str
    model: str
    max_concurrency: int
    context_window_tokens: int
    max_output_tokens: int
    generation_params: dict[str, Any]
    provider: LiteLLMProvider


def normalize_provider(provider: str) -> str:
    normalized = _PROVIDER_ALIASES.get(provider.strip().casefold(), provider.strip().casefold())
    if normalized not in _PROVIDER_MODEL_RULES:
        raise ProviderConfigurationError("Unsupported provider")
    return normalized


def normalize_profile_label(label: str) -> tuple[str, str]:
    display = " ".join(label.split())
    if not display or len(display) > 150:
        raise ProviderConfigurationError("Credential profile label must be 1-150 characters")
    return display, display.casefold()


def validate_model_id(provider: str, model_id: str) -> str:
    normalized_provider = normalize_provider(provider)
    value = model_id.strip()
    if not value or len(value) > 255 or any(char.isspace() for char in value):
        raise ProviderConfigurationError("Invalid model ID")
    if not any(rule.fullmatch(value) for rule in _PROVIDER_MODEL_RULES[normalized_provider]):
        raise ProviderConfigurationError("Model ID is not allowlisted for this provider")
    return value


def validate_base_url(provider: str, base_url: str | None) -> str | None:
    if base_url is None or not base_url.strip():
        return None
    normalized_provider = normalize_provider(provider)
    parsed = urlsplit(base_url.strip())
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ProviderConfigurationError(
            "Base URL must not include credentials, query, or fragment"
        )
    expected_scheme = "http" if normalized_provider == "ollama" else "https"
    if parsed.scheme.casefold() != expected_scheme or not parsed.hostname:
        raise ProviderConfigurationError(
            f"Base URL for {normalized_provider} must use {expected_scheme}"
        )
    host = parsed.hostname.casefold().rstrip(".")
    if host not in _PROVIDER_HOSTS[normalized_provider]:
        raise ProviderConfigurationError("Base URL host is not allowlisted for this provider")
    if normalized_provider != "ollama" and parsed.port not in (None, 443):
        raise ProviderConfigurationError("Remote provider base URLs must use port 443")
    normalized_netloc = host
    if ":" in host and not host.startswith("["):
        normalized_netloc = f"[{host}]"
    if parsed.port is not None:
        normalized_netloc += f":{parsed.port}"
    path = parsed.path.rstrip("/")
    return urlunsplit((expected_scheme, normalized_netloc, path, "", ""))


def validate_generation_params(params: Mapping[str, Any] | None) -> dict[str, Any]:
    if not params:
        return {}
    rejected = [
        key
        for key in params
        if key not in _GENERATION_PARAM_KEYS or _SECRETISH_PARAM_RE.search(key)
    ]
    if rejected:
        raise ProviderConfigurationError(
            "Unsupported generation parameter(s): " + ", ".join(sorted(rejected))
        )
    values = dict(params)
    if "temperature" in values:
        temperature = float(values["temperature"])
        if not 0 <= temperature <= 2:
            raise ProviderConfigurationError("temperature must be between 0 and 2")
        values["temperature"] = temperature
    for key in ("top_p", "frequency_penalty", "presence_penalty"):
        if key in values:
            values[key] = float(values[key])
    for key in ("top_k", "max_tokens", "max_retries", "num_retries"):
        if key in values:
            values[key] = int(values[key])
    if "timeout" in values:
        timeout = float(values["timeout"])
        if not 0 < timeout <= 600:
            raise ProviderConfigurationError("timeout must be between 0 and 600 seconds")
        values["timeout"] = timeout
    return values


def mask_credential(secret: str) -> tuple[str | None, str]:
    suffix = secret[-4:] if secret else None
    return suffix, f"••••{suffix}" if suffix else "••••"


def credential_read_dto(credential: ProviderCredential) -> dict[str, Any]:
    suffix = credential.masked_suffix
    return {
        "id": credential.id,
        "provider": credential.provider,
        "profile_label": credential.profile_label,
        "configured": bool(credential.encrypted_ciphertext and credential.encryption_nonce),
        "masked_key": f"••••{suffix}" if suffix else "••••",
        "test_status": credential.test_status,
        "last_tested_at": credential.last_tested_at,
        "last_test_error_code": credential.last_test_error_code,
        "last_test_error_summary": credential.last_test_error_summary,
        "created_at": credential.created_at,
        "updated_at": credential.updated_at,
    }


def model_profile_read_dto(profile: ModelProfile) -> dict[str, Any]:
    return {
        "id": profile.id,
        "display_name": profile.display_name,
        "provider": profile.provider,
        "litellm_model_id": profile.litellm_model_id,
        "credential_id": profile.credential_id,
        "base_url": profile.base_url,
        "enabled": profile.enabled,
        "is_default": profile.is_default,
        "max_concurrency": profile.max_concurrency,
        "context_window_tokens": profile.context_window_tokens,
        "max_output_tokens": profile.max_output_tokens,
        "generation_params": dict(profile.generation_params or {}),
        "input_price_per_million": profile.input_price_per_million,
        "output_price_per_million": profile.output_price_per_million,
        "cache_read_price_per_million": profile.cache_read_price_per_million,
        "cache_write_price_per_million": profile.cache_write_price_per_million,
        "created_at": profile.created_at,
        "updated_at": profile.updated_at,
    }


def _require_credential_id(credential: ProviderCredential) -> int:
    if credential.id is None:
        raise RuntimeError("Credential was not persisted")
    return credential.id


def decrypt_credential(
    credential: ProviderCredential,
    *,
    master_key: bytes | None = None,
) -> str:
    return decrypt_credential_secret(
        credential.encrypted_ciphertext,
        credential.encryption_nonce,
        credential_id=_require_credential_id(credential),
        provider=credential.provider,
        key_version=credential.master_key_version,
        master_key=master_key,
    )


def _safe_test_error(exc: BaseException) -> tuple[str, str]:
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is not None:
        try:
            code = f"provider_http_{int(status)}"
        except (TypeError, ValueError):
            code = "provider_error"
    elif isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        code = "timeout"
    elif isinstance(exc, CredentialCryptoError):
        code = "credential_decryption_failed"
    elif isinstance(exc, ConnectionError):
        code = "connection_error"
    else:
        code = type(exc).__name__.lower()[:64] or "provider_error"
    summaries = {
        "timeout": "The provider did not respond before the test timeout.",
        "credential_decryption_failed": "The stored credential could not be decrypted.",
        "connection_error": "Could not connect to the provider.",
    }
    if code.startswith("provider_http_"):
        summary = "The provider rejected the connection test."
    else:
        summary = summaries.get(code, "The provider connection test failed.")
    return code, summary


def add_audit_event(
    session: Session,
    event_type: str,
    result: str,
    *,
    actor_user_id: int | None = None,
    admin_session_id: int | None = None,
    detail: Mapping[str, Any] | None = None,
) -> None:
    session.add(
        AuditEvent(
            actor_type="admin" if actor_user_id is not None else "system",
            actor_user_id=actor_user_id,
            session_id=admin_session_id,
            event_type=event_type,
            result=result,
            detail_json=dict(detail or {}),
        )
    )


class ProviderCredentialService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_credentials(self) -> list[ProviderCredential]:
        return list(
            self.session.exec(
                select(ProviderCredential).order_by(
                    ProviderCredential.provider, ProviderCredential.profile_label
                )
            ).all()
        )

    def get_credential(self, credential_id: int) -> ProviderCredential:
        credential = self.session.get(ProviderCredential, credential_id)
        if credential is None:
            raise CredentialNotFoundError("Credential not found")
        return credential

    def create_credential(
        self,
        *,
        provider: str,
        profile_label: str,
        secret: str,
        key_version: int = 1,
        master_key: bytes | None = None,
    ) -> ProviderCredential:
        provider = normalize_provider(provider)
        label, normalized_label = normalize_profile_label(profile_label)
        suffix, _ = mask_credential(secret)
        # Insert and flush first so the database-generated ID can be bound into
        # AES-GCM AAD. Placeholder bytes never commit: encryption and the final
        # flush are in the same caller-owned transaction.
        credential = ProviderCredential(
            provider=provider,
            profile_label=label,
            profile_label_normalized=normalized_label,
            encrypted_ciphertext=b"",
            encryption_nonce=b"",
            master_key_version=key_version,
            masked_suffix=suffix,
        )
        self.session.add(credential)
        self.session.flush()
        encrypted = encrypt_credential_secret(
            secret,
            credential_id=_require_credential_id(credential),
            provider=provider,
            key_version=key_version,
            master_key=master_key,
        )
        credential.encrypted_ciphertext = encrypted.ciphertext
        credential.encryption_nonce = encrypted.nonce
        credential.master_key_version = encrypted.key_version
        self.session.add(credential)
        self.session.flush()
        return credential

    def replace_credential(
        self,
        credential_id: int,
        *,
        secret: str,
        key_version: int | None = None,
        master_key: bytes | None = None,
    ) -> ProviderCredential:
        credential = self.get_credential(credential_id)
        version = key_version or credential.master_key_version
        encrypted = encrypt_credential_secret(
            secret,
            credential_id=_require_credential_id(credential),
            provider=credential.provider,
            key_version=version,
            master_key=master_key,
        )
        suffix, _ = mask_credential(secret)
        credential.encrypted_ciphertext = encrypted.ciphertext
        credential.encryption_nonce = encrypted.nonce
        credential.master_key_version = encrypted.key_version
        credential.masked_suffix = suffix
        credential.test_status = "untested"
        credential.last_tested_at = None
        credential.last_test_error_code = None
        credential.last_test_error_summary = None
        credential.updated_at = utc_now()
        self.session.add(credential)
        self.session.flush()
        return credential

    def delete_credential(self, credential_id: int) -> None:
        credential = self.get_credential(credential_id)
        linked = self.session.exec(
            select(ModelProfile.id).where(ModelProfile.credential_id == credential_id).limit(1)
        ).first()
        if linked is not None:
            raise ProviderConfigurationError(
                "Credential is referenced by a model profile and cannot be deleted"
            )
        self.session.delete(credential)
        self.session.flush()

    def decrypt(self, credential_id: int, *, master_key: bytes | None = None) -> str:
        return decrypt_credential(self.get_credential(credential_id), master_key=master_key)

    def list_profiles(self) -> list[ModelProfile]:
        return list(
            self.session.exec(
                select(ModelProfile).order_by(ModelProfile.display_name, ModelProfile.id)
            ).all()
        )

    def get_profile(self, profile_id: int) -> ModelProfile:
        profile = self.session.get(ModelProfile, profile_id)
        if profile is None:
            raise ModelProfileNotFoundError("Model profile not found")
        return profile

    def _validate_profile_values(
        self,
        *,
        provider: str,
        model_id: str,
        credential_id: int | None,
        base_url: str | None,
        generation_params: Mapping[str, Any] | None,
        max_concurrency: int,
        context_window_tokens: int,
        max_output_tokens: int,
    ) -> tuple[str, str, str | None, dict[str, Any]]:
        provider = normalize_provider(provider)
        model_id = validate_model_id(provider, model_id)
        base_url = validate_base_url(provider, base_url)
        generation = validate_generation_params(generation_params)
        if not 1 <= max_concurrency <= 32:
            raise ProviderConfigurationError("max_concurrency must be between 1 and 32")
        if context_window_tokens < 1:
            raise ProviderConfigurationError("context_window_tokens must be positive")
        if not 1 <= max_output_tokens <= context_window_tokens:
            raise ProviderConfigurationError(
                "max_output_tokens must be positive and not exceed the context window"
            )
        if credential_id is not None:
            credential = self.get_credential(credential_id)
            if credential.provider != provider:
                raise ProviderConfigurationError(
                    "Credential provider does not match the model profile provider"
                )
        return provider, model_id, base_url, generation

    def create_profile(self, **values: Any) -> ModelProfile:
        display_name = " ".join(str(values["display_name"]).split())
        if not display_name or len(display_name) > 150:
            raise ProviderConfigurationError("Display name must be 1-150 characters")
        provider, model_id, base_url, generation = self._validate_profile_values(
            provider=str(values["provider"]),
            model_id=str(values["litellm_model_id"]),
            credential_id=values.get("credential_id"),
            base_url=values.get("base_url"),
            generation_params=values.get("generation_params"),
            max_concurrency=int(values.get("max_concurrency", 4)),
            context_window_tokens=int(values.get("context_window_tokens", 128000)),
            max_output_tokens=int(values.get("max_output_tokens", 4096)),
        )
        enabled = bool(values.get("enabled", True))
        is_default = bool(values.get("is_default", False))
        if is_default and not enabled:
            raise ProviderConfigurationError("The default profile must be enabled")
        if is_default:
            self._clear_default()
        profile = ModelProfile(
            display_name=display_name,
            provider=provider,
            litellm_model_id=model_id,
            credential_id=values.get("credential_id"),
            base_url=base_url,
            enabled=enabled,
            is_default=is_default,
            max_concurrency=int(values.get("max_concurrency", 4)),
            context_window_tokens=int(values.get("context_window_tokens", 128000)),
            max_output_tokens=int(values.get("max_output_tokens", 4096)),
            generation_params=generation,
            input_price_per_million=_decimal(values.get("input_price_per_million")),
            output_price_per_million=_decimal(values.get("output_price_per_million")),
            cache_read_price_per_million=_decimal(values.get("cache_read_price_per_million")),
            cache_write_price_per_million=_decimal(values.get("cache_write_price_per_million")),
        )
        self.session.add(profile)
        self.session.flush()
        return profile

    def update_profile(self, profile_id: int, changes: Mapping[str, Any]) -> ModelProfile:
        profile = self.get_profile(profile_id)
        values = {
            "provider": changes.get("provider", profile.provider),
            "litellm_model_id": changes.get("litellm_model_id", profile.litellm_model_id),
            "credential_id": changes.get("credential_id", profile.credential_id),
            "base_url": changes.get("base_url", profile.base_url),
            "generation_params": changes.get("generation_params", profile.generation_params),
            "max_concurrency": changes.get("max_concurrency", profile.max_concurrency),
            "context_window_tokens": changes.get(
                "context_window_tokens", profile.context_window_tokens
            ),
            "max_output_tokens": changes.get("max_output_tokens", profile.max_output_tokens),
        }
        provider, model_id, base_url, generation = self._validate_profile_values(
            provider=str(values["provider"]),
            model_id=str(values["litellm_model_id"]),
            credential_id=values["credential_id"],
            base_url=values["base_url"],
            generation_params=values["generation_params"],
            max_concurrency=int(values["max_concurrency"]),
            context_window_tokens=int(values["context_window_tokens"]),
            max_output_tokens=int(values["max_output_tokens"]),
        )
        enabled = bool(changes.get("enabled", profile.enabled))
        is_default = bool(changes.get("is_default", profile.is_default))
        if is_default and not enabled:
            raise ProviderConfigurationError("The default profile must be enabled")
        if is_default and not profile.is_default:
            self._clear_default(except_id=profile.id)
        display_name = " ".join(str(changes.get("display_name", profile.display_name)).split())
        if not display_name or len(display_name) > 150:
            raise ProviderConfigurationError("Display name must be 1-150 characters")
        profile.display_name = display_name
        profile.provider = provider
        profile.litellm_model_id = model_id
        profile.credential_id = values["credential_id"]
        profile.base_url = base_url
        profile.enabled = enabled
        profile.is_default = is_default
        profile.max_concurrency = int(values["max_concurrency"])
        profile.context_window_tokens = int(values["context_window_tokens"])
        profile.max_output_tokens = int(values["max_output_tokens"])
        profile.generation_params = generation
        for name in (
            "input_price_per_million",
            "output_price_per_million",
            "cache_read_price_per_million",
            "cache_write_price_per_million",
        ):
            if name in changes:
                setattr(profile, name, _decimal(changes[name]))
        profile.updated_at = utc_now()
        self.session.add(profile)
        self.session.flush()
        return profile

    def set_default_profile(self, profile_id: int) -> ModelProfile:
        profile = self.get_profile(profile_id)
        if not profile.enabled:
            raise ProviderConfigurationError("A disabled model profile cannot be default")
        self._clear_default(except_id=profile.id)
        profile.is_default = True
        profile.updated_at = utc_now()
        self.session.add(profile)
        self.session.flush()
        return profile

    def set_profile_enabled(self, profile_id: int, enabled: bool) -> ModelProfile:
        profile = self.get_profile(profile_id)
        if not enabled and profile.is_default:
            raise ProviderConfigurationError("The default model profile cannot be disabled")
        profile.enabled = enabled
        profile.updated_at = utc_now()
        self.session.add(profile)
        self.session.flush()
        return profile

    def delete_profile(self, profile_id: int) -> None:
        profile = self.get_profile(profile_id)
        if profile.is_default:
            raise ProviderConfigurationError("The default model profile cannot be deleted")
        self.session.delete(profile)
        self.session.flush()

    def _clear_default(self, *, except_id: int | None = None) -> None:
        defaults = self.session.exec(
            select(ModelProfile).where(ModelProfile.is_default.is_(True))
        ).all()
        for current in defaults:
            if current.id == except_id:
                continue
            current.is_default = False
            current.updated_at = utc_now()
            self.session.add(current)
        self.session.flush()


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    converted = Decimal(str(value))
    if converted < 0:
        raise ProviderConfigurationError("Pricing values cannot be negative")
    return converted


def build_provider_runtime(
    session: Session,
    profile_id: int | None = None,
    *,
    master_key: bytes | None = None,
) -> ProviderRuntime:
    if profile_id is None:
        profile = session.exec(
            select(ModelProfile).where(
                ModelProfile.is_default.is_(True),
                ModelProfile.enabled.is_(True),
            )
        ).first()
        if profile is None:
            raise ModelProfileNotFoundError("No enabled default model profile is configured")
    else:
        profile = session.get(ModelProfile, profile_id)
        if profile is None:
            raise ModelProfileNotFoundError("Model profile not found")
        if not profile.enabled:
            raise ProviderConfigurationError("Model profile is disabled")
    api_key: str | None = None
    if profile.credential_id is not None:
        credential = session.get(ProviderCredential, profile.credential_id)
        if credential is None or not credential.enabled:
            raise ProviderConfigurationError("Model profile credential is unavailable")
        api_key = decrypt_credential(credential, master_key=master_key)
    generation_params = validate_generation_params(profile.generation_params)
    provider = LiteLLMProvider(
        api_key=api_key,
        api_base=profile.base_url,
        **generation_params,
    )
    if profile.id is None:
        raise RuntimeError("Model profile was not persisted")
    return ProviderRuntime(
        profile_id=profile.id,
        provider_name=profile.provider,
        model=profile.litellm_model_id,
        max_concurrency=min(32, max(1, profile.max_concurrency)),
        context_window_tokens=profile.context_window_tokens,
        max_output_tokens=profile.max_output_tokens,
        generation_params=generation_params,
        provider=provider,
    )


def create_provider_for_profile(
    session: Session,
    profile_id: int | None = None,
    *,
    master_key: bytes | None = None,
) -> LiteLLMProvider:
    """Stable Translator seam: resolve/decrypt once, return a short-lived provider."""

    return build_provider_runtime(session, profile_id, master_key=master_key).provider


async def run_connection_test(
    runtime: ProviderRuntime,
    *,
    timeout: float = CONNECTION_TEST_TIMEOUT_SECONDS,
) -> tuple[bool, str | None, str | None, str | None]:
    """Perform a bounded, minimal provider call without retaining DB objects."""

    try:
        async with _CONNECTION_TEST_LIMIT:
            result = await asyncio.wait_for(
                runtime.provider.translate(
                    "OK",
                    system_prompt="Return a minimal acknowledgement.",
                    context="",
                    model=runtime.model,
                    temperature=0.0,
                    completion_options={"max_tokens": 1, "max_retries": 0},
                ),
                timeout=timeout,
            )
        return True, result.request_id, None, None
    except Exception as exc:
        code, summary = _safe_test_error(exc)
        return False, None, code, summary


def rotate_credentials(
    session: Session,
    *,
    from_key: bytes,
    to_key: bytes,
    credentials: Iterable[ProviderCredential] | None = None,
) -> int:
    """Re-encrypt every credential atomically in the caller's transaction."""

    if len(from_key) != 32 or len(to_key) != 32:
        raise CredentialCryptoError("Master keys must each be exactly 32 bytes")
    rows = list(
        credentials
        if credentials is not None
        else session.exec(select(ProviderCredential).order_by(ProviderCredential.id)).all()
    )
    for credential in rows:
        secret = decrypt_credential(credential, master_key=from_key)
        new_version = credential.master_key_version + 1
        encrypted = encrypt_credential_secret(
            secret,
            credential_id=_require_credential_id(credential),
            provider=credential.provider,
            key_version=new_version,
            master_key=to_key,
        )
        credential.encrypted_ciphertext = encrypted.ciphertext
        credential.encryption_nonce = encrypted.nonce
        credential.master_key_version = encrypted.key_version
        credential.updated_at = utc_now()
        session.add(credential)
    session.flush()
    return len(rows)


def rotate_credentials_from_files(
    session: Session,
    *,
    from_file: str | Path,
    to_file: str | Path,
) -> int:
    return rotate_credentials(
        session,
        from_key=read_master_key_file(from_file),
        to_key=read_master_key_file(to_file),
    )


__all__ = [
    "CONNECTION_TEST_NOTICE",
    "CONNECTION_TEST_TIMEOUT_SECONDS",
    "CredentialNotFoundError",
    "ModelProfileNotFoundError",
    "ProviderConfigurationError",
    "ProviderCredentialService",
    "ProviderRuntime",
    "add_audit_event",
    "build_provider_runtime",
    "create_provider_for_profile",
    "credential_read_dto",
    "decrypt_credential",
    "mask_credential",
    "model_profile_read_dto",
    "normalize_provider",
    "rotate_credentials",
    "rotate_credentials_from_files",
    "run_connection_test",
    "validate_base_url",
    "validate_generation_params",
    "validate_model_id",
]
