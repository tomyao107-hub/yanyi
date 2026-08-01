from __future__ import annotations

import logging
from typing import Any

from sqlmodel import Session

from ..db import session_factory as default_session_factory
from ..models import RuntimeLog

logger = logging.getLogger(__name__)

_LEVELS = {"debug", "info", "warning", "error"}
_MAX_MESSAGE_CHARS = 1000


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
            return entry
    except Exception:
        logger.exception(
            "Could not persist runtime log project=%s job=%s event=%s",
            project_id,
            job_id,
            event_type,
        )
        return None
