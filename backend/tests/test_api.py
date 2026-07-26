from __future__ import annotations

import io
import sqlite3
import time
import zipfile
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

from backend.app.api import adapters
from backend.app.api.runtime import event_broker, translation_tasks
from backend.app.api.stream import _replay_events
from backend.app.config import REPOSITORY_ROOT, Settings, get_settings
from backend.app.db import get_session, migrate_db
from backend.app.main import create_app
from backend.app.models import AdminUser, Segment, TMEntry, utc_now
from backend.app.providers.base import TranslationResult
from backend.app.security.csrf import CSRF_COOKIE_NAME, CSRF_HEADER_NAME
from backend.app.security.passwords import hash_password
from backend.app.security.sessions import LoginTokenBucket, normalize_username

ADMIN_USERNAME = "test-admin"
ADMIN_PASSWORD = "correct horse battery staple"


def _seed_admin(engine: object) -> None:
    with Session(engine) as session:  # type: ignore[arg-type]
        session.add(
            AdminUser(
                username=ADMIN_USERNAME,
                normalized_username=normalize_username(ADMIN_USERNAME),
                password_hash=hash_password(ADMIN_PASSWORD),
            )
        )
        session.commit()


def _login(test_client: TestClient) -> None:
    """Authenticate the shared test client and arm double-submit CSRF."""

    response = test_client.post(
        "/api/auth/login",
        json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
    )
    assert response.status_code == 200, response.text
    csrf_token = test_client.cookies.get(CSRF_COOKIE_NAME)
    assert csrf_token
    test_client.headers[CSRF_HEADER_NAME] = csrf_token


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    database = tmp_path / "test.db"
    engine = create_engine(
        f"sqlite:///{database.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    _seed_admin(engine)
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

    def background_session() -> Session:
        return Session(engine)

    app = create_app()
    app.dependency_overrides[get_session] = test_session
    app.dependency_overrides[get_settings] = lambda: settings
    monkeypatch.setattr(adapters, "session_factory", background_session)
    monkeypatch.setattr("backend.app.main.migrate_db", lambda: None)
    monkeypatch.setattr("backend.app.main._recover_interrupted_work", lambda: None)
    monkeypatch.setattr("backend.app.main.initialize_admin", lambda session: False)
    # The login limiter is process-global; give each test a fresh bucket so
    # fixture logins across the suite don't trip it.
    monkeypatch.setattr(
        "backend.app.security.sessions.login_token_bucket", LoginTokenBucket()
    )
    # Ensure JobManager uses the test database engine
    from backend.app.jobs.manager import job_manager
    job_manager._session = background_session
    with TestClient(app) as test_client:
        _login(test_client)
        yield test_client
    adapters.set_translation_runner(None)


@pytest.fixture
def anonymous_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    """A client with no session, used to assert the anonymous boundary."""

    database = tmp_path / "anon.db"
    engine = create_engine(
        f"sqlite:///{database.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    _seed_admin(engine)
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
    monkeypatch.setattr("backend.app.main.migrate_db", lambda: None)
    monkeypatch.setattr("backend.app.main._recover_interrupted_work", lambda: None)
    monkeypatch.setattr("backend.app.main.initialize_admin", lambda session: False)
    with TestClient(app) as test_client:
        yield test_client


def upload_markdown(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/api/projects",
        files={"file": ("sample.md", b"# Chapter\n\nHello New York.\n\nSecond paragraph.")},
        data={"title": "Sample", "provider_cfg": '{"model":"mock/model"}'},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_health_settings_and_project_crud(client: TestClient) -> None:
    assert client.get("/health").json()["status"] == "ok"
    public_settings = client.get("/api/settings")
    assert public_settings.status_code == 200
    assert public_settings.json()["supported_source_types"] == ["epub", "md"]

    project = upload_markdown(client)
    project_id = int(project["id"])
    assert project["status"] == "ready"
    assert project["progress"]["total"] >= 2

    listing = client.get("/api/projects").json()
    assert listing["total"] == 1
    assert listing["items"][0]["id"] == project_id

    changed = client.patch(
        f"/api/projects/{project_id}",
        json={"title": "Renamed", "provider_cfg": {"temperature": 0.1}},
    )
    assert changed.status_code == 200
    assert changed.json()["title"] == "Renamed"
    assert changed.json()["provider_cfg"]["temperature"] == 0.1
    assert client.patch(
        f"/api/projects/{project_id}",
        json={"target_lang": "ja"},
    ).status_code == 422

    estimate = client.get(f"/api/projects/{project_id}/estimate")
    assert estimate.status_code == 200
    assert estimate.json()["remaining_segments"] >= 2

    deleted = client.delete(f"/api/projects/{project_id}")
    assert deleted.status_code == 204
    assert client.get(f"/api/projects/{project_id}").status_code == 404


def test_relative_runtime_paths_are_repository_relative() -> None:
    settings = Settings(
        data_dir="./data",
        upload_dir="./data/uploads",
        export_dir="./exports",
        database_url="sqlite:///./data/example.db",
    )
    assert settings.resolved_data_dir == (REPOSITORY_ROOT / "data").resolve()
    assert settings.resolved_upload_dir == (REPOSITORY_ROOT / "data/uploads").resolve()
    assert settings.resolved_export_dir == (REPOSITORY_ROOT / "exports").resolve()
    assert settings.effective_database_url.endswith("/data/example.db")


def test_deleting_project_clears_sse_history_before_id_reuse(client: TestClient) -> None:
    project_id = int(upload_markdown(client)["id"])

    async def publish_old_event() -> None:
        await event_broker.publish(
            project_id,
            "segment_done",
            segment_id=999,
            target_text="旧书译文",
        )

    import asyncio

    asyncio.run(publish_old_event())
    assert any(event.type == "segment_done" for event in event_broker.history(project_id))
    assert client.delete(f"/api/projects/{project_id}").status_code == 204
    assert event_broker.history(project_id) == []

    replacement_id = int(upload_markdown(client)["id"])
    assert replacement_id == project_id
    assert all(
        event.type != "segment_done"
        for event in event_broker.history(replacement_id)
    )


def test_segments_glossary_qa_and_bulk_actions(client: TestClient) -> None:
    project_id = int(upload_markdown(client)["id"])
    page = client.get(
        f"/api/projects/{project_id}/segments",
        params={"page_size": 10},
    )
    assert page.status_code == 200
    segments = page.json()["items"]
    first_id = segments[0]["id"]
    second_id = segments[1]["id"]

    edited = client.patch(
        f"/api/segments/{first_id}",
        json={"target_text": "章节"},
    )
    assert edited.status_code == 200
    with adapters.session_factory() as session:
        edited_segment = session.get(Segment, first_id)
        assert edited_segment is not None
        edited_tm = session.exec(
            select(TMEntry).where(TMEntry.src_hash == edited_segment.src_hash)
        ).one()
        assert edited_tm.target_text == "章节"
    reviewed = client.patch(
        f"/api/segments/{first_id}",
        json={"status": "reviewed"},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["status"] == "reviewed"
    with adapters.session_factory() as session:
        processing = session.get(Segment, second_id)
        assert processing is not None
        processing.status = "processing"
        session.add(processing)
        session.commit()
    assert client.patch(
        f"/api/segments/{second_id}",
        json={"target_text": "不应覆盖"},
    ).status_code == 409
    reviewed_bulk = client.post(
        f"/api/projects/{project_id}/segments/bulk",
        json={
            "action": "mark_reviewed",
            "segment_ids": [first_id, second_id],
        },
    )
    assert reviewed_bulk.status_code == 200
    assert reviewed_bulk.json()["matched"] == 2
    assert reviewed_bulk.json()["updated"] == 1

    added = client.post(
        f"/api/projects/{project_id}/glossary",
        json={
            "source_term": "New York",
            "target_term": "纽约",
            "case_sensitive": False,
        },
    )
    assert added.status_code == 201, added.text
    term_id = added.json()["items"][0]["id"]
    assert client.get(f"/api/projects/{project_id}/glossary").json()[0]["id"] == term_id

    csv_import = client.post(
        f"/api/projects/{project_id}/glossary",
        files={
            "file": (
                "terms.csv",
                (
                    "source_term,target_term,note,enabled\n"
                    'Chapter,章,"第一行\n第二行",true\n'
                ).encode(),
                "text/csv",
            )
        },
    )
    assert csv_import.status_code == 201, csv_import.text
    assert csv_import.json()["items"][0]["note"] == "第一行\n第二行"

    qa = client.get(f"/api/projects/{project_id}/qa")
    assert qa.status_code == 200
    assert qa.json()["counts"]["error"] >= 1

    bulk = client.post(
        f"/api/projects/{project_id}/segments/bulk",
        json={"action": "set_pending", "segment_ids": [first_id]},
    )
    assert bulk.status_code == 200
    assert bulk.json()["updated"] == 1
    reset_segment = client.get(f"/api/segments/{first_id}").json()
    assert reset_segment["status"] == "pending"
    assert reset_segment["target_text"] is None

    assert client.delete(f"/api/glossary/{term_id}").status_code == 204


def test_bulk_reset_is_rejected_while_translation_is_active(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = int(upload_markdown(client)["id"])
    segment_id = client.get(f"/api/projects/{project_id}/segments").json()["items"][0]["id"]
    monkeypatch.setattr(translation_tasks, "running", lambda _project_id: True)

    for action in ("set_pending", "retranslate"):
        response = client.post(
            f"/api/projects/{project_id}/segments/bulk",
            json={
                "action": action,
                "segment_ids": [segment_id],
                "start_translation": False,
            },
        )
        assert response.status_code == 409


def test_epub_upload_rejects_suspicious_compression(client: TestClient) -> None:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("OEBPS/oversized.xhtml", b"0" * (2 * 1024 * 1024))

    response = client.post(
        "/api/projects",
        files={"file": ("suspicious.epub", payload.getvalue(), "application/epub+zip")},
    )
    assert response.status_code == 422
    assert "suspiciously compressed" in response.text


def test_translation_task_uses_injected_runner(client: TestClient) -> None:
    project_id = int(upload_markdown(client)["id"])

    async def mock_runner(project: int, stop_event: object, retry: bool, callback: object) -> None:
        del stop_event, retry
        with adapters.session_factory() as session:
            rows = session.exec(
                select(Segment).where(Segment.project_id == project)
            ).all()
            for row in rows:
                row.target_text = f"译：{row.source_text}"
                row.status = "done"
                row.updated_at = utc_now()
                session.add(row)
            session.commit()
        await callback({"type": "progress", "done": len(rows), "total": len(rows)})

    adapters.set_translation_runner(mock_runner)
    started = client.post(f"/api/projects/{project_id}/translate", json={})
    assert started.status_code == 202
    assert started.json()["running"] is True

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        detail = client.get(f"/api/projects/{project_id}").json()
        if detail["status"] == "done":
            break
        time.sleep(0.01)
    assert detail["status"] == "done"
    assert detail["progress"]["completed"] == detail["progress"]["total"]


def test_markdown_export_and_download(client: TestClient) -> None:
    project_id = int(upload_markdown(client)["id"])
    segments = client.get(f"/api/projects/{project_id}/segments").json()["items"]
    for segment in segments:
        response = client.patch(
            f"/api/segments/{segment['id']}",
            json={"target_text": f"译文-{segment['id']}"},
        )
        assert response.status_code == 200

    exported = client.post(
        f"/api/projects/{project_id}/export",
        json={
            "mode": "bilingual",
            "include_untranslated": True,
            "format": "md",
        },
    )
    assert exported.status_code == 200, exported.text
    download = client.get(exported.json()["download_url"])
    assert download.status_code == 200
    assert "译文-" in download.text


def test_deleting_project_does_not_remove_another_projects_export(
    client: TestClient,
) -> None:
    first_id = int(upload_markdown(client)["id"])
    second = client.post(
        "/api/projects",
        files={"file": ("second.md", b"# Second\n\nKeep this export.")},
        data={"title": f"other-p{first_id}-title"},
    )
    assert second.status_code == 201, second.text
    second_id = int(second.json()["id"])
    exported = client.post(
        f"/api/projects/{second_id}/export",
        json={"mode": "bilingual", "include_untranslated": True, "format": "md"},
    )
    assert exported.status_code == 200, exported.text
    second_export = Path(exported.json()["path"])
    assert second_export.is_file()

    assert client.delete(f"/api/projects/{first_id}").status_code == 204
    assert second_export.is_file()


def test_single_retranslate_forces_provider_and_scopes_segment(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.app.engine import translator as core_translator

    class MockProvider:
        async def translate(self, text: str, **kwargs: object) -> TranslationResult:
            del kwargs
            return TranslationResult(
                text=f"强制译文：{text}",
                token_in=7,
                token_out=5,
                model="mock/forced",
            )

    monkeypatch.setattr(core_translator, "LiteLLMProvider", MockProvider)
    project_id = int(upload_markdown(client)["id"])
    segments = client.get(f"/api/projects/{project_id}/segments").json()["items"]
    selected = segments[0]
    untouched = segments[1]
    assert client.patch(
        f"/api/segments/{selected['id']}",
        json={"target_text": "旧译文"},
    ).status_code == 200

    with adapters.session_factory() as session:
        source = session.get(Segment, selected["id"])
        assert source is not None
        cached = session.exec(
            select(TMEntry).where(
                TMEntry.src_hash == source.src_hash,
                TMEntry.source_lang == "en",
                TMEntry.target_lang == "zh-CN",
            )
        ).one()
        cached.target_text = "不应复用的缓存"
        session.add(cached)
        session.commit()

    history = event_broker.history(project_id)
    cursor = history[-1].id if history else 0
    response = client.post(f"/api/segments/{selected['id']}/retranslate")
    assert response.status_code == 202, response.text
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        translated = client.get(f"/api/segments/{selected['id']}").json()
        if translated["status"] == "done" and translated["target_text"] != "旧译文":
            break
        time.sleep(0.01)
    assert translated["target_text"].startswith("强制译文："), (
        translated,
        client.get(f"/api/projects/{project_id}").json(),
    )
    assert translated["provider"] == "mock/forced"
    assert client.get(f"/api/segments/{untouched['id']}").json()["status"] == "pending"
    progress_events = [
        event.as_dict()
        for event in event_broker.history(project_id, cursor)
        if event.type == "progress"
    ]
    assert any(
        event.get("batch_total") == 1 and event.get("total") == len(segments)
        for event in progress_events
    )


def test_openapi_exposes_sse_and_export(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/projects/{project_id}/stream" in paths
    assert "/api/projects/{project_id}/export" in paths
    assert "/api/projects/{project_id}/segments/bulk" in paths


@pytest.mark.asyncio
async def test_sse_history_replays_only_with_explicit_cursor() -> None:
    project_id = 987_654_321
    first = await event_broker.publish(project_id, "segment_done", segment_id=1)
    second = await event_broker.publish(project_id, "completed", done=1, total=1)
    assert _replay_events(project_id, None) == []
    assert _replay_events(project_id, first.id) == [second]


def test_alembic_initial_revision_is_stamped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alembic import command
    from alembic.config import Config

    database = tmp_path / "migration.db"
    monkeypatch.setenv("TRANS_DATABASE_URL", f"sqlite:///{database.as_posix()}")
    get_settings.cache_clear()
    try:
        config = Config(str(REPOSITORY_ROOT / "backend" / "alembic.ini"))
        monkeypatch.chdir(tmp_path)
        migrate_db(f"sqlite:///{database.as_posix()}")
        connection = sqlite3.connect(database)
        try:
            version = connection.execute(
                "select version_num from alembic_version"
            ).fetchone()
        finally:
            connection.close()
        assert version == ("0002_server_foundation",)
        command.check(config)
        command.downgrade(config, "base")
    finally:
        get_settings.cache_clear()


def test_migrate_db_adopts_complete_unversioned_sqlmodel_schema(
    tmp_path: Path,
) -> None:
    database = tmp_path / "legacy.db"
    url = f"sqlite:///{database.as_posix()}"
    legacy_engine = create_engine(url)
    SQLModel.metadata.create_all(legacy_engine)
    legacy_engine.dispose()

    migrate_db(url)
    connection = sqlite3.connect(database)
    try:
        version = connection.execute(
            "select version_num from alembic_version"
        ).fetchone()
    finally:
        connection.close()
    assert version == ("0002_server_foundation",)
    database = tmp_path / "partial.db"
    connection = sqlite3.connect(database)
    try:
        connection.execute("create table project (id integer primary key)")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(RuntimeError, match="cannot be safely adopted"):
        migrate_db(f"sqlite:///{database.as_posix()}")


def test_translation_memory_stats_and_entries(client: TestClient) -> None:
    project_id = int(upload_markdown(client)["id"])
    page = client.get(f"/api/projects/{project_id}/segments").json()
    segment_id = page["items"][0]["id"]
    with adapters.session_factory() as session:
        source_segment = session.get(Segment, segment_id)
        assert source_segment is not None
        session.add(
            TMEntry(
                src_hash=source_segment.src_hash,
                source_lang="en",
                target_lang="zh-CN",
                source_text=source_segment.source_text,
                target_text="缓存译文",
                hit_count=3,
            )
        )
        session.commit()

    stats = client.get(f"/api/projects/{project_id}/tm/stats")
    assert stats.status_code == 200
    assert stats.json()["global_entries"] == 1
    assert stats.json()["language_pair_entries"] == 1
    assert stats.json()["total_hits"] == 3
    assert stats.json()["project_tm_matches"] == 1

    entries = client.get(f"/api/projects/{project_id}/tm")
    assert entries.status_code == 200
    assert entries.json()["total"] == 1
    assert entries.json()["items"][0]["target_text"] == "缓存译文"


def test_job_failure_requeues_until_attempts_exhausted(client: TestClient) -> None:
    from datetime import UTC, datetime, timedelta

    from backend.app.jobs.manager import job_manager
    from backend.app.models import Job

    project_id = int(upload_markdown(client)["id"])
    with adapters.session_factory() as session:
        job = Job(
            project_id=project_id,
            job_type="translate",
            status="running",
            attempt_count=1,
            max_attempts=3,
            lease_owner="test-owner",
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = job.id
    assert job_id is not None

    job_manager._fail_or_requeue(job_id, "job_execution_failed", "boom")
    with adapters.session_factory() as session:
        requeued = session.get(Job, job_id)
        assert requeued is not None
        assert requeued.status == "queued"
        assert requeued.finished_at is None
        assert requeued.lease_owner is None

    # A queued retry inside its backoff window must not be claimable yet.
    assert job_manager._claim_next() is None

    # Age the retry past its backoff floor and it becomes claimable again.
    aged = (datetime.now(UTC) - timedelta(seconds=120)).isoformat(timespec="milliseconds")
    with adapters.session_factory() as session:
        record = session.get(Job, job_id)
        assert record is not None
        record.updated_at = aged
        session.add(record)
        session.commit()
    assert job_manager._claim_next() == job_id

    # Exhausting max_attempts fails the job terminally.
    with adapters.session_factory() as session:
        record = session.get(Job, job_id)
        assert record is not None
        record.attempt_count = record.max_attempts
        session.add(record)
        session.commit()
    job_manager._fail_or_requeue(job_id, "job_execution_failed", "boom")
    with adapters.session_factory() as session:
        failed = session.get(Job, job_id)
        assert failed is not None
        assert failed.status == "failed"
        assert failed.finished_at is not None


def test_storage_maintenance_prunes_expired_audit_events(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime, timedelta

    from backend.app import main as main_module
    from backend.app.models import AuditEvent

    del client  # fixture wires adapters.session_factory to the test engine
    stale = (datetime.now(UTC) - timedelta(days=120)).isoformat(timespec="milliseconds")
    with adapters.session_factory() as session:
        session.add(
            AuditEvent(
                actor_type="system",
                event_type="login",
                result="succeeded",
                created_at=stale,
            )
        )
        session.add(
            AuditEvent(actor_type="system", event_type="login", result="succeeded")
        )
        session.commit()

    monkeypatch.setattr(main_module, "session_factory", adapters.session_factory)
    monkeypatch.setattr(main_module, "checkpoint_wal", lambda: None)
    assert main_module.run_storage_maintenance(audit_retention_days=90) == 1
    with adapters.session_factory() as session:
        remaining = session.exec(select(AuditEvent)).all()
        assert len(remaining) == 1
        assert remaining[0].created_at > stale


def test_health_reports_database_failure(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.app import main as main_module

    def broken_session() -> None:
        raise RuntimeError("database is gone")

    monkeypatch.setattr(main_module, "session_factory", broken_session)
    assert client.get("/health").status_code == 503
