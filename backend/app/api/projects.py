from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from ..config import Settings, get_settings
from ..db import get_session
from ..engine.translator import collect_segment_ids_in_chunks
from ..models import (
    Chapter,
    ModelProfile,
    Project,
    PromptTemplate,
    Segment,
    StoredArtifact,
    utc_now,
)
from ..parsers.epub_parser import validate_epub_archive
from ..qa import run_project_qa
from ..schemas import (
    CostEstimate,
    ExportOptions,
    ExportResult,
    ProjectDetail,
    ProjectList,
    ProjectPatch,
    ProviderConfig,
    QAReport,
    TaskState,
    TranslateRequest,
)
from ..services.providers import default_profile_id
from ..storage import StorageError, StorageService, UploadTooLarge
from .adapters import export_project_file, parse_and_persist_project, run_project_translation
from .queries import project_to_detail, project_to_read
from .runtime import event_broker, translation_tasks

router = APIRouter(prefix="/projects", tags=["projects"])

_SUPPORTED_SUFFIXES = {
    ".epub": "epub",
    ".md": "md",
    ".markdown": "md",
}
_SAFE_STEM_RE = re.compile(r"[^\w\u4e00-\u9fff.-]+", re.UNICODE)
logger = logging.getLogger(__name__)


def _project_or_404(session: Session, project_id: int) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _safe_title(filename: str) -> str:
    title = Path(filename).stem.strip()
    return title[:500] or "Untitled"


def _safe_export_stem(title: str) -> str:
    stem = _SAFE_STEM_RE.sub("-", title).strip("-.")
    return (stem[:80] or "translation").lower()


def _media_type(source_type: str) -> str:
    return "application/epub+zip" if source_type == "epub" else "text/markdown"


def _parse_project_in_background(
    bind: Any,
    project_id: int,
    *,
    max_chars: int,
) -> int:
    with Session(bind) as background_session:
        project = background_session.get(Project, project_id)
        if project is None:
            raise LookupError(f"project {project_id} not found")
        return parse_and_persist_project(
            background_session,
            project,
            max_chars=max_chars,
        )


@router.post(
    "",
    response_model=ProjectDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Upload and parse a book",
)
async def create_project(
    file: Annotated[UploadFile, File(description="An EPUB or Markdown source file")],
    title: Annotated[str | None, Form()] = None,
    source_lang: Annotated[str, Form()] = "en",
    target_lang: Annotated[str, Form()] = "zh-CN",
    source_type: Annotated[str | None, Form()] = None,
    provider_cfg: Annotated[str | None, Form(description="JSON provider configuration")] = None,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ProjectDetail:
    original_name = file.filename or "upload"
    suffix = Path(original_name).suffix.lower()
    inferred_type = _SUPPORTED_SUFFIXES.get(suffix)
    if inferred_type is None:
        raise HTTPException(
            status_code=415,
            detail="Only .epub, .md and .markdown files are supported",
        )
    requested_type = source_type.lower().strip() if source_type else inferred_type
    if requested_type not in {"epub", "md"}:
        raise HTTPException(
            status_code=415,
            detail="Only .epub, .md and .markdown files are supported",
        )
    if inferred_type is not None and inferred_type != requested_type:
        raise HTTPException(status_code=422, detail="source_type does not match file extension")
    stored_suffix = ".epub" if requested_type == "epub" else ".md"
    storage = StorageService(settings)
    object_key = storage.random_object_key(stored_suffix)

    config: dict[str, Any] = settings.default_provider_config
    if provider_cfg:
        try:
            provided = json.loads(provider_cfg)
            if not isinstance(provided, dict):
                raise ValueError("provider_cfg must be a JSON object")
            validated = ProviderConfig.model_validate(provided)
            config.update(validated.model_dump(exclude_none=True))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=f"Invalid provider_cfg: {exc}") from exc

    artifact = StoredArtifact(
        kind="source",
        object_key=object_key,
        original_filename=original_name[:512],
        download_filename=Path(original_name).name[:512] or f"source{stored_suffix}",
        media_type=_media_type(requested_type),
        status="pending",
    )
    session.add(artifact)
    session.commit()
    session.refresh(artifact)

    try:
        stored = await storage.stream_upload(
            file,
            kind="source",
            object_key=object_key,
            maximum_bytes=settings.max_upload_mb * 1024 * 1024,
        )
    except UploadTooLarge as exc:
        artifact.status = "error"
        artifact.updated_at = utc_now()
        session.add(artifact)
        session.commit()
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Upload exceeds {exc.maximum_bytes // (1024 * 1024)} MB limit",
        ) from exc
    except Exception:
        artifact.status = "error"
        artifact.updated_at = utc_now()
        session.add(artifact)
        session.commit()
        raise

    if requested_type == "epub":
        try:
            validate_epub_archive(
                stored.path,
                maximum_uncompressed_bytes=settings.max_upload_mb * 4 * 1024 * 1024,
            )
        except ValueError as exc:
            try:
                storage.delete_object("source", object_key)
            except StorageError:
                logger.warning("Could not remove rejected source artifact", exc_info=True)
            artifact.status = "error"
            artifact.updated_at = utc_now()
            session.add(artifact)
            session.commit()
            raise HTTPException(
                status_code=422,
                detail=f"Invalid EPUB archive: {exc}",
            ) from exc

    now = utc_now()
    artifact.size_bytes = stored.size_bytes
    artifact.sha256 = stored.sha256
    artifact.status = "ready"
    artifact.ready_at = now
    artifact.updated_at = now
    session.add(artifact)
    session.commit()
    session.refresh(artifact)

    project = Project(
        title=((title or "").strip() or _safe_title(original_name))[:500],
        source_lang=source_lang.strip() or "en",
        target_lang=target_lang.strip() or "zh-CN",
        source_type=requested_type,
        # Legacy internal bridge while parsers/writers still accept source_path.
        source_path=str(stored.path),
        source_artifact_id=artifact.id,
        provider_cfg=config,
        # New books follow the enabled default model profile so an admin can
        # switch providers without editing every project afterwards.
        model_profile_id=default_profile_id(session),
        status="parsing",
    )
    session.add(project)
    session.commit()
    session.refresh(project)

    artifact.project_id = project.id
    artifact.updated_at = utc_now()
    session.add(artifact)
    session.commit()

    try:
        segment_count = await asyncio.to_thread(
            _parse_project_in_background,
            session.get_bind(),
            project.id or 0,
            max_chars=settings.segment_max_chars,
        )
        session.expire_all()
        project = _project_or_404(session, project.id or 0)
    except Exception as exc:
        session.rollback()
        project = session.get(Project, project.id or 0)
        if project is not None:
            project.status = "error"
            project.updated_at = utc_now()
            session.add(project)
            session.commit()
        raise HTTPException(
            status_code=422,
            detail={
                "message": f"Could not parse {requested_type} source: {exc}",
                "project_id": project.id if project else None,
            },
        ) from exc

    await event_broker.publish(
        project.id or 0,
        "parsed",
        total=segment_count,
    )
    return project_to_detail(session, project)


@router.get("", response_model=ProjectList, summary="List projects with progress")
def list_projects(
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    session: Session = Depends(get_session),
) -> ProjectList:
    total = int(session.exec(select(func.count(Project.id))).one())
    projects = session.exec(
        select(Project)
        .order_by(Project.updated_at.desc(), Project.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return ProjectList(
        items=[project_to_read(session, project) for project in projects],
        total=total,
    )


@router.get("/{project_id}", response_model=ProjectDetail, summary="Get project details")
def get_project(
    project_id: int,
    session: Session = Depends(get_session),
) -> ProjectDetail:
    return project_to_detail(session, _project_or_404(session, project_id))


@router.patch("/{project_id}", response_model=ProjectDetail, summary="Update a project")
def update_project(
    project_id: int,
    patch: ProjectPatch,
    session: Session = Depends(get_session),
) -> ProjectDetail:
    project = _project_or_404(session, project_id)
    changes = patch.model_dump(exclude_unset=True)
    # Explicit IDs must point at existing, enabled objects so a project can
    # never silently translate through a deleted or paused configuration.
    if "model_profile_id" in changes and changes["model_profile_id"] is not None:
        profile = session.get(ModelProfile, changes["model_profile_id"])
        if profile is None:
            raise HTTPException(status_code=422, detail="Model profile not found")
        if not profile.enabled:
            raise HTTPException(status_code=422, detail="Model profile is disabled")
    if "prompt_template_id" in changes and changes["prompt_template_id"] is not None:
        template = session.get(PromptTemplate, changes["prompt_template_id"])
        if template is None:
            raise HTTPException(status_code=422, detail="Prompt template not found")
        if not template.enabled:
            raise HTTPException(status_code=422, detail="Prompt template is disabled")
    provider_cfg = changes.pop("provider_cfg", None)
    for key, value in changes.items():
        # An explicit null clears a profile/template assignment and reverts to
        # the fallback behaviour; other nullable fields ignore null.
        if value is not None or key in ("model_profile_id", "prompt_template_id"):
            setattr(project, key, value)
    if provider_cfg is not None:
        if isinstance(provider_cfg, ProviderConfig):
            provided = provider_cfg.without_none()
        else:
            provided = {key: value for key, value in provider_cfg.items() if value is not None}
        project.provider_cfg = {**project.provider_cfg, **provided}
    project.updated_at = utc_now()
    session.add(project)
    session.commit()
    session.refresh(project)
    return project_to_detail(session, project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: int,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> None:
    project = _project_or_404(session, project_id)
    if translation_tasks.running(project_id):
        await translation_tasks.stop(project_id)

    storage = StorageService(settings)
    artifacts = list(
        session.exec(
            select(StoredArtifact).where(StoredArtifact.project_id == project_id)
        ).all()
    )
    for artifact in artifacts:
        artifact.status = "deleting"
        artifact.updated_at = utc_now()
        session.add(artifact)
    session.commit()

    for artifact in artifacts:
        try:
            storage.delete_object(artifact.kind, artifact.object_key)
        except (OSError, StorageError):
            artifact.status = "error"
            artifact.updated_at = utc_now()
            session.add(artifact)
            session.commit()
            logger.warning(
                "Could not remove artifact %s while deleting project %s",
                artifact.id,
                project_id,
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail="Could not safely remove all project artifacts",
            ) from None
        artifact.status = "deleted"
        artifact.deleted_at = utc_now()
        artifact.updated_at = artifact.deleted_at
        session.add(artifact)
    session.commit()

    try:
        session.delete(project)
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        raise

    await event_broker.clear(project_id)


@router.post(
    "/{project_id}/translate",
    response_model=TaskState,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start or resume project translation",
)
async def start_translation(
    project_id: int,
    request: TranslateRequest = TranslateRequest(),
    session: Session = Depends(get_session),
) -> TaskState:
    project = _project_or_404(session, project_id)
    if translation_tasks.running(project_id):
        return TaskState(
            project_id=project_id,
            running=True,
            status="translating",
            message="Translation is already running",
        )
    scoped = request.chapter_id is not None or request.segment_ids is not None
    if request.segment_ids is not None and not request.segment_ids:
        return TaskState(
            project_id=project_id,
            running=False,
            status=project.status,
            message="No segments selected",
        )
    if request.chapter_id is not None:
        chapter_exists = session.get(Chapter, request.chapter_id)
        if chapter_exists is None or chapter_exists.project_id != project_id:
            raise HTTPException(status_code=404, detail="Chapter not found")

    eligible_statuses = ["pending", "error"] if request.retry_errors else ["pending"]
    if scoped:
        # Resolve the concrete queue up front so a restart replays exactly the
        # same segments, and so the response can report the real count.
        eligible_ids = collect_segment_ids_in_chunks(
            session,
            project_id=project_id,
            statuses=eligible_statuses,
            segment_ids=request.segment_ids,
            chapter_id=request.chapter_id,
        )
        remaining = len(eligible_ids)
    else:
        filters = [
            Segment.project_id == project_id,
            Segment.status.in_(eligible_statuses),
        ]
        eligible_ids = None
        remaining = int(session.exec(select(func.count(Segment.id)).where(*filters)).one())

    if remaining == 0:
        return TaskState(
            project_id=project_id,
            running=False,
            status=project.status,
            message="No pending segments in scope" if scoped else "No pending segments",
        )

    # force stays False so scoped runs only fill in pending/error segments and
    # never discard translations the user already has in the same chapter.
    payload: dict[str, Any] = {"retry_errors": request.retry_errors}
    if eligible_ids is not None:
        payload["segment_ids"] = eligible_ids
    started = translation_tasks.start(
        project_id,
        lambda stop_event: run_project_translation(
            project_id,
            stop_event,
            retry_errors=request.retry_errors,
            segment_ids=eligible_ids,
        ),
        payload=payload,
    )
    return TaskState(
        project_id=project_id,
        running=started,
        status="translating" if started else project.status,
        message=f"Queued {remaining} segments" if started else "Translation is already running",
    )


@router.post("/{project_id}/stop", response_model=TaskState, summary="Stop translation")
async def stop_translation(
    project_id: int,
    session: Session = Depends(get_session),
) -> TaskState:
    project = _project_or_404(session, project_id)
    stopped = await translation_tasks.stop(project_id)
    processing = session.exec(
        select(Segment).where(
            Segment.project_id == project_id,
            Segment.status == "processing",
        )
    ).all()
    for segment in processing:
        segment.status = "pending"
        segment.updated_at = utc_now()
        session.add(segment)
    project.status = "ready" if project.status == "translating" else project.status
    project.updated_at = utc_now()
    session.add(project)
    session.commit()
    if stopped:
        await event_broker.publish(project_id, "stopped")
    return TaskState(
        project_id=project_id,
        running=False,
        status=project.status,
        message="Translation stopped" if stopped else "No translation task was running",
    )


@router.get("/{project_id}/qa", response_model=QAReport, summary="Run QA checks")
def project_qa(
    project_id: int,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> QAReport:
    _project_or_404(session, project_id)
    return run_project_qa(
        session,
        project_id,
        temporary_directory=settings.resolved_temp_dir,
    )


@router.post("/{project_id}/export", response_model=ExportResult, summary="Export a book")
def export_project(
    project_id: int,
    options: ExportOptions,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ExportResult:
    project = _project_or_404(session, project_id)
    output_format = options.format or project.source_type
    if output_format != project.source_type:
        raise HTTPException(
            status_code=422,
            detail="Cross-format export is not supported; use the source format",
        )
    stem = _safe_export_stem(project.title)
    timestamp = utc_now().replace(":", "").replace("-", "").replace("+", "").replace(".", "")
    filename = f"{stem}-p{project_id}-{timestamp}.{output_format}"
    storage = StorageService(settings)
    object_key = storage.random_object_key(f".{output_format}")
    part_path = storage.part_path("export", object_key)
    artifact = StoredArtifact(
        kind="export",
        object_key=object_key,
        download_filename=filename,
        media_type=_media_type(output_format),
        project_id=project_id,
        status="pending",
    )
    session.add(artifact)
    session.commit()
    session.refresh(artifact)
    try:
        output_path = export_project_file(
            session,
            project,
            part_path,
            mode=options.mode,
            include_untranslated=options.include_untranslated,
        )
        if output_path.resolve(strict=False) != part_path.resolve(strict=False):
            raise RuntimeError("writer returned an unexpected export path")
        stored = storage.publish_generated(
            kind="export",
            object_key=object_key,
            generated_path=output_path,
        )
    except Exception as exc:
        try:
            storage.discard_part("export", object_key)
        except StorageError:
            logger.warning("Could not remove partial export artifact", exc_info=True)
        artifact.status = "error"
        artifact.updated_at = utc_now()
        session.add(artifact)
        session.commit()
        raise HTTPException(status_code=500, detail="Export failed") from exc

    now = utc_now()
    artifact.size_bytes = stored.size_bytes
    artifact.sha256 = stored.sha256
    artifact.status = "ready"
    artifact.ready_at = now
    artifact.updated_at = now
    session.add(artifact)
    session.commit()
    session.refresh(artifact)
    if artifact.id is None:
        raise HTTPException(status_code=500, detail="Export artifact was not registered")
    return ExportResult(
        artifact_id=artifact.id,
        filename=artifact.download_filename,
        download_url=f"/api/artifacts/{artifact.id}/download",
        path=str(stored.path),
    )


_MODEL_PRICES: tuple[tuple[str, tuple[float, float]], ...] = (
    ("gpt-5.4-mini", (0.75, 4.50)),
    ("gpt-5-mini", (0.25, 2.00)),
    ("gpt-4o", (2.50, 10.00)),
    ("claude-sonnet-5", (3.00, 15.00)),
    ("gemini-3.6-flash", (1.50, 7.50)),
    ("gemini-3.5-flash-lite", (0.30, 2.50)),
    ("deepseek-v4-flash", (0.14, 0.28)),
    ("deepseek-v4-pro", (0.435, 0.87)),
)


@router.get(
    "/{project_id}/estimate",
    response_model=CostEstimate,
    summary="Estimate remaining tokens and illustrative API cost",
    description=(
        "Uses a character-based token heuristic. Prices are illustrative snapshots, "
        "not live quotes; set input_usd_per_million/output_usd_per_million in "
        "provider_cfg for an explicit estimate."
    ),
)
def estimate_translation(
    project_id: int,
    session: Session = Depends(get_session),
) -> CostEstimate:
    project = _project_or_404(session, project_id)
    all_segments = list(
        session.exec(
            select(Segment.source_text, Segment.status, Segment.chapter_id).where(
                Segment.project_id == project_id
            )
        ).all()
    )
    remaining = [
        (source_text, chapter_id)
        for source_text, segment_status, chapter_id in all_segments
        if segment_status in {"pending", "error"}
    ]
    source_chars = sum(len(text) for text, _ in remaining)
    # About four Latin characters/token, with 20% context/prompt overhead.
    translation_input = math.ceil(source_chars / 4 * 1.2)
    # Chinese output commonly tokenizes more densely than the English source.
    translation_output = math.ceil(source_chars / 3)
    config = project.provider_cfg or {}
    model = str(config.get("model") or "unknown")
    summary_input = 0
    summary_output = 0
    chapters_to_summarize = 0
    if config.get("generate_chapter_summaries", False):
        candidate_chapters = {chapter_id for _, chapter_id in remaining}
        unsummarized = {
            chapter.id
            for chapter in session.exec(
                select(Chapter).where(
                    Chapter.project_id == project_id,
                    Chapter.id.in_(candidate_chapters),
                )
            ).all()
            if chapter.id is not None and not (chapter.summary or "").strip()
        }
        maximum_summary_chars = max(1, int(config.get("summary_max_chars", 8000)))
        source_by_chapter: dict[int, int] = {}
        for text, _segment_status, chapter_id in all_segments:
            source_by_chapter[chapter_id] = source_by_chapter.get(chapter_id, 0) + len(text) + 2
        chapters_to_summarize = len(unsummarized)
        summary_chars = sum(
            min(source_by_chapter.get(chapter_id, 0), maximum_summary_chars)
            for chapter_id in unsummarized
        )
        summary_input = math.ceil(summary_chars / 4)
        summary_output = chapters_to_summarize * 120
    estimated_input = translation_input + summary_input
    estimated_output = translation_output + summary_output
    input_price = config.get("input_usd_per_million")
    output_price = config.get("output_usd_per_million")
    note = "No price configured; token estimates only."
    if input_price is None or output_price is None:
        matched = next(
            (price for marker, price in _MODEL_PRICES if marker in model.lower()),
            None,
        )
        if matched:
            input_price, output_price = matched
            note = (
                "Illustrative built-in price snapshot; provider pricing can change. "
                "Override it in provider_cfg for budgeting."
            )
    else:
        note = "Price supplied by this project's provider_cfg."
    estimated_cost = None
    if input_price is not None and output_price is not None:
        input_price = float(input_price)
        output_price = float(output_price)
        estimated_cost = round(
            estimated_input * input_price / 1_000_000
            + estimated_output * output_price / 1_000_000,
            6,
        )
    return CostEstimate(
        project_id=project_id,
        model=model,
        total_segments=len(all_segments),
        remaining_segments=len(remaining),
        chapters_to_summarize=chapters_to_summarize,
        estimated_translation_input_tokens=translation_input,
        estimated_translation_output_tokens=translation_output,
        estimated_summary_input_tokens=summary_input,
        estimated_summary_output_tokens=summary_output,
        estimated_input_tokens=estimated_input,
        estimated_output_tokens=estimated_output,
        estimated_total_tokens=estimated_input + estimated_output,
        input_usd_per_million=input_price,
        output_usd_per_million=output_price,
        estimated_cost_usd=estimated_cost,
        pricing_note=note,
    )
