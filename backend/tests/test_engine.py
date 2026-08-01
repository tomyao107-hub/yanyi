from __future__ import annotations

import asyncio
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session

from backend.app.db import SCHEMA_HEAD_REVISION, create_db_engine, init_db
from backend.app.engine import (
    InMemoryTranslationMemory,
    RateLimitCoordinator,
    RetryPolicy,
    Translator,
    translate_records,
    translate_with_retry,
)
from backend.app.models import Project, Segment
from backend.app.parsers.base import Block, Chapter, DocModel
from backend.app.providers import TranslationResult
from backend.app.providers.litellm_provider import LiteLLMProvider
from backend.app.segment import persist_document, source_hash
from backend.cli import translate_file


@dataclass
class Record:
    ord: int
    source_text: str
    src_hash: str
    struct_path: dict[str, Any]
    status: str = "pending"
    target_text: str | None = None
    error_msg: str | None = None
    token_in: int | None = None
    token_out: int | None = None
    provider: str | None = None


class MockProvider:
    def __init__(self, *, fail_once: bool = False) -> None:
        self.calls: list[str] = []
        self.active = 0
        self.max_active = 0
        self.fail_once = fail_once
        self.summary_calls = 0

    async def translate(self, text: str, **kwargs: Any) -> TranslationResult:
        self.calls.append(text)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.005)
        self.active -= 1
        if "书籍编辑" in kwargs["system_prompt"]:
            self.summary_calls += 1
            return TranslationResult("缓存摘要", 3, 2, kwargs["model"])
        if self.fail_once:
            self.fail_once = False
            error = RuntimeError("limited")
            error.status_code = 429  # type: ignore[attr-defined]
            raise error
        return TranslationResult(f"译：{text}", 5, 4, kwargs["model"])


class StreamingProvider(MockProvider):
    async def stream_translate(self, text: str, **kwargs: Any):  # type: ignore[no-untyped-def]
        del kwargs
        for chunk in ("流", f"式：{text}"):
            await asyncio.sleep(0)
            yield chunk


class InlineSlotProvider(MockProvider):
    async def translate(self, text: str, **kwargs: Any) -> TranslationResult:
        self.calls.append(text)
        translated = (
            text.replace("Hello ", "你好，")
            .replace("world", "世界")
            .replace(" now.", "现在。")
        )
        return TranslationResult(translated, 5, 4, kwargs["model"])


@pytest.mark.asyncio
async def test_concurrency_tm_resume_and_markdown_protection() -> None:
    provider = MockProvider()
    shared_hash = source_hash("Same")
    records = [
        Record(0, "Same", shared_hash, {"source_type": "md"}),
        Record(1, "Same", shared_hash, {"source_type": "md"}),
        Record(
            2,
            "Visit [site](https://example.com) and `x = 1`, {name}.",
            source_hash("unique"),
            {"source_type": "md"},
        ),
        Record(3, "Already", source_hash("done"), {}, status="done", target_text="完成"),
    ]
    stats = await translate_records(
        records,
        provider,
        model="mock",
        max_concurrency=2,
        translation_memory=InMemoryTranslationMemory(),
    )
    assert stats.done == 3
    assert stats.tm_hits == 1
    assert len(provider.calls) == 2
    assert provider.max_active <= 2
    assert records[2].target_text is not None
    assert "https://example.com" in records[2].target_text
    assert "`x = 1`" in records[2].target_text
    assert "{name}" in records[2].target_text
    assert "⟦" not in records[2].target_text


@pytest.mark.asyncio
async def test_epub_inline_code_is_never_sent_or_stored() -> None:
    provider = InlineSlotProvider()
    record = Record(
        0,
        "Hello world ⟦EPUBCODE0000⟧ now.",
        source_hash("epub-code"),
        {
            "source_type": "epub",
            "inline_code": {"⟦EPUBCODE0000⟧": "x=1"},
            "inline_slots": [
                {"start": 0, "end": 6},
                {"start": 6, "end": 12},
                {"start": 26, "end": 31},
            ],
        },
    )
    await translate_records([record], provider, model="mock")
    assert "x=1" not in provider.calls[0]
    assert "EPUBCODE" not in provider.calls[0]
    assert "VAR0000" in provider.calls[0]
    assert record.target_text is not None
    assert "EPUBCODE" not in record.target_text
    assert "VAR0000" not in record.target_text
    assert record.target_text == "你好，世界 现在。"
    assert record.struct_path["target_slots"] == ["你好，", "世界 ", "现在。"]


@pytest.mark.asyncio
async def test_retry_and_stream_events() -> None:
    retrying = MockProvider(fail_once=True)
    result = await translate_with_retry(
        retrying,
        "hello",
        system_prompt="translate",
        context="",
        model="mock",
        temperature=0.0,
        policy=RetryPolicy(max_attempts=2, base_delay=0, jitter=0),
    )
    assert result.text == "译：hello"
    assert len(retrying.calls) == 2

    events: list[dict[str, Any]] = []
    record = Record(0, "hello", source_hash("hello"), {})
    await translate_records(
        [record],
        StreamingProvider(),
        model="mock",
        stream=True,
        event_callback=events.append,
    )
    assert record.target_text == "流式：hello"
    assert [event["delta"] for event in events if event["type"] == "segment_delta"] == [
        "流",
        "式：hello",
    ]


@pytest.mark.asyncio
async def test_rate_limit_cooldown_is_shared() -> None:
    coordinator = RateLimitCoordinator()
    await coordinator.penalize(0.02)
    started = time.monotonic()
    await asyncio.gather(coordinator.wait(), coordinator.wait())
    assert time.monotonic() - started >= 0.015


@pytest.mark.asyncio
async def test_gemini_flash_omits_temperature(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_completion(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "model": kwargs["model"],
            "choices": [{"message": {"content": "译文"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }

    provider = LiteLLMProvider(temperature=1.0, top_p=0.9, top_k=40)
    monkeypatch.setattr(provider, "_acompletion", lambda: fake_completion)
    await provider.translate(
        "text",
        system_prompt="system",
        context="",
        model="gemini/gemini-3.6-flash",
        temperature=0.8,
    )
    assert "temperature" not in captured
    assert "top_p" not in captured
    assert "top_k" not in captured


@pytest.mark.asyncio
async def test_database_resume_and_chapter_summary_cache(tmp_path: Path) -> None:
    engine = create_db_engine(f"sqlite:///{(tmp_path / 'engine.db').as_posix()}")
    init_db(engine)
    with Session(engine) as session:
        project = Project(
            title="Book",
            source_type="md",
            source_path=str(tmp_path / "book.md"),
            provider_cfg={
                "model": "mock",
                "max_concurrency": 2,
                "generate_chapter_summaries": True,
            },
        )
        session.add(project)
        session.commit()
        session.refresh(project)
        assert project.id is not None
        persist_document(
            session,
            project.id,
            DocModel(
                source_type="md",
                chapters=[
                    Chapter(
                        0,
                        "One",
                        "one",
                        [
                            Block(
                                "para",
                                "Hello world.",
                                {"source_type": "md", "token_index": 1},
                            )
                        ],
                    )
                ],
            ),
        )
        project_id = project.id

    provider = MockProvider()
    translator = Translator(
        session_factory=lambda: Session(engine),
        provider=provider,
        retry_policy=RetryPolicy(base_delay=0, jitter=0),
    )
    first = await translator.translate_project(project_id)
    assert first.done == 1
    assert provider.summary_calls == 1
    second = await translator.translate_project(project_id)
    assert second.total == 0
    assert provider.summary_calls == 1
    with Session(engine) as session:
        row = session.exec(
            __import__("sqlmodel").select(Segment).where(Segment.project_id == project_id)
        ).one()
        assert row.status == "done"


@pytest.mark.asyncio
async def test_chapter_summaries_do_not_block_first_segment_writeback(
    tmp_path: Path,
) -> None:
    """A whole-book run translates chapter one while chapter two is summarized."""

    translation_started = asyncio.Event()

    class PipelineProvider:
        def __init__(self) -> None:
            self.summary_calls = 0

        async def translate(self, text: str, **kwargs: Any) -> TranslationResult:
            if "书籍编辑" in kwargs["system_prompt"]:
                self.summary_calls += 1
                if self.summary_calls == 2:
                    await asyncio.wait_for(translation_started.wait(), timeout=0.5)
                return TranslationResult("章节摘要", 3, 2, kwargs["model"])
            translation_started.set()
            return TranslationResult(f"译：{text}", 5, 4, kwargs["model"])

    engine = create_db_engine(f"sqlite:///{(tmp_path / 'pipeline.db').as_posix()}")
    init_db(engine)
    with Session(engine) as session:
        project = Project(
            title="Pipeline",
            source_type="md",
            source_path=str(tmp_path / "pipeline.md"),
            provider_cfg={
                "model": "mock",
                "max_concurrency": 1,
                "generate_chapter_summaries": True,
            },
        )
        session.add(project)
        session.commit()
        session.refresh(project)
        assert project.id is not None
        persist_document(
            session,
            project.id,
            DocModel(
                source_type="md",
                chapters=[
                    Chapter(0, "One", "one", [Block("para", "Alpha.", {})]),
                    Chapter(1, "Two", "two", [Block("para", "Beta.", {})]),
                ],
            ),
        )
        project_id = project.id

    provider = PipelineProvider()
    stats = await Translator(
        session_factory=lambda: Session(engine),
        provider=provider,
        retry_policy=RetryPolicy(base_delay=0, jitter=0),
    ).translate_project(project_id)
    assert stats.done == 2
    assert provider.summary_calls == 2
    assert translation_started.is_set()


@pytest.mark.asyncio
async def test_cli_pipeline_and_resume(tmp_path: Path) -> None:
    source = tmp_path / "book.md"
    source.write_text("# One\n\nHello world.\n", encoding="utf-8")
    output = tmp_path / "translated.md"
    database = tmp_path / "cli.db"
    first_provider = MockProvider()
    exported, first = await translate_file(
        source,
        model="mock",
        output_path=output,
        database_url=str(database),
        provider=first_provider,
    )
    assert exported == output.resolve()
    assert first.done == 2
    assert "译：Hello world." in output.read_text(encoding="utf-8")

    second_provider = MockProvider()
    _, second = await translate_file(
        source,
        model="mock",
        output_path=output,
        database_url=str(database),
        provider=second_provider,
    )
    assert second.total == 0
    assert second_provider.calls == []

    source.write_text("# One\n\nHello world.\n\nA new paragraph.\n", encoding="utf-8")
    third_provider = MockProvider()
    _, third = await translate_file(
        source,
        model="mock",
        output_path=output,
        database_url=str(database),
        provider=third_provider,
    )
    assert third.total == 1
    assert third.done == 1
    assert third_provider.calls == ["A new paragraph."]
    assert "译：A new paragraph." in output.read_text(encoding="utf-8")

    connection = sqlite3.connect(database)
    try:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    finally:
        connection.close()
    assert revision == (SCHEMA_HEAD_REVISION,)
    database.unlink()
    assert not database.exists()
