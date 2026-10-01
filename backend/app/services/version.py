"""Build identity, schema-head and database schema-revision readouts.

Version identity comes from /app/version.json, baked into the image by the
Dockerfile. Everything here is read-only — no migration is ever triggered.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, select, text
from sqlmodel import Session

from ..config import REPOSITORY_ROOT, get_settings

logger = logging.getLogger(__name__)

_BAKED = Path("/app/version.json")

_BOOT_TRIM_KEEP = 25


@dataclass(frozen=True)
class BuildIdentity:
    version: str
    git_sha: str | None = None
    build_time: str | None = None


@lru_cache(maxsize=1)
def load_build_identity() -> BuildIdentity:
    """Resolve the version identity of the running build.

    Prefers the baked /app/version.json; local development (no container)
    falls back to the configured app version.
    """

    try:
        raw = json.loads(_BAKED.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return BuildIdentity(get_settings().app_version)
    version = str(raw.get("version") or "").strip() or "unknown"
    git_sha = raw.get("git_sha") or None
    build_time = raw.get("build_time") or None
    return BuildIdentity(version=version, git_sha=git_sha, build_time=build_time)


@lru_cache(maxsize=1)
def code_schema_head() -> str:
    """The single head revision of the checked-in Alembic migrations."""

    config = Config(str(REPOSITORY_ROOT / "backend" / "alembic.ini"))
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        raise RuntimeError(f"Expected exactly one Alembic head, found {heads}")
    return heads[0]


def db_current_revision(database_url: str | None = None) -> str | None:
    """Read the live database's Alembic revision, or None when unversioned.

    Read-only: never migrates and never creates tables.
    """

    url = database_url or get_settings().effective_database_url
    engine = create_engine(url)
    try:
        if "alembic_version" not in inspect(engine).get_table_names():
            return None
        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT version_num FROM alembic_version LIMIT 1")
            ).first()
        return str(row[0]) if row and row[0] else None
    finally:
        engine.dispose()


def record_release_boot(session: Session) -> None:
    """Record one kind=boot row per distinct build identity per process start.

    Skips when the latest row already matches the running version+git_sha so
    frequent restarts don't spam history; trims old boot rows to the newest 25.
    """

    from ..models import ReleaseRecord

    identity = load_build_identity()
    latest = session.scalars(
        select(ReleaseRecord).order_by(ReleaseRecord.created_at.desc()).limit(1)
    ).first()
    if latest is not None and latest.version == identity.version and latest.git_sha == identity.git_sha:
        return

    session.add(
        ReleaseRecord(
            version=identity.version,
            git_sha=identity.git_sha,
            kind="boot",
            status="ok",
        )
    )
    session.commit()

    stale = list(
        session.scalars(
            select(ReleaseRecord)
            .where(ReleaseRecord.kind == "boot")
            .order_by(ReleaseRecord.created_at.desc())
            .offset(_BOOT_TRIM_KEEP)
        )
    )
    for row in stale:
        session.delete(row)
    if stale:
        session.commit()
