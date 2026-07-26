from __future__ import annotations

import math
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlmodel import Session, select

from ..db import get_session
from ..models import Project, Segment, TMEntry
from ..schemas import TMEntryPage, TMEntryRead, TMStats

router = APIRouter(tags=["translation-memory"])


def _project_or_404(session: Session, project_id: int) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.get(
    "/projects/{project_id}/tm/stats",
    response_model=TMStats,
    summary="Get translation-memory statistics",
)
def translation_memory_stats(
    project_id: int,
    session: Session = Depends(get_session),
) -> TMStats:
    project = _project_or_404(session, project_id)
    pair_filter = (
        TMEntry.source_lang == project.source_lang,
        TMEntry.target_lang == project.target_lang,
    )
    global_entries = int(session.exec(select(func.count(TMEntry.id))).one())
    pair_entries, total_hits = session.exec(
        select(
            func.count(TMEntry.id),
            func.coalesce(func.sum(TMEntry.hit_count), 0),
        ).where(*pair_filter)
    ).one()
    project_segments = int(
        session.exec(
            select(func.count(Segment.id)).where(Segment.project_id == project_id)
        ).one()
    )
    project_matches = int(
        session.exec(
            select(func.count(Segment.id))
            .select_from(Segment)
            .join(
                TMEntry,
                (TMEntry.src_hash == Segment.src_hash)
                & (TMEntry.source_lang == project.source_lang)
                & (TMEntry.target_lang == project.target_lang),
            )
            .where(Segment.project_id == project_id)
        ).one()
    )
    reusable = int(
        session.exec(
            select(func.count(Segment.id))
            .select_from(Segment)
            .join(
                TMEntry,
                (TMEntry.src_hash == Segment.src_hash)
                & (TMEntry.source_lang == project.source_lang)
                & (TMEntry.target_lang == project.target_lang),
            )
            .where(
                Segment.project_id == project_id,
                Segment.status.in_(("pending", "error")),
            )
        ).one()
    )
    # TM hits intentionally do not claim an LLM provider. Manual edits can
    # also appear in this bucket, so the field name states the exact metric.
    completed_without_provider = int(
        session.exec(
            select(func.count(Segment.id)).where(
                Segment.project_id == project_id,
                Segment.status.in_(("done", "reviewed")),
                Segment.provider.is_(None),
            )
        ).one()
    )
    return TMStats(
        project_id=project_id,
        global_entries=global_entries,
        language_pair_entries=int(pair_entries),
        total_hits=int(total_hits),
        project_segments=project_segments,
        project_tm_matches=project_matches,
        reusable_remaining_segments=reusable,
        completed_without_provider=completed_without_provider,
    )


@router.get(
    "/projects/{project_id}/tm",
    response_model=TMEntryPage,
    summary="Page translation-memory entries for this project's language pair",
)
def list_translation_memory(
    project_id: int,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    search: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    session: Session = Depends(get_session),
) -> TMEntryPage:
    project = _project_or_404(session, project_id)
    filters = [
        TMEntry.source_lang == project.source_lang,
        TMEntry.target_lang == project.target_lang,
    ]
    if search:
        filters.append(
            or_(
                TMEntry.source_text.contains(search),
                TMEntry.target_text.contains(search),
            )
        )
    total = int(session.exec(select(func.count(TMEntry.id)).where(*filters)).one())
    entries = session.exec(
        select(TMEntry)
        .where(*filters)
        .order_by(TMEntry.hit_count.desc(), TMEntry.updated_at.desc(), TMEntry.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return TMEntryPage(
        items=[TMEntryRead.model_validate(entry) for entry in entries],
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size) if total else 0,
    )

