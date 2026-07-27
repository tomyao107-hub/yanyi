from __future__ import annotations

import asyncio
import inspect
import random
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from sqlalchemy import update
from sqlmodel import Session, select

from ..db import session_factory as default_session_factory
from ..models import Chapter, GlossaryTerm, Project, Segment, utc_now
from ..providers import LiteLLMProvider, TranslationProvider, TranslationResult
from ..providers.litellm_provider import estimate_tokens
from .context import ContextBuilder
from .prompt import build_system_prompt
from .protection import protect_for_segment, restore_for_segment
from .tm import InMemoryTranslationMemory, TranslationMemory, TranslationMemoryProtocol

EventCallback = Callable[[dict[str, Any]], Awaitable[None] | None]

# The local service is intentionally single-instance. Project configuration may
# request fewer workers, but never more than this server-controlled ceiling.
MAX_TRANSLATION_CONCURRENCY = 8
SAFE_PROVIDER_ERROR = "Translation provider request failed"
SAFE_EMPTY_RESULT_ERROR = "Translation provider returned an empty result"


@dataclass(slots=True)
class RetryPolicy:
    max_attempts: int = 5
    base_delay: float = 1.0
    max_delay: float = 8.0
    jitter: float = 0.25
    timeout: float = 60.0


@dataclass(slots=True)
class TranslationStats:
    total: int = 0
    done: int = 0
    errors: int = 0
    tm_hits: int = 0
    token_in: int = 0
    token_out: int = 0
    stopped: bool = False


class RateLimitCoordinator:
    """Share a provider cooldown across all concurrent workers."""

    def __init__(self) -> None:
        self._cooldown_until = 0.0
        self._lock = asyncio.Lock()

    async def penalize(self, delay: float) -> None:
        async with self._lock:
            self._cooldown_until = max(self._cooldown_until, time.monotonic() + delay)

    async def wait(
        self,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        while True:
            async with self._lock:
                remaining = self._cooldown_until - time.monotonic()
            if remaining <= 0:
                return
            await sleep(remaining)


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, ConnectionError)):
        return True
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    if status is not None:
        try:
            code = int(status)
            return code == 429 or 500 <= code < 600
        except (TypeError, ValueError):
            pass
    name = type(exc).__name__.lower()
    return any(
        word in name
        for word in ("ratelimit", "timeout", "connection", "serviceunavailable")
    )


def _is_rate_limit(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    try:
        return int(status) == 429
    except (TypeError, ValueError):
        return "ratelimit" in type(exc).__name__.lower()


async def _emit(callback: EventCallback | None, event: dict[str, Any]) -> None:
    if callback is None:
        return
    result = callback(event)
    if inspect.isawaitable(result):
        await result


def _stop_requested(stop_event: Any | None) -> bool:
    return bool(stop_event is not None and stop_event.is_set())


async def translate_with_retry(
    provider: TranslationProvider,
    text: str,
    *,
    system_prompt: str,
    context: str,
    model: str,
    temperature: float,
    policy: RetryPolicy | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rate_limit: RateLimitCoordinator | None = None,
) -> TranslationResult:
    retry = policy or RetryPolicy()
    if retry.max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    last_error: BaseException | None = None
    for attempt in range(1, retry.max_attempts + 1):
        try:
            if rate_limit is not None:
                await rate_limit.wait(sleep)
            return await asyncio.wait_for(
                provider.translate(
                    text,
                    system_prompt=system_prompt,
                    context=context,
                    model=model,
                    temperature=temperature,
                ),
                timeout=retry.timeout,
            )
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            last_error = exc
            if attempt >= retry.max_attempts or not _is_retryable(exc):
                raise
            delay = min(retry.max_delay, retry.base_delay * (2 ** (attempt - 1)))
            if retry.jitter:
                delay += random.uniform(0, retry.jitter * delay)
            if rate_limit is not None and _is_rate_limit(exc):
                await rate_limit.penalize(delay)
                await rate_limit.wait(sleep)
            else:
                await sleep(delay)
    assert last_error is not None
    raise last_error


async def stream_with_retry(
    provider: Any,
    text: str,
    *,
    system_prompt: str,
    context: str,
    model: str,
    temperature: float,
    on_delta: Callable[[str], Awaitable[None]],
    on_reset: Callable[[], Awaitable[None]] | None = None,
    policy: RetryPolicy | None = None,
    rate_limit: RateLimitCoordinator | None = None,
) -> TranslationResult:
    retry = policy or RetryPolicy()
    if not callable(getattr(provider, "stream_translate", None)):
        return await translate_with_retry(
            provider,
            text,
            system_prompt=system_prompt,
            context=context,
            model=model,
            temperature=temperature,
            policy=retry,
            rate_limit=rate_limit,
        )
    for attempt in range(1, retry.max_attempts + 1):
        chunks: list[str] = []
        try:
            if rate_limit is not None:
                await rate_limit.wait()
            async with asyncio.timeout(retry.timeout):
                async for chunk in provider.stream_translate(
                    text,
                    system_prompt=system_prompt,
                    context=context,
                    model=model,
                    temperature=temperature,
                ):
                    if chunk:
                        chunks.append(chunk)
                        await on_delta(chunk)
            output = "".join(chunks)
            return TranslationResult(
                text=output,
                token_in=estimate_tokens(system_prompt + context + text, model),
                token_out=estimate_tokens(output, model),
                model=model,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if attempt >= retry.max_attempts or not _is_retryable(exc):
                raise
            if chunks and on_reset is not None:
                await on_reset()
            delay = min(retry.max_delay, retry.base_delay * (2 ** (attempt - 1)))
            if retry.jitter:
                delay += random.uniform(0, retry.jitter * delay)
            if rate_limit is not None and _is_rate_limit(exc):
                await rate_limit.penalize(delay)
                await rate_limit.wait()
            else:
                await asyncio.sleep(delay)
    raise RuntimeError("stream retry loop exhausted")


async def translate_records(
    records: Sequence[Any],
    provider: TranslationProvider,
    *,
    model: str,
    source_lang: str = "en",
    target_lang: str = "zh-CN",
    temperature: float = 0.3,
    max_concurrency: int = 4,
    retry_policy: RetryPolicy | None = None,
    translation_memory: TranslationMemoryProtocol | None = None,
    glossary: Sequence[Any] = (),
    context_builder: ContextBuilder | None = None,
    event_callback: EventCallback | None = None,
    stop_event: Any | None = None,
    force: bool = False,
    stream: bool = False,
    retry_errors: bool = True,
) -> TranslationStats:
    """Translate mutable record-like objects; useful for tests and non-DB callers."""

    if max_concurrency < 1:
        raise ValueError("max_concurrency must be at least 1")
    candidates = [
        record
        for record in records
        if force
        or getattr(record, "status", "pending")
        in ({"pending", "error"} if retry_errors else {"pending"})
    ]
    stats = TranslationStats(total=len(candidates))
    semaphore = asyncio.Semaphore(max_concurrency)
    tm = translation_memory or InMemoryTranslationMemory()
    context_factory = context_builder or ContextBuilder()
    hash_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
    rate_limit = RateLimitCoordinator()
    system_prompt = build_system_prompt(source_lang, target_lang)

    async def worker(record: Any) -> None:
        if _stop_requested(stop_event):
            stats.stopped = True
            return
        async with semaphore:
            if _stop_requested(stop_event):
                stats.stopped = True
                return
            src_hash = str(record.src_hash)
            async with hash_locks[src_hash]:
                if not force:
                    cached = tm.lookup(src_hash)
                    if cached is not None:
                        record.target_text = cached
                        record.status = "done"
                        record.error_msg = None
                        stats.done += 1
                        stats.tm_hits += 1
                        await _emit(
                            event_callback,
                            {"type": "segment_done", "segment": record, "tm_hit": True},
                        )
                        return
                record.status = "processing"
                record.error_msg = None
                try:
                    context = context_factory.build(
                        record,
                        records,
                        glossary=glossary,
                        model=model,
                    )
                    record_path = dict(getattr(record, "struct_path", {}) or {})
                    protected = protect_for_segment(str(record.source_text), record_path)
                    if stream:

                        async def on_delta(delta: str) -> None:
                            if not protected.replacements:
                                await _emit(
                                    event_callback,
                                    {
                                        "type": "segment_delta",
                                        "segment": record,
                                        "delta": delta,
                                    },
                                )

                        async def on_reset() -> None:
                            await _emit(
                                event_callback,
                                {"type": "segment_reset", "segment": record},
                            )

                        result = await stream_with_retry(
                            provider,
                            protected.text,
                            system_prompt=system_prompt,
                            context=context,
                            model=model,
                            temperature=temperature,
                            on_delta=on_delta,
                            on_reset=on_reset,
                            policy=retry_policy,
                            rate_limit=rate_limit,
                        )
                    else:
                        result = await translate_with_retry(
                            provider,
                            protected.text,
                            system_prompt=system_prompt,
                            context=context,
                            model=model,
                            temperature=temperature,
                            policy=retry_policy,
                            rate_limit=rate_limit,
                        )
                    if not result.text.strip():
                        raise ValueError("Provider returned an empty translation")
                    record.target_text = restore_for_segment(
                        protected,
                        result.text.strip(),
                        record_path,
                    )
                    record.struct_path = record_path
                    if stream and protected.replacements:
                        await _emit(
                            event_callback,
                            {
                                "type": "segment_delta",
                                "segment": record,
                                "delta": record.target_text,
                            },
                        )
                    record.status = "done"
                    record.error_msg = None
                    record.token_in = result.token_in
                    record.token_out = result.token_out
                    record.provider = result.model or model
                    tm.store(src_hash, str(record.source_text), record.target_text)
                    stats.done += 1
                    stats.token_in += result.token_in
                    stats.token_out += result.token_out
                    await _emit(
                        event_callback,
                        {"type": "segment_done", "segment": record, "tm_hit": False},
                    )
                except asyncio.CancelledError:
                    record.status = "pending"
                    raise
                except Exception as exc:
                    record.status = "error"
                    record.error_msg = str(exc)
                    stats.errors += 1
                    await _emit(
                        event_callback,
                        {"type": "error", "segment": record, "error": str(exc)},
                    )

    tasks = [asyncio.create_task(worker(record)) for record in candidates]
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    return stats


class Translator:
    """Database-backed translation orchestrator shared by the API and CLI."""

    def __init__(
        self,
        session_factory: Callable[[], Session] | Session | None = None,
        provider: TranslationProvider | None = None,
        *,
        retry_policy: RetryPolicy | None = None,
        context_builder: ContextBuilder | None = None,
    ) -> None:
        if isinstance(session_factory, Session):
            bind = session_factory.get_bind()
            self._session_source: Callable[[], Session] = lambda: Session(bind)
        else:
            self._session_source = session_factory or default_session_factory
        self.provider = provider or LiteLLMProvider()
        self.retry_policy = retry_policy or RetryPolicy()
        self.context_builder = context_builder or ContextBuilder()

    @contextmanager
    def _session(self) -> Any:
        session = self._session_source()
        try:
            yield session
        finally:
            session.close()

    async def translate_project(
        self,
        project_id: int,
        stop_event: Any | None = None,
        event_callback: EventCallback | None = None,
        retry_errors: bool = True,
        *,
        segment_ids: Sequence[int] | None = None,
        force: bool = False,
    ) -> TranslationStats:
        """Translate a project using a fixed queue of IDs and short DB sessions."""

        with self._session() as session:
            project = session.get(Project, project_id)
            if project is None:
                raise LookupError(f"Project {project_id} does not exist")
            cfg = dict(project.provider_cfg or {})
            model = str(cfg.get("model") or "gpt-5-mini")
            temperature = float(cfg.get("temperature", 0.3))
            max_concurrency = min(
                MAX_TRANSLATION_CONCURRENCY,
                max(1, int(cfg.get("max_concurrency", 4))),
            )
            # Imported here: services.prompts reads engine.prompt, so a
            # module-level import would close an import cycle.
            from ..services.prompts import resolve_project_prompt
            from ..services.providers import resolve_project_runtime

            # A configured model profile owns the endpoint, credential and model
            # ID. Without one, fall back to the ambient-environment provider so
            # existing deployments keep working unchanged.
            runtime = resolve_project_runtime(session, project)
            if runtime is not None:
                model = runtime.model
                max_concurrency = min(MAX_TRANSLATION_CONCURRENCY, runtime.max_concurrency)
            active_provider = runtime.provider if runtime is not None else self.provider
            system_prompt = resolve_project_prompt(session, project)
            context_tokens = max(
                128,
                int(cfg.get("context_token_budget", cfg.get("context_tokens", 1200))),
            )
            previous_count = max(
                0,
                int(cfg.get("previous_segments", cfg.get("context_segments", 3))),
            )
            stream = bool(cfg.get("stream", False))
            generate_summaries = bool(cfg.get("generate_chapter_summaries", False))
            source_lang = project.source_lang
            target_lang = project.target_lang

            stale = list(
                session.exec(
                    select(Segment).where(
                        Segment.project_id == project_id,
                        Segment.status == "processing",
                    )
                ).all()
            )
            for segment in stale:
                segment.status = "pending"
                segment.error_msg = None
                segment.updated_at = utc_now()
                session.add(segment)

            eligible = {"pending", "error"} if retry_errors else {"pending"}
            claimable = eligible if not force else {
                "pending",
                "error",
                "done",
                "reviewed",
            }
            statement = select(Segment.id).where(
                Segment.project_id == project_id,
                Segment.status.in_(claimable),
            )
            selected_ids = None if segment_ids is None else {int(value) for value in segment_ids}
            if selected_ids is not None:
                if selected_ids:
                    statement = statement.where(Segment.id.in_(selected_ids))
                else:
                    statement = statement.where(Segment.id.in_([-1]))
            candidate_ids = [
                int(segment_id)
                for segment_id in session.exec(
                    statement.order_by(Segment.ord, Segment.id)
                ).all()
            ]
            project.status = "translating"
            project.updated_at = utc_now()
            session.add(project)
            session.commit()

        stats = TranslationStats(total=len(candidate_ids))
        stats_lock = asyncio.Lock()
        hash_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        rate_limit = RateLimitCoordinator()
        context_builder = ContextBuilder(
            max_tokens=context_tokens,
            previous_count=previous_count,
        )

        async def update_stats(
            *,
            done: int = 0,
            errors: int = 0,
            tm_hits: int = 0,
            token_in: int = 0,
            token_out: int = 0,
            stopped: bool = False,
        ) -> dict[str, int]:
            async with stats_lock:
                stats.done += done
                stats.errors += errors
                stats.tm_hits += tm_hits
                stats.token_in += token_in
                stats.token_out += token_out
                stats.stopped = stats.stopped or stopped
                return {"done": stats.done, "total": stats.total, "errors": stats.errors}

        await _emit(
            event_callback,
            {
                "type": "progress",
                "project_id": project_id,
                **(await update_stats()),
            },
        )

        if generate_summaries and candidate_ids:
            with self._session() as session:
                chapter_ids = list(
                    session.exec(
                        select(Segment.chapter_id)
                        .where(Segment.id.in_(candidate_ids))
                        .distinct()
                    ).all()
                )
            for chapter_id in chapter_ids:
                if _stop_requested(stop_event):
                    await update_stats(stopped=True)
                    break
                with self._session() as session:
                    chapter = session.get(Chapter, chapter_id)
                    if chapter is None or chapter.summary:
                        continue
                    source = "\n\n".join(
                        session.exec(
                            select(Segment.source_text)
                            .where(Segment.chapter_id == chapter_id)
                            .order_by(Segment.ord)
                        ).all()
                    )
                source = source[: int(cfg.get("summary_max_chars", 8000))]
                if not source.strip():
                    continue
                try:
                    result = await translate_with_retry(
                        active_provider,
                        source,
                        system_prompt=(
                            "你是一名书籍编辑。请用简体中文概括下面章节的关键人物、"
                            "事件、论点和语气，控制在 120 字以内。只输出摘要。"
                        ),
                        context="",
                        model=model,
                        temperature=min(temperature, 0.3),
                        policy=self.retry_policy,
                        rate_limit=rate_limit,
                    )
                    summary = result.text.strip()
                    if not summary:
                        raise ValueError(SAFE_EMPTY_RESULT_ERROR)
                    with self._session() as session:
                        chapter = session.get(Chapter, chapter_id)
                        if chapter is not None and not chapter.summary:
                            chapter.summary = summary
                            session.add(chapter)
                            session.commit()
                    await _emit(
                        event_callback,
                        {
                            "type": "chapter_summary",
                            "project_id": project_id,
                            "chapter_id": chapter_id,
                            "summary": summary,
                        },
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    await _emit(
                        event_callback,
                        {
                            "type": "error",
                            "project_id": project_id,
                            "chapter_id": chapter_id,
                            "stage": "chapter_summary",
                            "error_code": "chapter_summary_failed",
                            "error": "Chapter summary generation failed",
                        },
                    )

        queue: asyncio.Queue[int | None] = asyncio.Queue(
            maxsize=max(1, max_concurrency * 2)
        )

        def load_claim(segment_id: int) -> SimpleNamespace | None:
            with self._session() as session:
                segment = session.get(Segment, segment_id)
                if (
                    segment is None
                    or segment.project_id != project_id
                    or segment.status not in claimable
                ):
                    return None
                segment.status = "processing"
                segment.error_msg = None
                segment.updated_at = utc_now()
                session.add(segment)
                session.commit()

                previous = list(
                    session.exec(
                        select(Segment)
                        .where(
                            Segment.project_id == project_id,
                            Segment.ord < segment.ord,
                            Segment.status.in_(["done", "reviewed"]),
                            Segment.target_text.is_not(None),
                        )
                        .order_by(Segment.ord.desc())
                        .limit(previous_count)
                    ).all()
                )
                previous.reverse()
                glossary = list(
                    session.exec(
                        select(GlossaryTerm).where(
                            GlossaryTerm.project_id == project_id,
                            GlossaryTerm.enabled.is_(True),
                        )
                    ).all()
                )
                chapter = session.get(Chapter, segment.chapter_id)
                detached = SimpleNamespace(
                    id=segment.id,
                    ord=segment.ord,
                    source_text=segment.source_text,
                    src_hash=segment.src_hash,
                    struct_path=dict(segment.struct_path or {}),
                )
                detached.context = context_builder.build(
                    detached,
                    [
                        SimpleNamespace(
                            ord=row.ord,
                            source_text=row.source_text,
                            target_text=row.target_text,
                            status=row.status,
                        )
                        for row in previous
                    ],
                    glossary=[
                        SimpleNamespace(
                            source_term=term.source_term,
                            target_term=term.target_term,
                            note=term.note,
                            case_sensitive=term.case_sensitive,
                            enabled=term.enabled,
                        )
                        for term in glossary
                    ],
                    chapter_summary=chapter.summary if chapter else None,
                    model=model,
                )
                return detached

        def set_pending(segment_id: int) -> None:
            with self._session() as session:
                session.exec(
                    update(Segment)
                    .where(
                        Segment.id == segment_id,
                        Segment.project_id == project_id,
                        Segment.status == "processing",
                    )
                    .values(status="pending", error_msg=None, updated_at=utc_now())
                )
                session.commit()

        async def worker() -> None:
            while True:
                segment_id = await queue.get()
                try:
                    if segment_id is None:
                        return
                    if _stop_requested(stop_event):
                        await update_stats(stopped=True)
                        continue
                    segment = load_claim(segment_id)
                    if segment is None:
                        continue
                    if _stop_requested(stop_event):
                        set_pending(segment_id)
                        await update_stats(stopped=True)
                        continue
                    async with hash_locks[segment.src_hash]:
                        if _stop_requested(stop_event):
                            set_pending(segment_id)
                            await update_stats(stopped=True)
                            continue
                        if not force:
                            with self._session() as session:
                                cached = TranslationMemory(
                                    session, source_lang, target_lang
                                ).lookup(segment.src_hash)
                            if cached is not None:
                                with self._session() as session:
                                    result = session.exec(
                                        update(Segment)
                                        .where(
                                            Segment.id == segment_id,
                                            Segment.project_id == project_id,
                                            Segment.status == "processing",
                                        )
                                        .values(
                                            target_text=cached,
                                            status="done",
                                            error_msg=None,
                                            updated_at=utc_now(),
                                        )
                                    )
                                    session.commit()
                                    updated = bool(result.rowcount)
                                if updated:
                                    current = await update_stats(done=1, tm_hits=1)
                                    await _emit(
                                        event_callback,
                                        {
                                            "type": "segment_done",
                                            "project_id": project_id,
                                            "segment_id": segment_id,
                                            "target_text": cached,
                                            **current,
                                            "tm_hit": True,
                                        },
                                    )
                                continue

                        segment_path = dict(segment.struct_path)
                        protected = protect_for_segment(segment.source_text, segment_path)
                        try:
                            if stream:

                                async def on_delta(
                                    delta: str,
                                    protected_segment: Any = protected,
                                    current_segment_id: int = segment_id,
                                ) -> None:
                                    if not protected_segment.replacements:
                                        await _emit(
                                            event_callback,
                                            {
                                                "type": "segment_delta",
                                                "project_id": project_id,
                                                "segment_id": current_segment_id,
                                                "delta": delta,
                                            },
                                        )

                                async def on_reset(
                                    current_segment_id: int = segment_id,
                                ) -> None:
                                    await _emit(
                                        event_callback,
                                        {
                                            "type": "segment_reset",
                                            "project_id": project_id,
                                            "segment_id": current_segment_id,
                                        },
                                    )

                                provider_result = await stream_with_retry(
                                    active_provider,
                                    protected.text,
                                    system_prompt=system_prompt,
                                    context=segment.context,
                                    model=model,
                                    temperature=temperature,
                                    on_delta=on_delta,
                                    on_reset=on_reset,
                                    policy=self.retry_policy,
                                    rate_limit=rate_limit,
                                )
                            else:
                                provider_result = await translate_with_retry(
                                    active_provider,
                                    protected.text,
                                    system_prompt=system_prompt,
                                    context=segment.context,
                                    model=model,
                                    temperature=temperature,
                                    policy=self.retry_policy,
                                    rate_limit=rate_limit,
                                )
                            if not provider_result.text.strip():
                                raise ValueError(SAFE_EMPTY_RESULT_ERROR)
                            target_text = restore_for_segment(
                                protected,
                                provider_result.text.strip(),
                                segment_path,
                            )
                            with self._session() as session:
                                result = session.exec(
                                    update(Segment)
                                    .where(
                                        Segment.id == segment_id,
                                        Segment.project_id == project_id,
                                        Segment.status == "processing",
                                    )
                                    .values(
                                        target_text=target_text,
                                        struct_path=segment_path,
                                        status="done",
                                        error_msg=None,
                                        token_in=provider_result.token_in,
                                        token_out=provider_result.token_out,
                                        provider=provider_result.model or model,
                                        updated_at=utc_now(),
                                    )
                                )
                                session.commit()
                                updated = bool(result.rowcount)
                            if not updated:
                                continue
                            with self._session() as session:
                                TranslationMemory(session, source_lang, target_lang).store(
                                    segment.src_hash,
                                    segment.source_text,
                                    target_text,
                                )
                            current = await update_stats(
                                done=1,
                                token_in=provider_result.token_in,
                                token_out=provider_result.token_out,
                            )
                            if stream and protected.replacements:
                                await _emit(
                                    event_callback,
                                    {
                                        "type": "segment_delta",
                                        "project_id": project_id,
                                        "segment_id": segment_id,
                                        "delta": target_text,
                                    },
                                )
                            await _emit(
                                event_callback,
                                {
                                    "type": "segment_done",
                                    "project_id": project_id,
                                    "segment_id": segment_id,
                                    "target_text": target_text,
                                    **current,
                                    "tm_hit": False,
                                },
                            )
                        except asyncio.CancelledError:
                            set_pending(segment_id)
                            raise
                        except Exception:
                            with self._session() as session:
                                result = session.exec(
                                    update(Segment)
                                    .where(
                                        Segment.id == segment_id,
                                        Segment.project_id == project_id,
                                        Segment.status == "processing",
                                    )
                                    .values(
                                        status="error",
                                        error_msg=SAFE_PROVIDER_ERROR,
                                        updated_at=utc_now(),
                                    )
                                )
                                session.commit()
                                updated = bool(result.rowcount)
                            if updated:
                                current = await update_stats(errors=1)
                                await _emit(
                                    event_callback,
                                    {
                                        "type": "error",
                                        "project_id": project_id,
                                        "segment_id": segment_id,
                                        "error_code": "provider_request_failed",
                                        "error": SAFE_PROVIDER_ERROR,
                                        **current,
                                    },
                                )
                finally:
                    queue.task_done()

        workers = [
            asyncio.create_task(worker(), name=f"translation-worker-{index}")
            for index in range(min(max_concurrency, max(1, len(candidate_ids))))
        ]
        try:
            for candidate_id in candidate_ids:
                if _stop_requested(stop_event):
                    await update_stats(stopped=True)
                    break
                await queue.put(candidate_id)
            for _ in workers:
                await queue.put(None)
            await queue.join()
            await asyncio.gather(*workers)
        except asyncio.CancelledError:
            await update_stats(stopped=True)
            for worker_task in workers:
                worker_task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            with self._session() as session:
                session.exec(
                    update(Segment)
                    .where(
                        Segment.project_id == project_id,
                        Segment.status == "processing",
                    )
                    .values(status="pending", error_msg=None, updated_at=utc_now())
                )
                session.commit()
            raise

        with self._session() as session:
            remaining = session.exec(
                select(Segment.id).where(
                    Segment.project_id == project_id,
                    Segment.status.in_(["pending", "processing", "error"]),
                )
            ).first()
            project = session.get(Project, project_id)
            if project is not None:
                project.status = "ready" if stats.stopped or remaining is not None else "done"
                project.updated_at = utc_now()
                session.add(project)
                session.commit()
        await _emit(
            event_callback,
            {
                "type": "progress",
                "project_id": project_id,
                **(await update_stats()),
                "stopped": stats.stopped,
            },
        )
        return stats

    async def _segment_event(
        self,
        callback: EventCallback | None,
        project_id: int,
        segment: Segment,
        stats: TranslationStats,
        tm_hit: bool,
    ) -> None:
        await _emit(
            callback,
            {
                "type": "segment_done",
                "project_id": project_id,
                "segment_id": segment.id,
                "target_text": segment.target_text,
                "done": stats.done,
                "total": stats.total,
                "tm_hit": tm_hit,
            },
        )

    async def translate_segment(
        self,
        segment_id: int,
        *,
        force: bool = True,
        event_callback: EventCallback | None = None,
    ) -> TranslationStats:
        with self._session() as session:
            segment = session.get(Segment, segment_id)
            if segment is None:
                raise LookupError(f"Segment {segment_id} does not exist")
            project_id = segment.project_id
        return await self.translate_project(
            project_id,
            event_callback=event_callback,
            segment_ids=[segment_id],
            force=force,
        )
