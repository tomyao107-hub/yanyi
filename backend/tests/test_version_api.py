"""Version identity, schema head/revision readouts and /api/system/version."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from backend.app.api import adapters
from backend.app.config import REPOSITORY_ROOT, Settings, get_settings
from backend.app.db import SCHEMA_HEAD_REVISION, get_session, migrate_db
from backend.app.main import create_app
from backend.app.models import AdminUser
from backend.app.security.crypto import MASTER_KEY_BYTES, MASTER_KEY_ENV
from backend.app.security.csrf import CSRF_COOKIE_NAME, CSRF_HEADER_NAME
from backend.app.security.passwords import hash_password
from backend.app.security.sessions import LoginTokenBucket, normalize_username
from backend.app.services import version as version_service
from backend.app.services.version import (
    code_schema_head,
    db_current_revision,
    load_build_identity,
)

ADMIN_USERNAME = "version-admin"
ADMIN_PASSWORD = "correct horse battery staple"


@pytest.fixture
def client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    key_file = tmp_path / "master.key"
    key_file.write_bytes(os.urandom(MASTER_KEY_BYTES))
    monkeypatch.setenv(MASTER_KEY_ENV, str(key_file))

    database = tmp_path / "version.db"
    engine = create_engine(
        f"sqlite:///{database.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            AdminUser(
                username=ADMIN_USERNAME,
                normalized_username=normalize_username(ADMIN_USERNAME),
                password_hash=hash_password(ADMIN_PASSWORD),
            )
        )
        session.commit()

    settings = Settings(
        data_dir=tmp_path / "data",
        upload_dir=tmp_path / "uploads",
        export_dir=tmp_path / "exports",
        database_url=f"sqlite:///{database.as_posix()}",
    )
    settings.ensure_directories()

    def test_session() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = test_session
    app.dependency_overrides[get_settings] = lambda: settings
    monkeypatch.setattr(adapters, "session_factory", lambda: Session(engine))
    monkeypatch.setattr("backend.app.main.migrate_db", lambda: None)
    monkeypatch.setattr("backend.app.main._recover_interrupted_work", lambda: None)
    monkeypatch.setattr("backend.app.main.initialize_admin", lambda session: False)
    monkeypatch.setattr("backend.app.main.session_factory", lambda: Session(engine))
    # db_current_revision() reads settings directly (not through FastAPI), so
    # point it at the test database rather than the cached real settings.
    monkeypatch.setattr(
        "backend.app.services.version.get_settings", lambda: settings
    )
    monkeypatch.setattr(
        "backend.app.security.sessions.login_token_bucket", LoginTokenBucket()
    )
    from backend.app.jobs.manager import job_manager

    job_manager._session = lambda: Session(engine)
    job_manager._wake = asyncio.Event()
    job_manager._worker = None
    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/auth/login",
            json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        )
        assert response.status_code == 200, response.text
        test_client.headers[CSRF_HEADER_NAME] = test_client.cookies.get(CSRF_COOKIE_NAME)
        test_client.state_engine = engine  # type: ignore[attr-defined]
        yield test_client


def test_build_identity_reads_baked_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baked = tmp_path / "version.json"
    baked.write_text(
        json.dumps(
            {"version": "9.9.9", "git_sha": "abc123", "build_time": "2026-08-02T00:00:00Z"}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(version_service, "_BAKED", baked)
    load_build_identity.cache_clear()
    try:
        identity = load_build_identity()
        assert identity.version == "9.9.9"
        assert identity.git_sha == "abc123"
        assert identity.build_time == "2026-08-02T00:00:00Z"
    finally:
        load_build_identity.cache_clear()


def test_build_identity_falls_back_when_unbaked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(version_service, "_BAKED", tmp_path / "missing.json")
    load_build_identity.cache_clear()
    try:
        identity = load_build_identity()
        assert identity.version == get_settings().app_version
        assert identity.git_sha is None
        assert identity.build_time is None
    finally:
        load_build_identity.cache_clear()


def test_code_schema_head_matches_stamp_constant() -> None:
    assert code_schema_head() == SCHEMA_HEAD_REVISION


def test_db_current_revision_tracks_migration(tmp_path: Path) -> None:
    database = tmp_path / "revision.db"
    url = f"sqlite:///{database.as_posix()}"
    assert db_current_revision(url) is None
    migrate_db(url)
    assert db_current_revision(url) == SCHEMA_HEAD_REVISION


def test_system_version_endpoint_reports_identity_and_schema(
    client: TestClient,
) -> None:
    response = client.get("/api/system/version")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()

    assert payload["version"] == load_build_identity().version
    assert payload["code_schema_head"] == SCHEMA_HEAD_REVISION
    # The test database was created with create_all, not Alembic, so the live
    # revision is unknown and the schema must read as out of sync.
    assert payload["db_schema_revision"] is None
    assert payload["schema_ok"] is False
    assert len(payload["release_commands"]) == 3

    # The lifespan boot hook recorded a kind=boot row into the test database.
    assert payload["releases"], "expected at least the startup boot record"
    boot = payload["releases"][0]
    assert boot["version"] == payload["version"]
    assert boot["kind"] == "boot"
    assert boot["status"] == "ok"
    assert boot["created_at"]


def test_system_version_requires_authentication(client: TestClient) -> None:
    anonymous = TestClient(client.app, base_url=str(client.base_url))
    assert anonymous.get("/api/system/version").status_code == 401


def test_release_record_migration_is_reversible(tmp_path: Path) -> None:
    from alembic import command
    from alembic.config import Config

    database = tmp_path / "reversible.db"
    url = f"sqlite:///{database.as_posix()}"
    migrate_db(url)

    config = Config(str(REPOSITORY_ROOT / "backend" / "alembic.ini"))
    config.attributes["database_url_override"] = url
    command.downgrade(config, "0004_runtime_logs")
    connection = sqlite3.connect(database)
    try:
        tables = [
            row[0]
            for row in connection.execute(
                "select name from sqlite_master where type = 'table'"
            )
        ]
    finally:
        connection.close()
    assert "release_record" not in tables
    assert "alembic_version" in tables

    command.upgrade(config, "head")
    assert db_current_revision(url) == SCHEMA_HEAD_REVISION
