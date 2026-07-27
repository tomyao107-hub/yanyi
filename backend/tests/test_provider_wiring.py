"""The translation path must honour model profiles and prompt templates."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session, SQLModel, create_engine

from backend.app.engine.translator import Translator
from backend.app.models import Chapter, Project, PromptTemplate, Segment
from backend.app.providers.base import TranslationResult
from backend.app.security.crypto import MASTER_KEY_BYTES, MASTER_KEY_ENV
from backend.app.services.prompts import resolve_project_prompt, seed_builtin_templates
from backend.app.services.providers import ProviderCredentialService


class RecordingProvider:
    """Captures what the engine actually sent, plus how it was constructed."""

    instances: list[RecordingProvider] = []

    def __init__(self, **kwargs: Any) -> None:
        self.api_key = kwargs.get("api_key")
        self.api_base = kwargs.get("api_base")
        self.options = kwargs
        self.calls: list[dict[str, Any]] = []
        RecordingProvider.instances.append(self)

    async def translate(self, text: str, **kwargs: Any) -> TranslationResult:
        self.calls.append({"text": text, **kwargs})
        return TranslationResult(f"译：{text}", 5, 4, kwargs["model"])


@pytest.fixture
def engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    key_file = tmp_path / "master.key"
    key_file.write_bytes(os.urandom(MASTER_KEY_BYTES))
    monkeypatch.setenv(MASTER_KEY_ENV, str(key_file))
    database = tmp_path / "wiring.db"
    db_engine = create_engine(
        f"sqlite:///{database.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(db_engine)
    RecordingProvider.instances.clear()
    # The engine resolves profiles through the service, so patch construction there.
    monkeypatch.setattr(
        "backend.app.services.providers.LiteLLMProvider",
        RecordingProvider,
    )
    return db_engine


def _project_with_segment(session: Session, **fields: Any) -> int:
    project = Project(
        title="Wiring",
        source_type="md",
        source_path="unused.md",
        provider_cfg={"model": "cfg-model", "max_concurrency": 1},
        **fields,
    )
    session.add(project)
    session.commit()
    session.refresh(project)
    chapter = Chapter(project_id=project.id or 0, ord=0, title="One")
    session.add(chapter)
    session.commit()
    session.refresh(chapter)
    session.add(
        Segment(
            project_id=project.id or 0,
            chapter_id=chapter.id or 0,
            ord=0,
            stable_key="0000:00000:aaaaaaaa",
            struct_path={"source_type": "md"},
            source_text="Hello world.",
            src_hash="a" * 40,
            status="pending",
        )
    )
    session.commit()
    return int(project.id or 0)


@pytest.mark.asyncio
async def test_translation_uses_profile_endpoint_key_and_model(engine: Any) -> None:
    with Session(engine) as session:
        service = ProviderCredentialService(session)
        credential = service.create_credential(
            provider="custom",
            profile_label="Relay",
            secret="sk-live-secret9999",
        )
        profile = service.create_profile(
            display_name="Relay",
            provider="custom",
            litellm_model_id="vendor/exotic-v3",
            credential_id=credential.id,
            base_url="https://relay.example.com/v1",
            max_concurrency=1,
        )
        session.commit()
        project_id = _project_with_segment(session, model_profile_id=profile.id)

    translator = Translator(session_factory=lambda: Session(engine))
    stats = await translator.translate_project(project_id)
    assert stats.done == 1

    provider = RecordingProvider.instances[-1]
    # The endpoint and decrypted key come from the profile, not the environment.
    assert provider.api_base == "https://relay.example.com/v1"
    assert provider.api_key == "sk-live-secret9999"
    # The profile's model ID overrides whatever provider_cfg carried.
    assert provider.calls[0]["model"] == "vendor/exotic-v3"


@pytest.mark.asyncio
async def test_project_without_profile_keeps_ambient_provider(engine: Any) -> None:
    with Session(engine) as session:
        project_id = _project_with_segment(session)

    injected = RecordingProvider()
    translator = Translator(
        session_factory=lambda: Session(engine),
        provider=injected,
    )
    stats = await translator.translate_project(project_id)
    assert stats.done == 1
    # No profile assigned, so the caller's provider and provider_cfg model win.
    assert injected.calls[0]["model"] == "cfg-model"


@pytest.mark.asyncio
async def test_translation_uses_the_selected_prompt_template(engine: Any) -> None:
    with Session(engine) as session:
        template = PromptTemplate(
            name="Terse",
            name_normalized="terse",
            system_prompt="Translate {source_lang} into {target_lang}. Be terse.",
            user_prefix="Keep names unchanged.",
        )
        session.add(template)
        session.commit()
        session.refresh(template)
        project_id = _project_with_segment(session, prompt_template_id=template.id)

    injected = RecordingProvider()
    translator = Translator(
        session_factory=lambda: Session(engine),
        provider=injected,
    )
    await translator.translate_project(project_id)

    sent = injected.calls[0]["system_prompt"]
    assert "Be terse." in sent
    # Placeholders render from the project's language pair.
    assert "英文" in sent and "简体中文" in sent
    assert "{source_lang}" not in sent
    # The template's extra guidance travels with the system prompt.
    assert "Keep names unchanged." in sent


def test_prompt_resolution_falls_back_when_template_is_unusable(engine: Any) -> None:
    with Session(engine) as session:
        seed_builtin_templates(session)
        session.commit()
        default_prompt = resolve_project_prompt(
            session,
            Project(title="t", source_type="md", source_path="x"),
        )
        assert default_prompt.strip()

        disabled = PromptTemplate(
            name="Off",
            name_normalized="off",
            system_prompt="SHOULD NOT BE USED",
            enabled=False,
        )
        session.add(disabled)
        session.commit()
        session.refresh(disabled)
        project = Project(
            title="t",
            source_type="md",
            source_path="x",
            prompt_template_id=disabled.id,
        )
        # A disabled template is skipped in favour of the default one.
        assert "SHOULD NOT BE USED" not in resolve_project_prompt(session, project)

        stale = Project(
            title="t",
            source_type="md",
            source_path="x",
            prompt_template_id=999_999,
        )
        assert resolve_project_prompt(session, stale).strip()
