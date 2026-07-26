from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete
from sqlmodel import select
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import Response

from . import __version__
from .api import artifacts, auth, glossary, projects, segments, stream, tm
from .api import settings as settings_api
from .api.runtime import translation_tasks
from .config import get_settings
from .db import checkpoint_wal, migrate_db, session_factory
from .models import AuditEvent, Project, Segment, utc_now
from .schemas import HealthResponse
from .security.crypto import MASTER_KEY_ENV, CredentialCryptoError, read_master_key_file
from .security.dependencies import require_authenticated_session
from .security.sessions import initialize_admin

logger = logging.getLogger(__name__)


class SPAStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: dict[str, object]) -> Response:
        normalized = path.lstrip("/")
        first_component = normalized.split("/", 1)[0]
        if first_component in {"api", "health", "docs", "redoc", "openapi.json"}:
            return await super().get_response(path, scope)
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
        else:
            if response.status_code != 404:
                return response
        return await super().get_response("index.html", scope)


def _recover_interrupted_work() -> None:
    """Move process-local in-flight states back to resumable states."""

    with session_factory() as session:
        processing = list(
            session.exec(select(Segment).where(Segment.status == "processing")).all()
        )
        for segment in processing:
            segment.status = "pending"
            segment.updated_at = utc_now()
            session.add(segment)
        translating = list(
            session.exec(select(Project).where(Project.status == "translating")).all()
        )
        for project in translating:
            project.status = "ready"
            project.updated_at = utc_now()
            session.add(project)
        if processing or translating:
            session.commit()


def run_storage_maintenance(*, audit_retention_days: int) -> int:
    """Prune expired audit events and truncate the SQLite WAL.

    Returns the number of audit rows removed. Failures propagate so the
    caller can log them without masking bugs.
    """

    removed = 0
    if audit_retention_days > 0:
        with session_factory() as session:
            # created_at is a lexicographically sortable ISO-8601 UTC string.
            result = session.exec(
                delete(AuditEvent).where(
                    AuditEvent.created_at < _audit_cutoff(audit_retention_days)
                )
            )
            session.commit()
            removed = int(getattr(result, "rowcount", 0) or 0)
    checkpoint_wal()
    return removed


def _audit_cutoff(retention_days: int) -> str:
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) - timedelta(days=retention_days)).isoformat(
        timespec="milliseconds"
    )


async def _maintenance_loop(interval_seconds: int) -> None:
    """Periodic WAL checkpoint and audit-event retention enforcement."""

    settings = get_settings()
    while True:
        await asyncio.sleep(max(60, interval_seconds))
        try:
            removed = await asyncio.to_thread(
                run_storage_maintenance,
                audit_retention_days=settings.audit_retention_days,
            )
            if removed:
                logger.info("Maintenance pruned %d expired audit events", removed)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Storage maintenance run failed")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    del app
    settings = get_settings()
    settings.ensure_directories()

    # Fail fast when credential crypto is configured but broken, instead of
    # deferring the failure to the first credential write weeks later.
    if os.environ.get(MASTER_KEY_ENV):
        try:
            read_master_key_file()
        except CredentialCryptoError as exc:
            raise RuntimeError(f"Master key validation failed: {exc}") from exc

    if settings.run_migrations_on_startup:
        migrate_db()
    _recover_interrupted_work()
    with session_factory() as session:
        initialize_admin(session)

    # Start the durable job worker so queued jobs execute. SIGTERM/SIGINT are
    # handled by the ASGI server (uvicorn), which runs this lifespan's exit;
    # installing our own signal handlers here would replace uvicorn's and
    # break server shutdown.
    await translation_tasks.startup()

    maintenance = asyncio.create_task(
        _maintenance_loop(settings.maintenance_interval_seconds),
        name="storage-maintenance",
    )
    try:
        yield
    finally:
        maintenance.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await maintenance
        # Drain in-flight jobs gracefully, then persist interruption records.
        await translation_tasks.shutdown()


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="EPUB and Markdown AI translation workbench API.",
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None if settings.is_production else "/redoc",
        openapi_url=None if settings.is_production else "/openapi.json",
    )
    application.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=settings.trusted_hosts,
    )
    if settings.cors_origins and not settings.is_production:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @application.middleware("http")
    async def security_headers(request: Request, call_next: object) -> Response:
        request_id = request.headers.get("x-request-id", "")
        if not request_id or len(request_id) > 64:
            request_id = uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)  # type: ignore[operator]
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        )
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        if settings.is_production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @application.get("/health/live", response_model=HealthResponse, tags=["system"])
    def live_health() -> HealthResponse:
        return HealthResponse(status="ok", version=__version__)

    health_dependencies = (
        [] if not settings.is_production else [Depends(require_authenticated_session)]
    )

    @application.get(
        "/health",
        response_model=HealthResponse,
        tags=["system"],
        include_in_schema=not settings.is_production,
        dependencies=health_dependencies,
    )
    @application.get(
        "/api/health",
        response_model=HealthResponse,
        tags=["system"],
        include_in_schema=not settings.is_production,
        dependencies=health_dependencies,
    )
    def health() -> HealthResponse:
        # Readiness: the service is healthy only if the database answers.
        try:
            with session_factory() as session:
                session.exec(select(1)).one()
        except Exception as exc:
            raise StarletteHTTPException(
                status_code=503, detail="database unavailable"
            ) from exc
        return HealthResponse(status="ok", version=__version__)

    protected = [Depends(require_authenticated_session)]
    application.include_router(auth.router, prefix=settings.api_prefix)
    application.include_router(
        projects.router, prefix=settings.api_prefix, dependencies=protected
    )
    application.include_router(
        artifacts.router, prefix=settings.api_prefix, dependencies=protected
    )
    application.include_router(
        segments.router, prefix=settings.api_prefix, dependencies=protected
    )
    application.include_router(
        glossary.router, prefix=settings.api_prefix, dependencies=protected
    )
    application.include_router(
        stream.router, prefix=settings.api_prefix, dependencies=protected
    )
    application.include_router(tm.router, prefix=settings.api_prefix, dependencies=protected)
    application.include_router(
        settings_api.router, prefix=settings.api_prefix, dependencies=protected
    )

    frontend_dist = settings.resolved_frontend_dist
    if frontend_dist.is_dir():
        application.mount(
            "/",
            SPAStaticFiles(directory=frontend_dist, html=True),
            name="frontend",
        )
    else:
        @application.get("/", include_in_schema=False)
        def root() -> dict[str, str]:
            return {
                "name": settings.app_name,
                "version": settings.app_version,
                "docs": "/docs",
            }

    return application


app = create_app()
