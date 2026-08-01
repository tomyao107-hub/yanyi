from __future__ import annotations

import math
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_
from sqlmodel import Session, select

from ..db import get_session
from ..engine.tm import TranslationMemory
from ..models import Project, Segment, utc_now
from ..schemas import (
    BulkActionResult,
    SegmentBulkAction,
    SegmentIdList,
    SegmentPage,
    SegmentPatch,
    SegmentRead,
    TaskState,
)
from .adapters import run_project_translation
from .runtime import event_broker, translation_tasks

router = APIRouter(tags=["segments"])

MAX_EXPLICIT_SELECTION = 10_000


def _project_or_404(session: Session, project_id: int) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _segment_or_404(session: Session, segment_id: int) -> Segment:
    segment = session.get(Segment, segment_id)
    if segment is None:
        raise HTTPException(status_code=404, detail="Segment not found")
    return segment


@router.get(
    "/projects/{project_id}/segments",
    response_model=SegmentPage,
    summary="Page and filter project segments",
)
def list_segments(
    project_id: int,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=500)] = 100,
    chapter_id: int | None = None,
    segment_status: Annotated[
        list[Literal["pending", "processing", "done", "error", "reviewed"]] | None,
        Query(alias="status"),
    ] = None,
    search: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    session: Session = Depends(get_session),
) -> SegmentPage:
    _project_or_404(session, project_id)
    filters = [Segment.project_id == project_id]
    if chapter_id is not None:
        filters.append(Segment.chapter_id == chapter_id)
    if segment_status:
        filters.append(Segment.status.in_(segment_status))
    if search:
        escaped = (
            search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        pattern = f"%{escaped}%"
        filters.append(
            or_(
                Segment.source_text.ilike(pattern, escape="\\"),
                Segment.target_text.ilike(pattern, escape="\\"),
            )
        )

    total = int(
        session.exec(select(func.count(Segment.id)).where(*filters)).one()
    )
    items = list(
        session.exec(
            select(Segment)
            .where(*filters)
            .order_by(Segment.ord, Segment.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    )
    return SegmentPage(
        items=[SegmentRead.model_validate(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size) if total else 0,
    )


@router.get(
    "/projects/{project_id}/segment-ids",
    response_model=SegmentIdList,
    summary="List selectable segment IDs for the current filters",
)
def list_segment_ids(
    project_id: int,
    chapter_id: int | None = None,
    segment_status: Annotated[
        list[Literal["pending", "processing", "done", "error", "reviewed"]] | None,
        Query(alias="status"),
    ] = None,
    session: Session = Depends(get_session),
) -> SegmentIdList:
    _project_or_404(session, project_id)
    filters = [
        Segment.project_id == project_id,
        Segment.status != "processing",
    ]
    if chapter_id is not None:
        filters.append(Segment.chapter_id == chapter_id)
    if segment_status:
        filters.append(Segment.status.in_(segment_status))
    ids = [
        int(segment_id)
        for segment_id in session.exec(
            select(Segment.id)
            .where(*filters)
            .order_by(Segment.ord, Segment.id)
            .limit(MAX_EXPLICIT_SELECTION + 1)
        ).all()
    ]
    if len(ids) > MAX_EXPLICIT_SELECTION:
        raise HTTPException(
            status_code=422,
            detail=(
                "The current filter contains more than 10000 selectable segments; "
                "narrow the chapter or status filter"
            ),
        )
    return SegmentIdList(ids=ids, total=len(ids))


@router.get("/segments/{segment_id}", response_model=SegmentRead, summary="Get a segment")
def get_segment(
    segment_id: int,
    session: Session = Depends(get_session),
) -> SegmentRead:
    return SegmentRead.model_validate(_segment_or_404(session, segment_id))


@router.patch(
    "/segments/{segment_id}",
    response_model=SegmentRead,
    summary="Edit a translation or mark its review status",
)
async def update_segment(
    segment_id: int,
    patch: SegmentPatch,
    session: Session = Depends(get_session),
) -> SegmentRead:
    segment = _segment_or_404(session, segment_id)
    target_changed = "target_text" in patch.model_fields_set
    if segment.status == "processing":
        raise HTTPException(
            status_code=409,
            detail="A processing segment cannot be edited until translation finishes or stops",
        )
    if "target_text" in patch.model_fields_set:
        segment.target_text = patch.target_text
        if patch.status is None:
            segment.status = "done" if (patch.target_text or "").strip() else "pending"
    if patch.status is not None:
        segment.status = patch.status
    if segment.status in {"done", "reviewed"} and not (segment.target_text or "").strip():
        raise HTTPException(
            status_code=422,
            detail="done/reviewed segments require non-empty target_text",
        )
    if segment.status in {"done", "reviewed", "pending"}:
        segment.error_msg = None
    segment.updated_at = utc_now()
    session.add(segment)
    project = session.get(Project, segment.project_id)
    if project is not None:
        project.updated_at = utc_now()
        session.add(project)
    session.commit()
    if (
        target_changed
        and project is not None
        and segment.status in {"done", "reviewed"}
        and (segment.target_text or "").strip()
    ):
        TranslationMemory(
            session,
            project.source_lang,
            project.target_lang,
        ).store(
            segment.src_hash,
            segment.source_text,
            segment.target_text or "",
        )
    session.refresh(segment)
    await event_broker.publish(
        segment.project_id,
        "segment_updated",
        segment_id=segment.id,
        status=segment.status,
        target_text=segment.target_text,
    )
    return SegmentRead.model_validate(segment)


@router.post(
    "/segments/{segment_id}/retranslate",
    response_model=TaskState,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Force a single segment retranslation",
)
async def retranslate_segment(
    segment_id: int,
    session: Session = Depends(get_session),
) -> TaskState:
    segment = _segment_or_404(session, segment_id)
    project = _project_or_404(session, segment.project_id)
    if translation_tasks.running(project.id or 0):
        raise HTTPException(
            status_code=409,
            detail="Stop the active project translation before forcing a retranslation",
        )
    segment.status = "pending"
    segment.target_text = None
    segment.error_msg = None
    segment.token_in = None
    segment.token_out = None
    segment.provider = None
    segment.updated_at = utc_now()
    session.add(segment)
    session.commit()
    # force=True bypasses the TM cache in the translator; persist the scope in
    # the durable job payload so the executor honours it after a restart too.
    started = translation_tasks.start(
        project.id or 0,
        lambda stop_event: run_project_translation(
            project.id or 0,
            stop_event,
            retry_errors=False,
            segment_ids=[segment_id],
            force=True,
        ),
        job_type="retranslate",
        payload={
            "retry_errors": False,
            "segment_ids": [segment_id],
            "force": True,
        },
        progress_total=1,
    )
    return TaskState(
        project_id=project.id or 0,
        running=started,
        status="translating",
        message="Segment retranslation started",
    )


@router.post(
    "/projects/{project_id}/segments/bulk",
    response_model=BulkActionResult,
    summary="Bulk mark, reset, or retranslate segments",
    description=(
        "Scope by explicit segment_ids, chapter_id and/or statuses. With no scope "
        "the action applies to every segment in the project."
    ),
)
async def bulk_segment_action(
    project_id: int,
    action: SegmentBulkAction,
    session: Session = Depends(get_session),
) -> BulkActionResult:
    project = _project_or_404(session, project_id)
    if action.action in {"set_pending", "retranslate"} and translation_tasks.running(
        project_id
    ):
        raise HTTPException(
            status_code=409,
            detail="Stop the active project translation before resetting segments",
        )
    filters = [Segment.project_id == project_id]
    if action.segment_ids is not None:
        if not action.segment_ids:
            return BulkActionResult(project_id=project_id, matched=0, updated=0)
        filters.append(Segment.id.in_(action.segment_ids))
    if action.chapter_id is not None:
        filters.append(Segment.chapter_id == action.chapter_id)
    if action.statuses:
        filters.append(Segment.status.in_(action.statuses))

    segments = list(
        session.exec(
            select(Segment).where(*filters).order_by(Segment.ord, Segment.id)
        ).all()
    )
    updated_ids: list[int] = []
    for segment in segments:
        if action.action == "mark_reviewed":
            if segment.status not in {"done", "reviewed"} or not (
                segment.target_text or ""
            ).strip():
                continue
            segment.status = "reviewed"
        else:
            segment.status = "pending"
            segment.target_text = None
            segment.error_msg = None
            segment.token_in = None
            segment.token_out = None
            segment.provider = None
        segment.updated_at = utc_now()
        session.add(segment)
        if segment.id is not None:
            updated_ids.append(segment.id)
    if updated_ids:
        project.updated_at = utc_now()
        session.add(project)
        session.commit()

    started = False
    if action.action == "retranslate" and action.start_translation and updated_ids:
        started = translation_tasks.start(
            project_id,
            lambda stop_event: run_project_translation(
                project_id,
                stop_event,
                retry_errors=False,
                segment_ids=updated_ids,
                force=True,
            ),
            job_type="retranslate",
            payload={
                "retry_errors": False,
                "segment_ids": updated_ids,
                "force": True,
            },
            progress_total=len(updated_ids),
        )
    await event_broker.publish(
        project_id,
        "segments_bulk_updated",
        action=action.action,
        segment_ids=updated_ids,
        count=len(updated_ids),
    )
    return BulkActionResult(
        project_id=project_id,
        matched=len(segments),
        updated=len(updated_ids),
        translation_started=started,
    )
