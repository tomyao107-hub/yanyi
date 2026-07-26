from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from .app.config import get_settings
from .app.db import create_db_engine, migrate_db
from .app.engine import Translator
from .app.models import Project, utc_now
from .app.parsers import parse_document
from .app.providers import LiteLLMProvider, TranslationProvider
from .app.segment import persist_document
from .app.writers import export_project


def _database_url(value: str | None) -> str | None:
    if not value:
        return None
    if "://" in value:
        return value
    path = Path(value).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path.as_posix()}"


def _default_output(source: Path, mode: str) -> Path:
    suffix = ".bilingual" if mode == "bilingual" else ".zh"
    return source.with_name(f"{source.stem}{suffix}{source.suffix}")


def _source_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def translate_file(
    source_path: str | Path,
    *,
    model: str,
    output_path: str | Path | None = None,
    database_url: str | None = None,
    source_lang: str = "en",
    target_lang: str = "zh-CN",
    temperature: float = 0.3,
    max_concurrency: int = 4,
    context_token_budget: int = 1200,
    segment_max_chars: int = 1500,
    mode: str = "bilingual",
    include_untranslated: bool = True,
    force: bool = False,
    reparse: bool = False,
    stream: bool = False,
    generate_chapter_summaries: bool = False,
    provider: TranslationProvider | None = None,
) -> tuple[Path, Any]:
    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    source_type = source.suffix.lstrip(".").lower()
    if source_type in {"markdown", "mdown"}:
        source_type = "md"
    if source_type not in {"md", "epub"}:
        raise ValueError("Only .md and .epub inputs are supported")
    destination = (
        Path(output_path).expanduser().resolve()
        if output_path
        else _default_output(source, mode)
    )
    if destination == source:
        raise ValueError("Output path must differ from the source path")
    source_fingerprint = _source_fingerprint(source)

    db_engine = create_db_engine(_database_url(database_url))
    try:
        migrate_db(db_engine.url.render_as_string(hide_password=False))
        provider_cfg = {
            "model": model,
            "temperature": temperature,
            "max_concurrency": max_concurrency,
            "context_token_budget": context_token_budget,
            "stream": stream,
            "generate_chapter_summaries": generate_chapter_summaries,
            "source_fingerprint": source_fingerprint,
        }

        with Session(db_engine) as session:
            project = session.exec(
                select(Project)
                .where(
                    Project.source_path == str(source),
                    Project.source_lang == source_lang,
                    Project.target_lang == target_lang,
                )
                .order_by(Project.id.desc())
            ).first()
            if project is None:
                project = Project(
                    title=source.stem,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    source_type=source_type,
                    source_path=str(source),
                    provider_cfg=provider_cfg,
                    status="parsing",
                )
                session.add(project)
                session.commit()
                session.refresh(project)
                reparse = True
            else:
                previous_fingerprint = (project.provider_cfg or {}).get(
                    "source_fingerprint"
                )
                reparse = reparse or previous_fingerprint != source_fingerprint
                project.provider_cfg = provider_cfg
                project.updated_at = utc_now()
                session.add(project)
                session.commit()
            if project.id is None:
                raise RuntimeError("Project primary key was not generated")
            project_id = project.id
            if reparse:
                document = parse_document(source, source_type)
                persist_document(
                    session,
                    project_id,
                    document,
                    max_chars=segment_max_chars,
                )
                project.status = "ready"
                project.updated_at = utc_now()
                session.add(project)
                session.commit()

        async def show_progress(event: dict[str, Any]) -> None:
            event_type = event.get("type")
            if event_type == "segment_done":
                done = event.get("done", 0)
                total = event.get("total", 0)
                marker = "TM" if event.get("tm_hit") else "LLM"
                print(f"\rTranslated {done}/{total} [{marker}]", end="", flush=True)
            elif event_type == "error" and event.get("segment_id"):
                print(
                    (
                        f"\nSegment {event['segment_id']} failed: "
                        f"{event.get('error', 'unknown error')}"
                    ),
                    file=sys.stderr,
                )

        translator = Translator(
            session_factory=lambda: Session(db_engine),
            provider=provider or LiteLLMProvider(),
        )
        stats = await translator.translate_project(
            project_id,
            event_callback=show_progress,
            force=force,
        )
        if stats.total:
            print()
        with Session(db_engine) as session:
            exported = export_project(
                session,
                project_id,
                destination,
                mode=mode,
                include_untranslated=include_untranslated,
            )
        return exported, stats
    finally:
        db_engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        prog="trans",
        description="Translate EPUB or Markdown books with a LiteLLM-compatible model.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    translate = subparsers.add_parser("translate", help="translate or resume a document")
    translate.add_argument("file", type=Path)
    translate.add_argument("--model", default=settings.default_model)
    translate.add_argument("--out", type=Path)
    translate.add_argument("--db", help="SQLite path or SQLAlchemy database URL")
    translate.add_argument("--source-lang", default="en")
    translate.add_argument("--target-lang", default="zh-CN")
    translate.add_argument("--temperature", type=float, default=settings.default_temperature)
    translate.add_argument(
        "--concurrency",
        type=int,
        default=settings.default_max_concurrency,
    )
    translate.add_argument(
        "--context-tokens",
        type=int,
        default=settings.context_token_budget,
    )
    translate.add_argument(
        "--segment-max-chars",
        type=int,
        default=settings.segment_max_chars,
    )
    translate.add_argument(
        "--mode",
        choices=("bilingual", "target_only"),
        default="bilingual",
    )
    translate.add_argument(
        "--exclude-untranslated",
        action="store_true",
        help="leave unfinished target-only blocks blank",
    )
    translate.add_argument(
        "--force",
        action="store_true",
        help="retranslate eligible segments and bypass translation memory",
    )
    translate.add_argument(
        "--reparse",
        action="store_true",
        help="reparse the source while preserving unchanged completed segments",
    )
    translate.add_argument("--stream", action="store_true", help="request streaming output")
    translate.add_argument(
        "--chapter-summaries",
        action="store_true",
        help="generate and cache short chapter summaries before translation",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "translate":
        return 2
    try:
        exported, stats = asyncio.run(
            translate_file(
                args.file,
                model=args.model,
                output_path=args.out,
                database_url=args.db,
                source_lang=args.source_lang,
                target_lang=args.target_lang,
                temperature=args.temperature,
                max_concurrency=args.concurrency,
                context_token_budget=args.context_tokens,
                segment_max_chars=args.segment_max_chars,
                mode=args.mode,
                include_untranslated=not args.exclude_untranslated,
                force=args.force,
                reparse=args.reparse,
                stream=args.stream,
                generate_chapter_summaries=args.chapter_summaries,
            )
        )
    except KeyboardInterrupt:
        print("\nInterrupted; completed segments are saved. Run the same command to resume.")
        return 130
    except Exception as exc:
        print(f"Translation failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"Exported {exported} "
        f"({stats.done} done, {stats.tm_hits} TM hits, {stats.errors} errors)"
    )
    return 2 if stats.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
