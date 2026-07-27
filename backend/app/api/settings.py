from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlmodel import Session

from ..config import Settings, get_settings
from ..db import get_session
from ..engine.prompt import TEMPLATE_PLACEHOLDERS
from ..models import utc_now
from ..schemas import (
    ConnectionTestResult,
    ModelProfileCreate,
    ModelProfilePatch,
    ModelProfileRead,
    PromptPreviewRequest,
    PromptPreviewResponse,
    PromptTemplateCreate,
    PromptTemplatePatch,
    PromptTemplateRead,
    ProviderCredentialCreate,
    ProviderCredentialRead,
    ProviderCredentialRotate,
    ProviderOption,
    PublicSettings,
)
from ..security.crypto import CredentialCryptoError, master_key_is_ephemeral
from ..security.dependencies import AuthenticatedAdmin, AuthenticatedAdminDependency
from ..services.prompts import (
    PromptTemplateError,
    PromptTemplateNotFoundError,
    PromptTemplateService,
    prompt_template_read_dto,
)
from ..services.providers import (
    CONNECTION_TEST_NOTICE,
    GENERATION_PARAM_KEYS,
    SUPPORTED_PROVIDERS,
    CredentialNotFoundError,
    ModelProfileNotFoundError,
    ProviderConfigurationError,
    ProviderCredentialService,
    add_audit_event,
    base_url_is_plaintext_remote,
    build_provider_runtime,
    credential_read_dto,
    model_profile_read_dto,
    run_connection_test,
)

router = APIRouter(tags=["settings"])

# Providers that route on a model prefix and need no endpoint from the admin.
_PROVIDER_LABELS: dict[str, tuple[str, str]] = {
    "openai": ("OpenAI", "官方 OpenAI 接口，模型如 gpt-5-mini。"),
    "anthropic": ("Anthropic", "Claude 系列，模型如 claude-sonnet-5。"),
    "gemini": ("Google Gemini", "Gemini 系列，模型如 gemini-3.6-flash。"),
    "deepseek": ("DeepSeek", "DeepSeek 系列，模型如 deepseek-v4-flash。"),
    "openrouter": ("OpenRouter", "聚合网关，模型如 openrouter/auto。"),
    "ollama": ("Ollama / 本地", "本地推理服务，需填写 http 地址。"),
    "custom": (
        "自定义（OpenAI 兼容）",
        "任意 OpenAI 兼容端点：中转站、聚合器或自建 vLLM / LM Studio。",
    ),
}
# Providers whose endpoint is fixed by LiteLLM; a base URL is optional there.
_ENDPOINT_REQUIRED = frozenset({"custom", "ollama"})

_SUGGESTED_MODELS = [
    "gpt-5-mini",
    "gpt-5.4-mini",
    "claude-sonnet-5",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "deepseek-v4-flash",
    "deepseek-v4-pro",
    "qwen3",
]


def _no_store(response: Response) -> None:
    # Credential and profile listings must never be cached by a proxy or browser.
    response.headers["Cache-Control"] = "no-store"


def _audit(
    session: Session,
    admin: AuthenticatedAdmin,
    event_type: str,
    result: str,
    **detail: object,
) -> None:
    """Record an admin configuration change.

    Detail values are identifiers and reasons only — never secret material,
    since audit rows are long-lived and widely readable.
    """

    add_audit_event(
        session,
        event_type,
        result,
        actor_user_id=admin.admin.id,
        admin_session_id=admin.session.id,
        detail={key: value for key, value in detail.items() if value is not None},
    )


def _credential_dto(credential: object) -> ProviderCredentialRead:
    return ProviderCredentialRead.model_validate(credential_read_dto(credential))


def _profile_dto(profile: object) -> ModelProfileRead:
    data = model_profile_read_dto(profile)
    data["insecure_transport"] = base_url_is_plaintext_remote(data.get("base_url"))
    return ModelProfileRead.model_validate(data)


def _template_dto(template: object) -> PromptTemplateRead:
    return PromptTemplateRead.model_validate(prompt_template_read_dto(template))


def _provider_options() -> list[ProviderOption]:
    options: list[ProviderOption] = []
    for name in SUPPORTED_PROVIDERS:
        label, hint = _PROVIDER_LABELS.get(name, (name, ""))
        options.append(
            ProviderOption(
                name=name,
                label=label,
                hint=hint,
                requires_base_url=name in _ENDPOINT_REQUIRED,
            )
        )
    return options


@router.get(
    "/settings",
    response_model=PublicSettings,
    summary="Get non-secret runtime defaults and supported options",
)
def public_settings(settings: Settings = Depends(get_settings)) -> PublicSettings:
    return PublicSettings(
        app_name=settings.app_name,
        app_version=settings.app_version,
        upload_limit_mb=settings.max_upload_mb,
        supported_source_types=["epub", "md"],
        supported_export_modes=["bilingual", "target_only"],
        provider_defaults=settings.default_provider_config,
        suggested_models=_SUGGESTED_MODELS,
        segment_max_chars=settings.segment_max_chars,
        providers=_provider_options(),
        generation_param_keys=sorted(GENERATION_PARAM_KEYS),
        prompt_placeholders=list(TEMPLATE_PLACEHOLDERS),
        connection_test_notice=CONNECTION_TEST_NOTICE,
        credential_key_is_ephemeral=master_key_is_ephemeral(),
    )


@router.get(
    "/settings/credentials",
    response_model=list[ProviderCredentialRead],
    summary="List provider credentials (secrets are never returned)",
)
def list_credentials(
    response: Response,
    admin: AuthenticatedAdminDependency,
    session: Annotated[Session, Depends(get_session)],
) -> list[ProviderCredentialRead]:
    del admin
    _no_store(response)
    return [
        _credential_dto(credential)
        for credential in ProviderCredentialService(session).list_credentials()
    ]


@router.post(
    "/settings/credentials",
    response_model=ProviderCredentialRead,
    status_code=status.HTTP_201_CREATED,
    summary="Store an encrypted provider API key",
)
def create_credential(
    payload: ProviderCredentialCreate,
    response: Response,
    admin: AuthenticatedAdminDependency,
    session: Annotated[Session, Depends(get_session)],
) -> ProviderCredentialRead:
    _no_store(response)
    service = ProviderCredentialService(session)
    try:
        credential = service.create_credential(
            provider=payload.provider,
            profile_label=payload.profile_label,
            secret=payload.api_key.get_secret_value(),
        )
    except ProviderConfigurationError as exc:
        _audit(session, admin, "credential.create", "denied", reason=str(exc))
        session.commit()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except CredentialCryptoError as exc:
        # The message is deliberately generic: it must not describe key material.
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    _audit(
        session,
        admin,
        "credential.create",
        "succeeded",
        credential_id=credential.id,
        provider=credential.provider,
    )
    try:
        session.commit()
    except Exception as exc:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="A credential with this provider and label already exists",
        ) from exc
    session.refresh(credential)
    return _credential_dto(credential)


@router.put(
    "/settings/credentials/{credential_id}",
    response_model=ProviderCredentialRead,
    summary="Replace a stored API key (rotation)",
)
def replace_credential_secret(
    credential_id: int,
    payload: ProviderCredentialRotate,
    response: Response,
    admin: AuthenticatedAdminDependency,
    session: Annotated[Session, Depends(get_session)],
) -> ProviderCredentialRead:
    _no_store(response)
    service = ProviderCredentialService(session)
    try:
        credential = service.replace_credential(
            credential_id,
            secret=payload.api_key.get_secret_value(),
        )
    except CredentialNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Credential not found") from exc
    except CredentialCryptoError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    _audit(
        session,
        admin,
        "credential.rotate",
        "succeeded",
        credential_id=credential_id,
    )
    session.commit()
    session.refresh(credential)
    return _credential_dto(credential)


@router.delete(
    "/settings/credentials/{credential_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a stored API key",
)
def delete_credential(
    credential_id: int,
    admin: AuthenticatedAdminDependency,
    session: Annotated[Session, Depends(get_session)],
) -> None:
    service = ProviderCredentialService(session)
    try:
        service.delete_credential(credential_id)
    except CredentialNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Credential not found") from exc
    except ProviderConfigurationError as exc:
        # Still referenced by a model profile; deleting would break translation.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _audit(
        session,
        admin,
        "credential.delete",
        "succeeded",
        credential_id=credential_id,
    )
    session.commit()


@router.get(
    "/settings/model-profiles",
    response_model=list[ModelProfileRead],
    summary="List configured model profiles",
)
def list_model_profiles(
    response: Response,
    admin: AuthenticatedAdminDependency,
    session: Annotated[Session, Depends(get_session)],
) -> list[ModelProfileRead]:
    del admin
    _no_store(response)
    return [_profile_dto(profile) for profile in ProviderCredentialService(session).list_profiles()]


@router.post(
    "/settings/model-profiles",
    response_model=ModelProfileRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a model profile",
)
def create_model_profile(
    payload: ModelProfileCreate,
    response: Response,
    admin: AuthenticatedAdminDependency,
    session: Annotated[Session, Depends(get_session)],
) -> ModelProfileRead:
    _no_store(response)
    service = ProviderCredentialService(session)
    try:
        profile = service.create_profile(**payload.model_dump(exclude_none=True))
    except CredentialNotFoundError as exc:
        raise HTTPException(status_code=422, detail="Credential not found") from exc
    except ProviderConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _audit(
        session,
        admin,
        "model_profile.create",
        "succeeded",
        provider=profile.provider,
        model_id=profile.litellm_model_id,
    )
    try:
        session.commit()
    except Exception as exc:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="A model profile with this display name already exists",
        ) from exc
    session.refresh(profile)
    return _profile_dto(profile)


@router.patch(
    "/settings/model-profiles/{profile_id}",
    response_model=ModelProfileRead,
    summary="Update a model profile",
)
def update_model_profile(
    profile_id: int,
    payload: ModelProfilePatch,
    response: Response,
    admin: AuthenticatedAdminDependency,
    session: Annotated[Session, Depends(get_session)],
) -> ModelProfileRead:
    _no_store(response)
    service = ProviderCredentialService(session)
    try:
        profile = service.update_profile(profile_id, payload.model_dump(exclude_unset=True))
    except ModelProfileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Model profile not found") from exc
    except CredentialNotFoundError as exc:
        raise HTTPException(status_code=422, detail="Credential not found") from exc
    except ProviderConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _audit(session, admin, "model_profile.update", "succeeded", profile_id=profile_id)
    try:
        session.commit()
    except Exception as exc:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="A model profile with this display name already exists",
        ) from exc
    session.refresh(profile)
    return _profile_dto(profile)


@router.post(
    "/settings/model-profiles/{profile_id}/default",
    response_model=ModelProfileRead,
    summary="Make a model profile the default for new books",
)
def set_default_model_profile(
    profile_id: int,
    response: Response,
    admin: AuthenticatedAdminDependency,
    session: Annotated[Session, Depends(get_session)],
) -> ModelProfileRead:
    _no_store(response)
    try:
        profile = ProviderCredentialService(session).set_default_profile(profile_id)
    except ModelProfileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Model profile not found") from exc
    except ProviderConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _audit(session, admin, "model_profile.set_default", "succeeded", profile_id=profile_id)
    session.commit()
    session.refresh(profile)
    return _profile_dto(profile)


@router.delete(
    "/settings/model-profiles/{profile_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a model profile",
)
def delete_model_profile(
    profile_id: int,
    admin: AuthenticatedAdminDependency,
    session: Annotated[Session, Depends(get_session)],
) -> None:
    try:
        ProviderCredentialService(session).delete_profile(profile_id)
    except ModelProfileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Model profile not found") from exc
    except ProviderConfigurationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _audit(session, admin, "model_profile.delete", "succeeded", profile_id=profile_id)
    session.commit()


@router.post(
    "/settings/model-profiles/{profile_id}/test",
    response_model=ConnectionTestResult,
    summary="Send one minimal request to verify the endpoint, key and model",
    description=CONNECTION_TEST_NOTICE,
)
async def test_model_profile(
    profile_id: int,
    response: Response,
    admin: AuthenticatedAdminDependency,
    session: Annotated[Session, Depends(get_session)],
) -> ConnectionTestResult:
    _no_store(response)
    service = ProviderCredentialService(session)
    try:
        profile = service.get_profile(profile_id)
    except ModelProfileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Model profile not found") from exc
    try:
        runtime = build_provider_runtime(session, profile_id)
    except (ProviderConfigurationError, CredentialCryptoError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    succeeded, _request_id, error_code, error_summary = await run_connection_test(runtime)

    credential = None
    if profile.credential_id is not None:
        credential = service.get_credential(profile.credential_id)
        credential.test_status = "valid" if succeeded else "invalid"
        credential.last_tested_at = utc_now()
        credential.last_test_error_code = error_code
        credential.last_test_error_summary = error_summary
        credential.updated_at = credential.last_tested_at
        session.add(credential)
    _audit(
        session,
        admin,
        "model_profile.test",
        "succeeded" if succeeded else "failed",
        profile_id=profile_id,
        error_code=error_code,
    )
    session.commit()
    return ConnectionTestResult(
        ok=succeeded,
        provider=profile.provider,
        model=runtime.model,
        tested_at=credential.last_tested_at if credential else utc_now(),
        error_code=error_code,
        error_summary=error_summary,
    )


@router.get(
    "/settings/prompt-templates",
    response_model=list[PromptTemplateRead],
    summary="List translation prompt templates",
)
def list_prompt_templates(
    admin: AuthenticatedAdminDependency,
    session: Annotated[Session, Depends(get_session)],
) -> list[PromptTemplateRead]:
    del admin
    return [
        PromptTemplateRead.model_validate(prompt_template_read_dto(template))
        for template in PromptTemplateService(session).list_templates()
    ]


@router.post(
    "/settings/prompt-templates",
    response_model=PromptTemplateRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a translation prompt template",
)
def create_prompt_template(
    payload: PromptTemplateCreate,
    admin: AuthenticatedAdminDependency,
    session: Annotated[Session, Depends(get_session)],
) -> PromptTemplateRead:
    try:
        template = PromptTemplateService(session).create_template(
            **payload.model_dump(exclude_none=True)
        )
    except PromptTemplateError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _audit(session, admin, "prompt_template.create", "succeeded", name=template.name)
    try:
        session.commit()
    except Exception as exc:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="A prompt template with this name already exists",
        ) from exc
    session.refresh(template)
    return PromptTemplateRead.model_validate(prompt_template_read_dto(template))


@router.patch(
    "/settings/prompt-templates/{template_id}",
    response_model=PromptTemplateRead,
    summary="Update a translation prompt template",
)
def update_prompt_template(
    template_id: int,
    payload: PromptTemplatePatch,
    admin: AuthenticatedAdminDependency,
    session: Annotated[Session, Depends(get_session)],
) -> PromptTemplateRead:
    try:
        template = PromptTemplateService(session).update_template(
            template_id,
            payload.model_dump(exclude_unset=True),
        )
    except PromptTemplateNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Prompt template not found") from exc
    except PromptTemplateError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _audit(session, admin, "prompt_template.update", "succeeded", template_id=template_id)
    try:
        session.commit()
    except Exception as exc:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="A prompt template with this name already exists",
        ) from exc
    session.refresh(template)
    return PromptTemplateRead.model_validate(prompt_template_read_dto(template))


@router.post(
    "/settings/prompt-templates/{template_id}/default",
    response_model=PromptTemplateRead,
    summary="Make a prompt template the default",
)
def set_default_prompt_template(
    template_id: int,
    admin: AuthenticatedAdminDependency,
    session: Annotated[Session, Depends(get_session)],
) -> PromptTemplateRead:
    try:
        template = PromptTemplateService(session).set_default_template(template_id)
    except PromptTemplateNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Prompt template not found") from exc
    except PromptTemplateError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _audit(session, admin, "prompt_template.set_default", "succeeded", template_id=template_id)
    session.commit()
    session.refresh(template)
    return PromptTemplateRead.model_validate(prompt_template_read_dto(template))


@router.delete(
    "/settings/prompt-templates/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a prompt template",
)
def delete_prompt_template(
    template_id: int,
    admin: AuthenticatedAdminDependency,
    session: Annotated[Session, Depends(get_session)],
) -> None:
    try:
        PromptTemplateService(session).delete_template(template_id)
    except PromptTemplateNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Prompt template not found") from exc
    except PromptTemplateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _audit(session, admin, "prompt_template.delete", "succeeded", template_id=template_id)
    session.commit()


@router.post(
    "/settings/prompt-templates/preview",
    response_model=PromptPreviewResponse,
    summary="Render a prompt template against a language pair",
)
def preview_prompt_template(
    payload: PromptPreviewRequest,
    admin: AuthenticatedAdminDependency,
    session: Annotated[Session, Depends(get_session)],
) -> PromptPreviewResponse:
    del admin
    service = PromptTemplateService(session)
    try:
        rendered = service.preview(
            template_id=payload.template_id,
            system_prompt=payload.system_prompt,
            user_prefix=payload.user_prefix,
            source_lang=payload.source_lang,
            target_lang=payload.target_lang,
        )
    except PromptTemplateNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Prompt template not found") from exc
    except PromptTemplateError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return PromptPreviewResponse(rendered=rendered, placeholders=list(TEMPLATE_PLACEHOLDERS))
