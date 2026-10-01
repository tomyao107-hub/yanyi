"""Exercise host release orchestration with Docker boundaries simulated."""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path
from typing import Any

import pytest
import yaml

from deploy import release


@pytest.fixture
def deployment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    env = tmp_path / "env"
    env.write_text("TRANS_IMAGE_TAG=old\nOTHER=value\n", encoding="utf-8")
    ctx = release.Context(
        compose_file="compose.yml",
        override_file=None,
        env_file=str(env),
        no_migrate=False,
        root=tmp_path,
    )
    ctx.releases_dir.mkdir()
    manifest = {
        "current": "old",
        "operations": [],
        "releases": {
            "old": {"tag": "old", "schema_revision": "rev_old", "git_sha": "sha_old"},
            "older": {"tag": "older", "schema_revision": "rev_older", "git_sha": "sha_older"},
        },
    }
    release._save_manifest(ctx, manifest)
    archive = tmp_path / "trans-new.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("src/Dockerfile")
        content = b"FROM scratch\n"
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))

    state: dict[str, Any] = {"stopped": False, "revision": "rev_old", "events": []}
    monkeypatch.setattr(release, "_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(release, "_image_schema_head", lambda image: "rev_new")
    monkeypatch.setattr(release, "_read_db_revision", lambda *args: state["revision"])

    def stop(context: release.Context) -> None:
        state["stopped"] = True
        state["events"].append("stop")

    def backup(context: release.Context, image: str, label: str) -> Path:
        assert state["stopped"], "backup must not race a live SQLite writer"
        state["events"].append("backup")
        path = tmp_path / "backup.tar.gz"
        path.write_bytes(b"backup")
        return path

    def migrate(context: release.Context, image: str, command: str, target: str) -> None:
        assert state["stopped"], "schema changes require a quiescent service"
        state["events"].append((command, target))
        state["revision"] = "rev_new" if target == "head" else target

    def up(context: release.Context) -> None:
        state["events"].append("up")
        state["stopped"] = False

    monkeypatch.setattr(release, "_stop", stop)
    monkeypatch.setattr(release, "_backup", backup)
    monkeypatch.setattr(release, "_alembic", migrate)
    monkeypatch.setattr(release, "_up", up)
    monkeypatch.setattr(release, "_health_check", lambda *args: None)
    return ctx, archive, state


def test_install_orders_backup_before_migration(deployment: Any) -> None:
    ctx, archive, state = deployment
    assert release.cmd_install(ctx, archive, "new", "sha_new") == 0
    assert state["events"] == ["stop", "backup", ("upgrade", "head"), "up"]
    assert release._load_manifest(ctx)["current"] == "new"
    assert release._env_file_value(ctx.env_file, "TRANS_IMAGE_TAG") == "new"
    assert release._env_file_value(ctx.env_file, "OTHER") == "value"


def test_install_health_failure_restores_schema_and_tag(
    deployment: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, archive, state = deployment
    calls = 0

    def health(*args: Any) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise release.ReleaseError("unhealthy")

    monkeypatch.setattr(release, "_health_check", health)
    assert release.cmd_install(ctx, archive, "new", "sha_new") == 1
    assert state["revision"] == "rev_old"
    assert state["stopped"] is False
    assert release._env_file_value(ctx.env_file, "TRANS_IMAGE_TAG") == "old"
    assert release._load_manifest(ctx)["current"] == "old"
    assert state["events"].index("backup") < state["events"].index(("upgrade", "head"))


def test_backup_failure_restarts_old_service_without_migration(
    deployment: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, archive, state = deployment

    def fail(*args: Any) -> None:
        raise release.ReleaseError("empty backup")

    monkeypatch.setattr(release, "_backup", fail)
    assert release.cmd_install(ctx, archive, "new", "sha_new") == 1
    assert state["revision"] == "rev_old"
    assert state["stopped"] is False
    assert not any(isinstance(event, tuple) for event in state["events"])


def test_failed_rollback_restores_original_schema_before_restart(
    deployment: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, _, state = deployment
    calls = 0

    def health(*args: Any) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise release.ReleaseError("unhealthy target")

    monkeypatch.setattr(release, "_health_check", health)
    assert release.cmd_rollback(ctx, "older") == 1
    assert state["revision"] == "rev_old"
    assert ("downgrade", "rev_older") in state["events"]
    assert ("upgrade", "rev_old") in state["events"]
    assert release._env_file_value(ctx.env_file, "TRANS_IMAGE_TAG") == "old"
    assert state["stopped"] is False


def test_first_install_also_requires_backup(deployment: Any) -> None:
    ctx, archive, state = deployment
    manifest = release._load_manifest(ctx)
    manifest["current"] = None
    release._save_manifest(ctx, manifest)
    assert release.cmd_install(ctx, archive, "new", "sha_new") == 0
    assert state["events"][:2] == ["stop", "backup"]


def test_same_version_rejected_before_side_effects(deployment: Any) -> None:
    ctx, archive, state = deployment
    with pytest.raises(release.ReleaseError):
        release.cmd_install(ctx, archive, "old", "sha_old")
    assert not state["events"]


@pytest.mark.parametrize("git_sha", [None, "wrong"])
def test_health_identity_missing_or_mismatched_is_rejected(
    deployment: Any,
    monkeypatch: pytest.MonkeyPatch,
    git_sha: str | None,
) -> None:
    ctx, _, _ = deployment
    monkeypatch.setattr(release, "_ps_app", lambda context: "container")
    monkeypatch.setattr(release, "_run", lambda *args, **kwargs: json.dumps({"git_sha": git_sha}))
    with pytest.raises(release.ReleaseError, match="git_sha"):
        release._crosscheck_git_sha(ctx, "expected")


def test_archive_traversal_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("../outside")
        info.size = 1
        tar.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(release.ReleaseError):
        release._extract_archive(archive, tmp_path / "src")
    assert not (tmp_path / "outside").exists()


def test_e2e_compose_override_is_valid_and_isolated() -> None:
    script = (Path(__file__).parents[2] / "deploy" / "test_release_e2e.sh").read_text(
        encoding="utf-8"
    )
    override = script.split("<<'YAML'\n", 1)[1].split("\nYAML", 1)[0]
    config = yaml.safe_load(override.replace("!override", ""))
    assert config["name"] == "trans-e2e"
    app = config["services"]["app"]
    assert app["image"].startswith("${TRANS_RELEASE_IMAGE}:")
    assert not any(service.get("ports") for service in config["services"].values())
    assert {mount["target"] for mount in app["volumes"]} == {
        "/var/lib/trans",
        "/run/secrets/master_key",
        "/run/secrets/admin_bootstrap_password",
    }
