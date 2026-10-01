from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete as sa_delete
from sqlmodel import Session, select

from ..db import session_factory as default_session_factory
from ..models import RuntimeLog

logger = logging.getLogger(__name__)

_LEVELS = {"debug", "info", "warning", "error"}
_MAX_MESSAGE_CHARS = 1000
_MAX_ROWS_PER_PROJECT = 2000
_MAX_AGE_DAYS = 1
_CLEANUP_EVERY = 100


def _trim_project_logs(session: Session, project_id: int) -> None:
    """Enforce per-project retention: drop old and excess rows, best effort."""

    cutoff = (datetime.now(UTC) - timedelta(days=_MAX_AGE_DAYS)).isoformat(timespec="milliseconds")
    session.exec(
        sa_delete(RuntimeLog).where(
            RuntimeLog.project_id == project_id,
            RuntimeLog.created_at < cutoff,
        )
    )
    stale_ids = session.exec(
        select(RuntimeLog.id)
        .where(RuntimeLog.project_id == project_id)
        .order_by(RuntimeLog.id.desc())
        .offset(_MAX_ROWS_PER_PROJECT)
        .limit(1)
    ).all()
    if stale_ids:
        session.exec(
            sa_delete(RuntimeLog).where(
                RuntimeLog.project_id == project_id,
                RuntimeLog.id <= stale_ids[0],
            )
        )
    session.commit()


def record_runtime_log(
    *,
    project_id: int,
    event_type: str,
    message: str,
    level: str = "info",
    job_id: int | None = None,
    segment_id: int | None = None,
    chapter_id: int | None = None,
    details: dict[str, Any] | None = None,
    session_factory: Any = None,
) -> RuntimeLog | None:
    """Best-effort persistent diagnostics that must never break translation."""

    normalized_level = level if level in _LEVELS else "info"
    factory = session_factory or default_session_factory
    try:
        with factory() as session:
            assert isinstance(session, Session)
            entry = RuntimeLog(
                project_id=project_id,
                job_id=job_id,
                segment_id=segment_id,
                chapter_id=chapter_id,
                level=normalized_level,
                event_type=event_type.strip()[:64] or "runtime.event",
                message=message.strip()[:_MAX_MESSAGE_CHARS] or "Runtime event",
                details_json=dict(details or {}),
            )
            session.add(entry)
            session.commit()
            session.refresh(entry)
            if entry.id % _CLEANUP_EVERY == 0:
                try:
                    _trim_project_logs(session, project_id)
                except Exception:
                    logger.exception("Could not trim runtime logs project=%s", project_id)
            return entry
    except Exception:
        logger.exception(
            "Could not persist runtime log project=%s job=%s event=%s",
            project_id,
            job_id,
            event_type,
        )
        return None
