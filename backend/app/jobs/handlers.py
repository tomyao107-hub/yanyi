from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

JobHandler = Callable[[int, int, dict[str, Any], asyncio.Event], Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class HandlerSpec:
    handler: JobHandler
    idempotent: bool


class UnsupportedJobTypeError(RuntimeError):
    """Raised when a persisted job has no in-process implementation yet."""


async def run_translation_job(
    job_id: int,
    project_id: int,
    payload: dict[str, Any],
    stop_event: asyncio.Event,
) -> Any:
    from ..api.adapters import run_project_translation

    del job_id
    segment_ids = payload.get("segment_ids")
    if segment_ids is not None:
        segment_ids = [int(segment_id) for segment_id in segment_ids]
    return await run_project_translation(
        project_id,
        stop_event,
        retry_errors=bool(payload.get("retry_errors", True)),
        segment_ids=segment_ids,
        force=bool(payload.get("force", False)),
    )


HANDLERS: dict[str, HandlerSpec] = {
    "translate": HandlerSpec(run_translation_job, idempotent=True),
    "retranslate": HandlerSpec(run_translation_job, idempotent=True),
}


def get_handler(job_type: str) -> HandlerSpec | None:
    return HANDLERS.get(job_type)


def supported_job_types() -> frozenset[str]:
    return frozenset(HANDLERS)
