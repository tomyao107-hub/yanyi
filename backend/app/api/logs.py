from __future__ import annotations

import math
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import delete as sa_delete, func
from sqlmodel import Session, select

from ..db import get_session
from ..models import Project, RuntimeLog
from ..schemas import RuntimeLogPage, RuntimeLogRead

router = APIRouter(prefix="/projects", tags=["runtime-logs"])


@router.get(
    "/{project_id}/logs",
    response_model=RuntimeLogPage,
    summary="List persistent runtime logs for a project",
)
def list_runtime_logs(
    project_id: int,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=500)] = 200,
    level: Literal["debug", "info", "warning", "error"] | None = None,
    event_type: str | None = None,
    session: Session = Depends(get_session),
) -> RuntimeLogPage:
    if session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    filters = [RuntimeLog.project_id == project_id]
    if level is not None:
        filters.append(RuntimeLog.level == level)
    if event_type is not None:
        filters.append(RuntimeLog.event_type == event_type)
    total = int(session.exec(select(func.count(RuntimeLog.id)).where(*filters)).one())
    items = list(
        session.exec(
            select(RuntimeLog)
            .where(*filters)
            .order_by(RuntimeLog.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    )
    return RuntimeLogPage(
        items=[RuntimeLogRead.model_validate(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size) if total else 0,
    )


@router.delete(
    "/{project_id}/logs",
    status_code=204,
    summary="Clear all runtime logs for a project",
)
def clear_runtime_logs(
    project_id: int,
    session: Session = Depends(get_session),
) -> Response:
    if session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    session.exec(sa_delete(RuntimeLog).where(RuntimeLog.project_id == project_id))
    session.commit()
    return Response(status_code=204)
