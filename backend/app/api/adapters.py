from __future__ import annotations

import asyncio
import hashlib
import importlib
import inspect
import json
import logging
import re
import unicodedata
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from sqlalchemy import delete, update
from sqlmodel import Session, select

from ..db import session_factory
from ..models import Chapter, Job, Project, Segment, utc_now
from ..services.runtime_logs import record_runtime_log
from .queries import project_progress
from .runtime import event_broker

TranslationRunner = Callable[
    [int, asyncio.Event, bool, Callable[..., Awaitable[Any]]], Awaitable[Any]
]
_translation_runner_override: TranslationRunner | None = None
logger = logging.getLogger(__name__)


def set_translation_runner(runner: TranslationRunner | None) -> None:
    """Install a runner for tests or an embedded deployment."""

    global _translation_runner_override
    _translation_runner_override = runner


def _member(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _call_parser(module: Any, path: Path, source_type: str) -> Any:
    function_names = (
        ("parse_markdown", "parse_md", "parse_document", "parse")
        if source_type == "md"
        else ("parse_epub", "parse_document", "parse")
    )
    for name in function_names:
        function = getattr(module, name, None)
        if callable(function):
            try:
                return function(path)
            except TypeError:
                return function(str(path))

    class_names = (
        ("MarkdownParser", "MDParser", "MdParser")
        if source_type == "md"
        else ("EpubParser", "EPUBParser")
    )
    for name in class_names:
        parser_class = getattr(module, name, None)
        if parser_class is None:
            continue
        try:
            parser = parser_class()
        except TypeError:
            parser = parser_class(path)
        parse = getattr(parser, "parse", None)
        if callable(parse):
            try:
                return parse(path)
            except TypeError:
                return parse(str(path))
    raise RuntimeError(f"{module.__name__} does not expose a supported parser")


def parse_source(path: Path, source_type: str) -> Any:
    try:
        parsers = importlib.import_module("backend.app.parsers")
        parse_document = getattr(parsers, "parse_document", None)
        if callable(parse_document):
            return parse_document(path, source_type=source_type)
    except (ImportError, AttributeError):
        pass
    if source_type == "md":
        module_name = "backend.app.parsers.md_parser"
    elif source_type == "epub":
        module_name = "backend.app.parsers.epub_parser"
    else:
        raise ValueError(f"unsupported source type: {source_type}")
    module = importlib.import_module(module_name)
    return _call_parser(module, path, source_type)


_SPACE_RE = re.compile(r"\s+")
_TRANSLATION_TABLE = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
    }
)


def _normalize(text: str) -> str:
    try:
        segment_module = importlib.import_module("backend.app.segment")
        normalizer = getattr(segment_module, "normalize", None) or getattr(
            segment_module, "normalize_text", None
        )
        if callable(normalizer):
            return str(normalizer(text))
    except ImportError:
        pass
    return _SPACE_RE.sub(
        " ", unicodedata.normalize("NFC", text).translate(_TRANSLATION_TABLE).strip()
    )


def _stable_key(chapter_ord: int, block_index: int, struct_path: dict[str, Any]) -> str:
    serialized = json.dumps(
        struct_path,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    suffix = hashlib.sha1(serialized.encode("utf-8")).hexdigest()[:8]
    return f"{chapter_ord:04d}:{block_index:05d}:{suffix}"


def persist_doc_model(session: Session, project: Project, document: Any) -> int:
    """Persist a parser DocModel into the durable translation ledger.

    This deliberately consumes the small protocol documented in plan.md
    instead of depending on parser implementation classes.
    """

    if project.id is None:
        raise ValueError("project must be persisted before parsing")

    session.exec(delete(Segment).where(Segment.project_id == project.id))
    session.exec(delete(Chapter).where(Chapter.project_id == project.id))
    session.flush()

    segment_ord = 0
    segment_count = 0
    chapters = list(_member(document, "chapters", ()) or ())
    for chapter_index, parsed_chapter in enumerate(chapters):
        chapter_ord = int(_member(parsed_chapter, "ord", chapter_index))
        chapter = Chapter(
            project_id=project.id,
            ord=chapter_ord,
            title=_member(parsed_chapter, "title"),
            href=_member(parsed_chapter, "href"),
        )
        session.add(chapter)
        session.flush()
        if chapter.id is None:
            raise RuntimeError("failed to persist chapter")

        blocks = list(_member(parsed_chapter, "blocks", ()) or ())
        for block_index, block in enumerate(blocks):
            text = _member(block, "text")
            translatable = bool(_member(block, "translatable", text is not None))
            if not translatable or text is None or not str(text).strip():
                continue
            struct_path = dict(_member(block, "struct_path", {}) or {})
            source_text = str(text)
            normalized = _normalize(source_text)
            session.add(
                Segment(
                    project_id=project.id,
                    chapter_id=chapter.id,
                    ord=segment_ord,
                    stable_key=_stable_key(chapter_ord, block_index, struct_path),
                    struct_path=struct_path,
                    source_text=source_text,
                    src_hash=hashlib.sha1(normalized.encode("utf-8")).hexdigest(),
                    status="pending",
                )
            )
            segment_ord += 1
            segment_count += 1
    session.flush()
    return segment_count


def parse_and_persist_project(
    session: Session,
    project: Project,
    *,
    max_chars: int = 1500,
) -> int:
    document = parse_source(Path(project.source_path), project.source_type)
    try:
        segment_module = importlib.import_module("backend.app.segment")
        persist_document = segment_module.persist_document
    except (ImportError, AttributeError):
        segments = None
    else:
        segments = persist_document(
            session,
            project.id,
            document,
            max_chars=max_chars,
        )
    count = (
        len(segments)
        if segments is not None
        else persist_doc_model(session, project, document)
    )
    project.status = "ready"
    project.updated_at = utc_now()
    session.add(project)
    session.commit()
    return count


async def _invoke_core_translation(
    project_id: int,
    stop_event: asyncio.Event,
    retry_errors: bool,
    callback: Callable[..., Awaitable[Any]],
    *,
    segment_ids: list[int] | None = None,
    force: bool = False,
) -> Any:
    if _translation_runner_override is not None:
        return await _translation_runner_override(
            project_id, stop_event, retry_errors, callback
        )

    module = importlib.import_module("backend.app.engine.translator")
    runner = getattr(module, "translate_project", None) or getattr(
        module, "run_translation", None
    )
    if callable(runner):
        parameters = inspect.signature(runner).parameters
        kwargs: dict[str, Any] = {}
        for name in parameters:
            if name in {"project_id", "project"}:
                kwargs[name] = project_id
            elif name in {"session_factory", "get_session"}:
                kwargs[name] = session_factory
            elif name in {"stop_event", "cancel_event"}:
                kwargs[name] = stop_event
            elif name in {"event_callback", "callback", "on_event", "emit"}:
                kwargs[name] = callback
            elif name == "retry_errors":
                kwargs[name] = retry_errors
            elif name == "segment_ids":
                kwargs[name] = segment_ids
            elif name in {"force", "bypass_tm"}:
                kwargs[name] = force
        result = runner(**kwargs)
        return await result if inspect.isawaitable(result) else result

    translator_class = getattr(module, "Translator", None) or getattr(
        module, "TranslationEngine", None
    )
    if translator_class is None:
        raise RuntimeError("translator module exposes no supported runner")

    constructor_parameters = inspect.signature(translator_class).parameters
    constructor_kwargs: dict[str, Any] = {}
    if "session_factory" in constructor_parameters:
        constructor_kwargs["session_factory"] = session_factory
    translator = translator_class(**constructor_kwargs)
    method = getattr(translator, "translate_project", None) or getattr(
        translator, "run", None
    )
    if not callable(method):
        raise RuntimeError("translator class exposes no translate_project/run method")
    method_parameters = inspect.signature(method).parameters
    method_kwargs: dict[str, Any] = {}
    for name in method_parameters:
        if name in {"project_id", "project"}:
            method_kwargs[name] = project_id
        elif name in {"stop_event", "cancel_event"}:
            method_kwargs[name] = stop_event
        elif name in {"event_callback", "callback", "on_event", "emit"}:
            method_kwargs[name] = callback
        elif name == "retry_errors":
            method_kwargs[name] = retry_errors
        elif name == "segment_ids":
            method_kwargs[name] = segment_ids
        elif name in {"force", "bypass_tm"}:
            method_kwargs[name] = force
    result = method(**method_kwargs)
    return await result if inspect.isawaitable(result) else result


async def run_project_translation(
    project_id: int,
    stop_event: asyncio.Event,
    *,
    retry_errors: bool = True,
    segment_ids: list[int] | None = None,
    force: bool = False,
    job_id: int | None = None,
) -> None:
    async def callback(event_type: str | dict[str, Any] = "progress", **data: Any) -> None:
        if isinstance(event_type, dict):
            payload = dict(event_type)
            normalized_type = str(payload.pop("type", "progress"))
            payload.update(data)
        else:
            normalized_type = event_type
            payload = data
        for reserved in ("project_id", "type", "id", "timestamp"):
            payload.pop(reserved, None)
        if normalized_type == "runtime_log":
            level = str(payload.pop("level", "info"))
            log_event_type = str(payload.pop("event_type", "runtime.event"))
            message = str(payload.pop("message", "Runtime event"))
            details = dict(payload.pop("details", {}) or {})
            segment_id = payload.get("segment_id")
            chapter_id = payload.get("chapter_id")
            entry = record_runtime_log(
                project_id=project_id,
                job_id=job_id,
                segment_id=int(segment_id) if segment_id is not None else None,
                chapter_id=int(chapter_id) if chapter_id is not None else None,
                level=level,
                event_type=log_event_type,
                message=message,
                details=details,
                session_factory=session_factory,
            )
            await event_broker.publish(
                project_id,
                "runtime_log",
                log_id=entry.id if entry is not None else None,
                job_id=job_id,
                segment_id=segment_id,
                chapter_id=chapter_id,
                level=level,
                runtime_event_type=log_event_type,
                message=message,
                details=details,
            )
            return
        if normalized_type == "progress":
            for key in ("done", "total", "errors"):
                if key in payload:
                    payload[f"batch_{key}"] = payload.pop(key)
            with session_factory() as progress_session:
                payload.update(
                    project_progress(progress_session, project_id).model_dump()
                )
            payload["project_status"] = "translating"
            payload["running"] = True
            if job_id is not None:
                batch_done = int(payload.get("batch_done", 0))
                batch_total = int(payload.get("batch_total", 0))
                with session_factory() as job_session:
                    job_session.exec(
                        update(Job)
                        .where(Job.id == job_id)
                        .values(
                            progress_current=max(0, batch_done),
                            progress_total=max(0, batch_total),
                            updated_at=utc_now(),
                        )
                    )
                    job_session.commit()
        await event_broker.publish(project_id, normalized_type, **payload)

    with session_factory() as session:
        project = session.get(Project, project_id)
        if project is None:
            return
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
        project.status = "translating"
        project.updated_at = utc_now()
        session.add(project)
        session.commit()
        progress = project_progress(session, project_id)
        selected_ids = segment_ids
        if selected_ids is None and not retry_errors:
            selected_ids = list(
                session.exec(
                    select(Segment.id).where(
                        Segment.project_id == project_id,
                        Segment.status == "pending",
                    )
                ).all()
            )
    await event_broker.publish(
        project_id,
        "progress",
        **progress.model_dump(),
        project_status="translating",
        running=True,
    )

    try:
        await _invoke_core_translation(
            project_id,
            stop_event,
            retry_errors,
            callback,
            segment_ids=selected_ids,
            force=force,
        )
    except asyncio.CancelledError:
        with session_factory() as session:
            project = session.get(Project, project_id)
            if project is not None:
                project.status = "ready"
                project.updated_at = utc_now()
                session.add(project)
                session.commit()
        await event_broker.publish(project_id, "stopped")
        raise
    except Exception as exc:
        logger.exception(
            "Project translation failed project=%s job=%s",
            project_id,
            job_id,
        )
        with session_factory() as session:
            project = session.get(Project, project_id)
            if project is not None:
                project.status = "error"
                project.updated_at = utc_now()
                session.add(project)
                session.commit()
        await event_broker.publish(
            project_id,
            "error",
            message=str(exc),
            error_type=type(exc).__name__,
            project_status="error",
            running=False,
        )
        with session_factory() as session:
            progress = project_progress(session, project_id)
        await event_broker.publish(
            project_id,
            "progress",
            **progress.model_dump(),
            project_status="error",
            running=False,
        )
        raise

    with session_factory() as session:
        project = session.get(Project, project_id)
        if project is None:
            return
        progress = project_progress(session, project_id)
        if stop_event.is_set():
            project.status = "ready"
            event_type = "stopped"
        elif progress.pending == 0 and progress.processing == 0 and progress.error == 0:
            project.status = "done"
            event_type = "completed"
        else:
            project.status = "ready"
            event_type = "progress"
        project.updated_at = utc_now()
        session.add(project)
        session.commit()
        final_project_status = project.status
    await event_broker.publish(
        project_id,
        event_type,
        **progress.model_dump(),
        project_status=final_project_status,
        running=False,
    )


async def run_single_segment_translation(
    segment_id: int,
    project_id: int,
) -> None:
    """Use a core single-segment hook, falling back to the project runner."""

    module = importlib.import_module("backend.app.engine.translator")
    async def callback(
        event_type: str | dict[str, Any] = "progress",
        **data: Any,
    ) -> None:
        if isinstance(event_type, dict):
            payload = dict(event_type)
            normalized_type = str(payload.pop("type", "progress"))
            payload.update(data)
        else:
            normalized_type = event_type
            payload = data
        for reserved in ("project_id", "type", "id", "timestamp"):
            payload.pop(reserved, None)
        if normalized_type == "progress":
            for key in ("done", "total", "errors"):
                if key in payload:
                    payload[f"batch_{key}"] = payload.pop(key)
            with session_factory() as progress_session:
                payload.update(
                    project_progress(progress_session, project_id).model_dump()
                )
            payload["project_status"] = "translating"
            payload["running"] = True
        await event_broker.publish(project_id, normalized_type, **payload)

    function = getattr(module, "translate_segment", None)
    if callable(function):
        parameters = inspect.signature(function).parameters
        kwargs: dict[str, Any] = {}
        for name in parameters:
            if name in {"segment_id", "segment"}:
                kwargs[name] = segment_id
            elif name == "project_id":
                kwargs[name] = project_id
            elif name in {"session_factory", "get_session"}:
                kwargs[name] = session_factory
            elif name in {"event_callback", "callback", "on_event", "emit"}:
                kwargs[name] = callback
            elif name in {"force", "bypass_tm"}:
                kwargs[name] = True
        result = function(**kwargs)
        if inspect.isawaitable(result):
            await result
    else:
        translator_class = getattr(module, "Translator", None) or getattr(
            module, "TranslationEngine", None
        )
        if translator_class is None:
            raise RuntimeError("translator module exposes no translate_segment hook")
        constructor_parameters = inspect.signature(translator_class).parameters
        constructor_kwargs: dict[str, Any] = {}
        if "session_factory" in constructor_parameters:
            constructor_kwargs["session_factory"] = session_factory
        translator = translator_class(**constructor_kwargs)
        method = getattr(translator, "translate_segment", None)
        if callable(method):
            result = method(
                segment_id,
                force=True,
                event_callback=callback,
            )
        else:
            result = translator.translate_project(
                project_id,
                stop_event=asyncio.Event(),
                event_callback=callback,
                segment_ids=[segment_id],
                force=True,
            )
        if inspect.isawaitable(result):
            await result
    with session_factory() as session:
        segment = session.get(Segment, segment_id)
        if segment is not None:
            await event_broker.publish(
                project_id,
                "segment_done" if segment.status in {"done", "reviewed"} else "error",
                segment_id=segment_id,
                status=segment.status,
                target_text=segment.target_text,
                error_msg=segment.error_msg,
            )


def _invoke_writer(
    session: Session,
    project: Project,
    output_path: Path,
    *,
    mode: str,
    include_untranslated: bool,
) -> Path:
    module_name = (
        "backend.app.writers.md_writer"
        if project.source_type == "md"
        else "backend.app.writers.epub_writer"
    )
    module = importlib.import_module(module_name)
    functions = (
        ("write_markdown", "export_markdown", "write_project", "export_project")
        if project.source_type == "md"
        else ("write_epub", "export_epub", "write_project", "export_project")
    )
    for name in functions:
        writer = getattr(module, name, None)
        if not callable(writer):
            continue
        parameters = inspect.signature(writer).parameters
        kwargs: dict[str, Any] = {}
        for parameter in parameters:
            if parameter in {"session", "db"}:
                kwargs[parameter] = session
            elif parameter in {"project", "project_id"}:
                kwargs[parameter] = (
                    project.id if parameter == "project_id" else project
                )
            elif parameter in {"source_path", "input_path"}:
                kwargs[parameter] = Path(project.source_path)
            elif parameter in {"output_path", "out_path", "destination"}:
                kwargs[parameter] = output_path
            elif parameter == "mode":
                kwargs[parameter] = mode
            elif parameter == "include_untranslated":
                kwargs[parameter] = include_untranslated
        result = writer(**kwargs)
        if inspect.isawaitable(result):
            raise RuntimeError("async writers are not supported by the sync export adapter")
        return Path(result) if result else output_path

    class_names = (
        ("MarkdownWriter", "MDWriter")
        if project.source_type == "md"
        else ("EpubWriter", "EPUBWriter")
    )
    for class_name in class_names:
        writer_class = getattr(module, class_name, None)
        if writer_class is None:
            continue
        writer = writer_class()
        method = getattr(writer, "write", None) or getattr(writer, "export", None)
        if callable(method):
            result = method(
                session=session,
                project=project,
                output_path=output_path,
                mode=mode,
                include_untranslated=include_untranslated,
            )
            return Path(result) if result else output_path
    raise RuntimeError(f"{module_name} exposes no supported writer")


def export_project_file(
    session: Session,
    project: Project,
    output_path: Path,
    *,
    mode: str,
    include_untranslated: bool,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = _invoke_writer(
        session,
        project,
        output_path,
        mode=mode,
        include_untranslated=include_untranslated,
    )
    if not result.exists():
        raise RuntimeError("writer completed without creating the export file")
    return result
