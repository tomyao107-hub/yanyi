from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlmodel import Session

from ..config import Settings, get_settings
from ..db import get_session, session_factory
from ..models import Project
from .queries import project_progress
from .runtime import PublishedEvent, event_broker, translation_tasks

router = APIRouter(tags=["stream"])


def _encode(event: PublishedEvent) -> str:
    payload = json.dumps(event.as_dict(), ensure_ascii=False, separators=(",", ":"))
    return f"id: {event.id}\nevent: {event.type}\ndata: {payload}\n\n"


def _replay_events(project_id: int, cursor: int | None) -> list[PublishedEvent]:
    # A fresh EventSource has no cursor and should receive only a current
    # snapshot. Explicit cursors are used for browser reconnect catch-up.
    return [] if cursor is None else event_broker.history(project_id, cursor)


@router.get(
    "/projects/{project_id}/stream",
    response_class=StreamingResponse,
    summary="Stream translation progress with Server-Sent Events",
)
async def project_stream(
    project_id: int,
    request: Request,
    after: int | None = Query(default=None, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    cursor = after
    if cursor is None and last_event_id:
        try:
            cursor = max(0, int(last_event_id))
        except ValueError:
            cursor = None

    async def events() -> AsyncIterator[str]:
        yield "retry: 2000\n\n"
        async with event_broker.subscribe(project_id) as queue:
            last_sent = cursor or 0
            replay = _replay_events(project_id, cursor)
            for event in replay:
                last_sent = max(last_sent, event.id)
                yield _encode(event)

            with session_factory() as current_session:
                current_project = current_session.get(Project, project_id)
                if current_project is not None:
                    progress = project_progress(current_session, project_id)
                    await event_broker.publish(
                        project_id,
                        "progress",
                        **progress.model_dump(),
                        project_status=current_project.status,
                        running=translation_tasks.running(project_id),
                    )

            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(
                        queue.get(),
                        timeout=settings.sse_heartbeat_seconds,
                    )
                except TimeoutError:
                    yield f": heartbeat {project_id}\n\n"
                    continue
                if event.id <= last_sent:
                    continue
                last_sent = event.id
                yield _encode(event)
                if event.type == "project_deleted":
                    break

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
