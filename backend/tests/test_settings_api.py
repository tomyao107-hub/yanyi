"""Provider credential, model profile and prompt template API coverage."""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

from backend.app.api import adapters
from backend.app.config import Settings, get_settings
from backend.app.db import get_session
from backend.app.main import create_app
from backend.app.models import AdminUser, ProviderCredential
from backend.app.security.crypto import MASTER_KEY_BYTES, MASTER_KEY_ENV
from backend.app.security.csrf import CSRF_COOKIE_NAME, CSRF_HEADER_NAME
from backend.app.security.passwords import hash_password
from backend.app.security.sessions import LoginTokenBucket, normalize_username

ADMIN_USERNAME = "settings-admin"
ADMIN_PASSWORD = "correct horse battery staple"


@pytest.fixture
def client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    # An explicit key file keeps the dev fallback from writing into the repo.
    key_file = tmp_path / "master.key"
    key_file.write_bytes(os.urandom(MASTER_KEY_BYTES))
    monkeypatch.setenv(MASTER_KEY_ENV, str(key_file))

    database = tmp_path / "settings.db"
    engine = create_engine(
        f"sqlite:///{database.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            AdminUser(
                username=ADMIN_USERNAME,
                normalized_username=normalize_username(ADMIN_USERNAME),
                password_hash=hash_password(ADMIN_PASSWORD),
            )
        )
        session.commit()

    settings = Settings(
        data_dir=tmp_path / "data",
        upload_dir=tmp_path / "uploads",
        export_dir=tmp_path / "exports",
        database_url=f"sqlite:///{database.as_posix()}",
    )
    settings.ensure_directories()

    def test_session() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = test_session
    app.dependency_overrides[get_settings] = lambda: settings
    monkeypatch.setattr(adapters, "session_factory", lambda: Session(engine))
    monkeypatch.setattr("backend.app.main.migrate_db", lambda: None)
    monkeypatch.setattr("backend.app.main._recover_interrupted_work", lambda: None)
    monkeypatch.setattr("backend.app.main.initialize_admin", lambda session: False)
    # Startup seeds the built-in prompt templates; point it at this test's
    # database so the templates land where the request sessions can see them.
    monkeypatch.setattr("backend.app.main.session_factory", lambda: Session(engine))
    monkeypatch.setattr(
        "backend.app.security.sessions.login_token_bucket", LoginTokenBucket()
    )
    # The durable job worker is process-global; point it at this database.
    from backend.app.jobs.manager import job_manager

    job_manager._session = lambda: Session(engine)
    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/auth/login",
            json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        )
        assert response.status_code == 200, response.text
        test_client.headers[CSRF_HEADER_NAME] = test_client.cookies.get(CSRF_COOKIE_NAME)
        test_client.state_engine = engine  # type: ignore[attr-defined]
        yield test_client


def _create_credential(
    client: TestClient,
    *,
    provider: str = "custom",
    label: str = "Relay",
    api_key: str = "sk-live-abcd1234WXYZ",
) -> dict[str, object]:
    response = client.post(
        "/api/settings/credentials",
        json={"provider": provider, "profile_label": label, "api_key": api_key},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_public_settings_advertises_providers_and_placeholders(client: TestClient) -> None:
    payload = client.get("/api/settings").json()
    names = [option["name"] for option in payload["providers"]]
    assert "custom" in names and "openai" in names
    # A custom relay has no fixed endpoint, so the UI must require a base URL.
    custom = next(option for option in payload["providers"] if option["name"] == "custom")
    assert custom["requires_base_url"] is True
    assert "source_lang" in payload["prompt_placeholders"]
    assert "temperature" in payload["generation_param_keys"]
    # An explicit key file is configured by the fixture.
    assert payload["credential_key_is_ephemeral"] is False


def test_credential_secret_is_encrypted_and_never_returned(client: TestClient) -> None:
    secret = "sk-live-abcd1234WXYZ"
    created = _create_credential(client, api_key=secret)
    assert created["masked_key"].endswith("WXYZ")
    assert created["configured"] is True
    body = client.get("/api/settings/credentials").text
    assert secret not in body
    assert "sk-live" not in body

    # The stored ciphertext must not contain the plaintext either.
    engine = client.state_engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        row = session.exec(select(ProviderCredential)).one()
        assert secret.encode() not in row.encrypted_ciphertext
        assert row.masked_suffix == "WXYZ"
        assert len(row.encryption_nonce) == 12


def test_credential_rotation_resets_test_state(client: TestClient) -> None:
    created = _create_credential(client)
    rotated = client.put(
        f"/api/settings/credentials/{created['id']}",
        json={"api_key": "sk-live-rotated5678"},
    )
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["masked_key"].endswith("5678")
    assert rotated.json()["test_status"] == "untested"


def _create_profile(
    client: TestClient,
    *,
    name: str,
    provider: str,
    model: str,
    credential_id: int | None = None,
    **extra: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "display_name": name,
        "provider": provider,
        "litellm_model_id": model,
        **extra,
    }
    if credential_id is not None:
        payload["credential_id"] = credential_id
    response = client.post("/api/settings/model-profiles", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_multiple_providers_coexist_with_independent_credentials(
    client: TestClient,
) -> None:
    openai_key = _create_credential(client, provider="openai", label="Prod")
    deepseek_key = _create_credential(client, provider="deepseek", label="Cheap")
    relay_key = _create_credential(client, provider="custom", label="Relay")

    _create_profile(
        client,
        name="OpenAI",
        provider="openai",
        model="gpt-5-mini",
        credential_id=int(openai_key["id"]),
    )
    _create_profile(
        client,
        name="DeepSeek",
        provider="deepseek",
        model="deepseek-v4-flash",
        credential_id=int(deepseek_key["id"]),
    )
    _create_profile(
        client,
        name="Relay",
        provider="custom",
        model="gpt-4o-mini",
        credential_id=int(relay_key["id"]),
        base_url="https://relay.example.com/v1",
    )

    profiles = client.get("/api/settings/model-profiles").json()
    assert len(profiles) == 3
    models = {profile["litellm_model_id"] for profile in profiles}
    # LiteLLM routes on a prefix, so providers that need one get it applied.
    assert models == {"gpt-5-mini", "deepseek/deepseek-v4-flash", "gpt-4o-mini"}
    relay = next(profile for profile in profiles if profile["display_name"] == "Relay")
    assert relay["base_url"] == "https://relay.example.com/v1"


def test_custom_endpoint_accepts_third_party_host_and_flags_plaintext(
    client: TestClient,
) -> None:
    profile = _create_profile(
        client,
        name="HTTPS relay",
        provider="custom",
        model="some-vendor/exotic-model-v3",
        base_url="https://gateway.internal.example:8443/v1",
    )
    assert profile["base_url"] == "https://gateway.internal.example:8443/v1"
    assert profile["insecure_transport"] is False

    plaintext = _create_profile(
        client,
        name="Plaintext relay",
        provider="custom",
        model="gpt-4o",
        base_url="http://gateway.example.com/v1",
    )
    # http to a non-local host would send the key unencrypted; the UI warns.
    assert plaintext["insecure_transport"] is True

    local = _create_profile(
        client,
        name="Local Ollama",
        provider="ollama",
        model="qwen3",
        base_url="http://127.0.0.1:11434",
    )
    assert local["litellm_model_id"] == "ollama/qwen3"
    assert local["insecure_transport"] is False


def test_endpoint_rejects_embedded_credentials(client: TestClient) -> None:
    response = client.post(
        "/api/settings/model-profiles",
        json={
            "display_name": "Leaky",
            "provider": "custom",
            "litellm_model_id": "gpt-4o",
            "base_url": "https://user:pass@relay.example.com/v1",
        },
    )
    assert response.status_code == 422
    assert "credentials" in response.json()["detail"]


def test_generation_params_reject_secret_like_keys(client: TestClient) -> None:
    response = client.post(
        "/api/settings/model-profiles",
        json={
            "display_name": "Sneaky",
            "provider": "custom",
            "litellm_model_id": "gpt-4o",
            "base_url": "https://relay.example.com/v1",
            "generation_params": {"api_key": "sk-leak"},
        },
    )
    assert response.status_code == 422
    assert "api_key" in response.json()["detail"]


def test_credential_provider_must_match_profile(client: TestClient) -> None:
    credential = _create_credential(client, provider="openai", label="Prod")
    response = client.post(
        "/api/settings/model-profiles",
        json={
            "display_name": "Mismatched",
            "provider": "anthropic",
            "litellm_model_id": "claude-sonnet-5",
            "credential_id": credential["id"],
        },
    )
    assert response.status_code == 422
    assert "does not match" in response.json()["detail"]


def test_credential_in_use_cannot_be_deleted(client: TestClient) -> None:
    credential = _create_credential(client, provider="openai", label="Prod")
    _create_profile(
        client,
        name="Uses it",
        provider="openai",
        model="gpt-5-mini",
        credential_id=int(credential["id"]),
    )
    response = client.delete(f"/api/settings/credentials/{credential['id']}")
    assert response.status_code == 409


def _profiles_by_name(client: TestClient) -> dict[str, dict[str, object]]:
    return {
        item["display_name"]: item
        for item in client.get("/api/settings/model-profiles").json()
    }


def test_only_one_profile_is_default(client: TestClient) -> None:
    _create_profile(
        client,
        name="First",
        provider="custom",
        model="a",
        base_url="https://a.example.com",
        is_default=True,
    )
    _create_profile(
        client,
        name="Second",
        provider="custom",
        model="b",
        base_url="https://b.example.com",
        is_default=True,
    )
    profiles = _profiles_by_name(client)
    assert profiles["Second"]["is_default"] is True
    assert profiles["First"]["is_default"] is False

    first_id = profiles["First"]["id"]
    assert client.post(f"/api/settings/model-profiles/{first_id}/default").status_code == 200
    profiles = _profiles_by_name(client)
    assert profiles["First"]["is_default"] is True
    assert profiles["Second"]["is_default"] is False
    # The default profile is the translation fallback, so it must not vanish.
    assert client.delete(f"/api/settings/model-profiles/{first_id}").status_code == 409


def test_settings_endpoints_require_authentication(client: TestClient) -> None:
    anonymous = TestClient(client.app, base_url=str(client.base_url))
    for path in (
        "/api/settings/credentials",
        "/api/settings/model-profiles",
        "/api/settings/prompt-templates",
    ):
        assert anonymous.get(path).status_code == 401, path


def test_credential_listings_are_not_cacheable(client: TestClient) -> None:
    response = client.get("/api/settings/credentials")
    assert response.headers["cache-control"] == "no-store"


def test_builtin_prompt_templates_are_seeded_with_one_default(
    client: TestClient,
) -> None:
    templates = client.get("/api/settings/prompt-templates").json()
    assert len(templates) == 3
    assert [item["is_default"] for item in templates].count(True) == 1
    assert all(item["is_builtin"] for item in templates)


def test_prompt_template_crud_and_placeholder_rendering(client: TestClient) -> None:
    created = client.post(
        "/api/settings/prompt-templates",
        json={
            "name": "法律文本",
            "description": "条款翻译",
            "system_prompt": "把{source_lang}译为{target_lang}，术语从严。",
            "user_prefix": "保留条款编号。",
        },
    )
    assert created.status_code == 201, created.text
    template_id = created.json()["id"]

    # Rendering substitutes language names, not raw codes.
    preview = client.post(
        "/api/settings/prompt-templates/preview",
        json={"template_id": template_id, "source_lang": "en", "target_lang": "zh-CN"},
    )
    assert preview.status_code == 200, preview.text
    rendered = preview.json()["rendered"]
    assert "英文" in rendered and "简体中文" in rendered
    assert "{source_lang}" not in rendered
    # The extra guidance is folded into the same system prompt.
    assert "保留条款编号。" in rendered

    updated = client.patch(
        f"/api/settings/prompt-templates/{template_id}",
        json={"system_prompt": "只输出{target_lang}译文。"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["system_prompt"] == "只输出{target_lang}译文。"

    assert client.delete(f"/api/settings/prompt-templates/{template_id}").status_code == 204


def test_prompt_preview_renders_unsaved_draft(client: TestClient) -> None:
    response = client.post(
        "/api/settings/prompt-templates/preview",
        json={"system_prompt": "翻译成{target_lang}。", "target_lang": "ja"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["rendered"] == "翻译成日文。"


def test_duplicate_template_name_is_rejected(client: TestClient) -> None:
    payload = {"name": "重复", "system_prompt": "x"}
    assert client.post("/api/settings/prompt-templates", json=payload).status_code == 201
    assert client.post("/api/settings/prompt-templates", json=payload).status_code == 422


def test_default_prompt_template_cannot_be_deleted(client: TestClient) -> None:
    templates = client.get("/api/settings/prompt-templates").json()
    default = next(item for item in templates if item["is_default"])
    assert client.delete(f"/api/settings/prompt-templates/{default['id']}").status_code == 409
