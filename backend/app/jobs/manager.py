from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ..db import session_factory as default_session_factory
from ..models import Job, utc_now
from ..services.runtime_logs import record_runtime_log
from .handlers import get_handler

logger = logging.getLogger(__name__)

ACTIVE_JOB_STATUSES = ("queued", "running", "stopping")
TERMINAL_JOB_STATUSES = ("succeeded", "failed", "cancelled", "interrupted")
SAFE_JOB_FAILURE_SUMMARY = "The background job failed"
SAFE_UNSUPPORTED_SUMMARY = "This job type is not available in this server version"
LEASE_SECONDS = 30
HEARTBEAT_SECONDS = 10
SHUTDOWN_GRACE_SECONDS = 8.0


def _future_timestamp(seconds: int) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat(timespec="milliseconds")


def _retry_backoff_seconds(attempt_count: int) -> int:
    """Exponential floor between attempts: 5s, 10s, 20s, 40s, capped at 60s."""

    return min(60, 5 * (2 ** max(0, attempt_count - 1)))


def _json_result(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "__dict__"):
        return {
            key: item
            for key, item in vars(value).items()
            if isinstance(item, (str, int, float, bool, type(None), list, dict))
        }
    if isinstance(value, dict):
        return value
    return {"result": str(value)}


class JobManager:
    """Durable single-worker bridge backed by the Job table.

    The database is the source of truth. Local tasks/events only make the
    single-process service responsive; duplicate prevention is enforced by the
    partial unique index and handled as an IntegrityError transaction.
    """

    def __init__(self, session_factory: Any = None) -> None:
        self._session_factory = session_factory or default_session_factory
        self._worker: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._closing = False
        self._local_stop_events: dict[int, asyncio.Event] = {}
        self._instance_id = f"local-{uuid4().hex}"

    def _session(self) -> Session:
        return self._session_factory()

    def active_job(self, project_id: int, session: Session | None = None) -> Job | None:
        owns_session = session is None
        current = session or self._session()
        try:
            return current.exec(
                select(Job)
                .where(
                    Job.project_id == project_id,
                    Job.status.in_(ACTIVE_JOB_STATUSES),
                )
                .order_by(Job.created_at, Job.id)
            ).first()
        finally:
            if owns_session:
                current.close()

    def get(self, job_id: int, session: Session | None = None) -> Job | None:
        owns_session = session is None
        current = session or self._session()
        try:
            return current.get(Job, job_id)
        finally:
            if owns_session:
                current.close()

    def running(self, project_id: int) -> bool:
        return self.active_job(project_id) is not None

    def create(
        self,
        project_id: int,
        *,
        job_type: str = "translate",
        payload: dict[str, Any] | None = None,
        progress_total: int = 0,
    ) -> tuple[Job, bool]:
        """Persist a queued job, returning the winner on an active-job race."""

        with self._session() as session:
            existing = self.active_job(project_id, session)
            if existing is not None:
                return existing, False
            job = Job(
                project_id=project_id,
                job_type=job_type,
                status="queued",
                payload_json=dict(payload or {}),
                progress_total=max(0, progress_total),
            )
            session.add(job)
            try:
                session.commit()
            except IntegrityError:
                # The unique active-project index chose another concurrent request.
                # The failed INSERT must be rolled back before querying that winner.
                session.rollback()
                existing = self.active_job(project_id, session)
                if existing is None:
                    raise
                return existing, False
            session.refresh(job)
            snapshot = self._snapshot(job)
        self._ensure_worker()
        self._wake.set()
        return snapshot, True

    def start(
        self,
        project_id: int,
        job_factory: Any = None,
        *,
        job_type: str = "translate",
        payload: dict[str, Any] | None = None,
        progress_total: int = 0,
    ) -> bool:
        """Queue a durable job for the project.

        The factory argument is deliberately ignored: persisted handler payloads
        define work so jobs survive restarts. Pass translation options through
        ``payload`` (retry_errors, segment_ids, force). Test runner injection
        remains available in adapters.
        """

        del job_factory
        _, created = self.create(
            project_id,
            job_type=job_type,
            payload=payload,
            progress_total=progress_total,
        )
        return created

    async def stop(self, project_id: int, *, timeout: float = 2.0) -> bool:
        del timeout
        with self._session() as session:
            job = self.active_job(project_id, session)
            if job is None or job.id is None:
                return False
            if job.status not in {"queued", "running", "stopping"}:
                return False
            now = utc_now()
            if job.status == "queued":
                job.status = "cancelled"
                job.cancel_requested_at = now
                job.cancelled_at = now
                job.finished_at = now
            else:
                job.status = "stopping"
                job.cancel_requested_at = now
            job.updated_at = now
            session.add(job)
            session.commit()
            job_id = job.id
            job_type = job.job_type
        record_runtime_log(
            project_id=project_id,
            job_id=job_id,
            level="warning",
            event_type="job.stop_requested",
            message="已请求停止后台任务",
            details={"job_type": job_type},
            session_factory=self._session,
        )
        stop_event = self._local_stop_events.get(job_id)
        if stop_event is not None:
            stop_event.set()
        self._wake.set()
        return True

    async def startup(self) -> None:
        self._closing = False
        self.recover()
        self._ensure_worker()
        self._wake.set()

    def recover(self) -> int:
        """Interrupt expired owners and requeue only supported idempotent work."""

        now = utc_now()
        recovered = 0
        with self._session() as session:
            stale = list(
                session.exec(
                    select(Job).where(
                        Job.status.in_(["running", "stopping"]),
                        (Job.lease_expires_at.is_(None)) | (Job.lease_expires_at < now),
                    )
                ).all()
            )
            for job in stale:
                spec = get_handler(job.job_type)
                job.status = "interrupted"
                job.error_code = "worker_interrupted"
                job.safe_error_summary = "The background worker was interrupted"
                job.finished_at = now
                job.lease_owner = None
                job.lease_expires_at = None
                job.updated_at = now
                session.add(job)
                session.flush()
                recovered += 1
                if spec is not None and spec.idempotent and job.project_id is not None:
                    retry = Job(
                        project_id=job.project_id,
                        job_type=job.job_type,
                        status="queued",
                        source_artifact_id=job.source_artifact_id,
                        model_profile_id=job.model_profile_id,
                        progress_total=job.progress_total,
                        attempt_count=job.attempt_count,
                        max_attempts=job.max_attempts,
                        payload_json=dict(job.payload_json or {}),
                    )
                    session.add(retry)
            if stale:
                try:
                    session.commit()
                except IntegrityError:
                    # A caller may already have queued replacement work. Preserve
                    # interruption records and leave the stale job explicitly retriable.
                    session.rollback()
                    for job in stale:
                        current = session.get(Job, job.id or 0)
                        if current is None:
                            continue
                        current.status = "interrupted"
                        current.error_code = "worker_interrupted"
                        current.safe_error_summary = "The background worker was interrupted"
                        current.finished_at = now
                        current.lease_owner = None
                        current.lease_expires_at = None
                        current.updated_at = now
                        session.add(current)
                    session.commit()
        return recovered

    async def shutdown(self) -> None:
        self._closing = True
        for stop_event in self._local_stop_events.values():
            stop_event.set()
        self._wake.set()
        worker = self._worker
        if worker is not None and not worker.done():
            try:
                await asyncio.wait_for(asyncio.shield(worker), timeout=SHUTDOWN_GRACE_SECONDS)
            except TimeoutError:
                worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker
        self._worker = None
        now = utc_now()
        with self._session() as session:
            owned = list(
                session.exec(
                    select(Job).where(
                        Job.status.in_(["running", "stopping"]),
                        Job.lease_owner == self._instance_id,
                    )
                ).all()
            )
            for job in owned:
                job.status = "interrupted"
                job.error_code = "server_shutdown"
                job.safe_error_summary = "The server stopped before the job completed"
                job.finished_at = now
                job.lease_owner = None
                job.lease_expires_at = None
                job.updated_at = now
                session.add(job)
            if owned:
                session.commit()
        self._local_stop_events.clear()

    def _ensure_worker(self) -> None:
        if self._closing:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(
                self._worker_loop(),
                name="durable-job-worker",
            )

    async def _worker_loop(self) -> None:
        while not self._closing:
            job_id = self._claim_next()
            if job_id is None:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=HEARTBEAT_SECONDS)
                except TimeoutError:
                    pass
                continue
            await self._run(job_id)

    def _claim_next(self) -> int | None:
        now = utc_now()
        with self._session() as session:
            queued_jobs = list(
                session.exec(
                    select(Job)
                    .where(Job.status == "queued")
                    .order_by(Job.created_at, Job.id)
                    .limit(10)
                ).all()
            )
            claimed: Job | None = None
            for queued in queued_jobs:
                if queued.id is None:
                    continue
                if queued.attempt_count > 0:
                    # A previous attempt failed; honour the retry backoff floor
                    # (updated_at was refreshed when the job was requeued).
                    threshold = (
                        datetime.now(UTC)
                        - timedelta(seconds=_retry_backoff_seconds(queued.attempt_count))
                    ).isoformat(timespec="milliseconds")
                    if queued.updated_at > threshold:
                        continue
                claimed = queued
                break
            if claimed is None or claimed.id is None:
                return None
            result = session.exec(
                update(Job)
                .where(Job.id == claimed.id, Job.status == "queued")
                .values(
                    status="running",
                    attempt_count=Job.attempt_count + 1,
                    started_at=claimed.started_at or now,
                    heartbeat_at=now,
                    lease_owner=self._instance_id,
                    lease_expires_at=_future_timestamp(LEASE_SECONDS),
                    updated_at=now,
                )
            )
            session.commit()
            return claimed.id if result.rowcount else None

    async def _run(self, job_id: int) -> None:
        with self._session() as session:
            job = session.get(Job, job_id)
            if job is None or job.project_id is None:
                return
            project_id = job.project_id
            job_type = job.job_type
            payload = dict(job.payload_json or {})
        spec = get_handler(job_type)
        if spec is None:
            self._finish_failed(job_id, "unsupported_job_type", SAFE_UNSUPPORTED_SUMMARY)
            return

        stop_event = asyncio.Event()
        self._local_stop_events[job_id] = stop_event
        heartbeat = asyncio.create_task(
            self._heartbeat(job_id, stop_event),
            name=f"job-heartbeat-{job_id}",
        )
        record_runtime_log(
            project_id=project_id,
            job_id=job_id,
            event_type="job.started",
            message="后台翻译任务开始执行",
            details={
                "job_type": job_type,
                "payload_scope": (
                    "selected" if payload.get("segment_ids") is not None else "all"
                ),
            },
            session_factory=self._session,
        )
        try:
            result = await spec.handler(job_id, project_id, payload, stop_event)
            now = utc_now()
            with self._session() as session:
                job = session.get(Job, job_id)
                if job is None:
                    return
                if stop_event.is_set() or job.status == "stopping":
                    if self._closing and job.cancel_requested_at is None:
                        # Stopped by server shutdown, not by the user.
                        job.status = "interrupted"
                        job.error_code = "server_shutdown"
                        job.safe_error_summary = (
                            "The server stopped before the job completed"
                        )
                    else:
                        job.status = "cancelled"
                        job.cancelled_at = now
                else:
                    job.status = "succeeded"
                    job.result_json = _json_result(result)
                    job.progress_current = job.progress_total
                job.finished_at = now
                job.lease_owner = None
                job.lease_expires_at = None
                job.updated_at = now
                session.add(job)
                session.commit()
                final_status = job.status
            record_runtime_log(
                project_id=project_id,
                job_id=job_id,
                level="warning" if final_status in {"cancelled", "interrupted"} else "info",
                event_type=f"job.{final_status}",
                message=(
                    "后台翻译任务已完成"
                    if final_status == "succeeded"
                    else "后台翻译任务已停止"
                ),
                details={"job_type": job_type},
                session_factory=self._session,
            )
        except asyncio.CancelledError:
            now = utc_now()
            with self._session() as session:
                job = session.get(Job, job_id)
                if job is not None and job.status in {"running", "stopping"}:
                    job.status = "interrupted"
                    job.error_code = "server_shutdown"
                    job.safe_error_summary = "The server stopped before the job completed"
                    job.finished_at = now
                    job.lease_owner = None
                    job.lease_expires_at = None
                    job.updated_at = now
                    session.add(job)
                    session.commit()
            raise
        except Exception as exc:
            logger.exception("Job %s failed with %s", job_id, type(exc).__name__)
            record_runtime_log(
                project_id=project_id,
                job_id=job_id,
                level="error",
                event_type="job.failed",
                message="后台翻译任务执行失败",
                details={"job_type": job_type, "error_type": type(exc).__name__},
                session_factory=self._session,
            )
            self._fail_or_requeue(job_id, "job_execution_failed", SAFE_JOB_FAILURE_SUMMARY)
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
            self._local_stop_events.pop(job_id, None)

    async def _heartbeat(self, job_id: int, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            await asyncio.sleep(HEARTBEAT_SECONDS)
            now = utc_now()
            with self._session() as session:
                job = session.get(Job, job_id)
                if job is None or job.status not in {"running", "stopping"}:
                    return
                if job.status == "stopping" or job.cancel_requested_at is not None:
                    stop_event.set()
                job.heartbeat_at = now
                job.lease_expires_at = _future_timestamp(LEASE_SECONDS)
                job.updated_at = now
                session.add(job)
                session.commit()

    def _finish_failed(self, job_id: int, code: str, summary: str) -> None:
        now = utc_now()
        with self._session() as session:
            job = session.get(Job, job_id)
            if job is None:
                return
            job.status = "failed"
            job.error_code = code
            job.safe_error_summary = summary
            job.finished_at = now
            job.lease_owner = None
            job.lease_expires_at = None
            job.updated_at = now
            session.add(job)
            session.commit()

    def _fail_or_requeue(self, job_id: int, code: str, summary: str) -> None:
        """Requeue a failed attempt while attempts remain, else fail terminally.

        The backoff floor is enforced at claim time from attempt_count and
        updated_at, so a requeued job survives restarts without extra state.
        """

        now = utc_now()
        with self._session() as session:
            job = session.get(Job, job_id)
            if job is None:
                return
            if job.cancel_requested_at is not None or job.attempt_count >= job.max_attempts:
                job.status = "failed"
                job.finished_at = now
            else:
                job.status = "queued"
                logger.info(
                    "Job %s attempt %d/%d failed; retrying after backoff",
                    job_id,
                    job.attempt_count,
                    job.max_attempts,
                )
            job.error_code = code
            job.safe_error_summary = summary
            job.lease_owner = None
            job.lease_expires_at = None
            job.updated_at = now
            session.add(job)
            session.commit()

    @staticmethod
    def _snapshot(job: Job) -> Job:
        return SimpleNamespace(  # type: ignore[return-value]
            **{column.name: getattr(job, column.name) for column in Job.__table__.columns}
        )


job_manager = JobManager()
