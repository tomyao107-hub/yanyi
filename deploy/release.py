#!/usr/bin/env python3
"""服务器端发布管理工具（安装 / 回滚 / 状态 / 历史）。

在 compose 目录旁运行（默认 ``compose.yml`` + ``compose.6020.yml`` +
``.env.production``）。仅使用标准库，依赖宿主机 docker 命令。

命令:
    init      初始化 .releases/manifest.json（幂等）
    install   <archive> [--version V] [--git-sha S] [--no-migrate]
    rollback   [<版本>] [--no-migrate]
    status    查看版本 / schema / tag 漂移 / 备份
    history   查看 manifest 操作记录

安全护栏（代码强制）:
    * 目标版本 == 当前版本时拒绝执行（no-op）
    * 任何 schema 变更或 tag 切换前必须已有非空备份，否则硬中止
    * 绝不执行 down --volumes；绝不写入 /etc/trans（master-key 不动）
    * 不因 environment=development 拒绝执行（6020 主机即 dev 模式）
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

DEFAULT_COMPOSE_FILE = "compose.yml"
DEFAULT_OVERRIDE_FILE = "compose.6020.yml"
DEFAULT_ENV_FILE = ".env.production"
RELEASE_DIR_NAME = ".releases"
BACKUP_DIR_NAME = "backups"
MANIFEST_FILE_NAME = "manifest.json"
LOCK_FILE_NAME = "lock"
IMAGE_NAME = "trans-linux"
SCHEMA_VERSION = 1
OPERATIONS_KEEP = 50
HEALTH_TIMEOUT = 90
BACKUP_SUFFIXES = ("backup-*.tar.gz",)

PY_READ_REV = """\
import os, sqlite3
path = "/var/lib/trans/trans.db"
if not os.path.exists(path):
    print("__MISSING__")
else:
    con = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    try:
        row = con.execute("SELECT version_num FROM alembic_version").fetchone()
        print(row[0] if row else "")
    finally:
        con.close()
"""

PY_SCHEMA_HEAD = """\
import sys
sys.path.insert(0, "/app")
from alembic.config import Config
from alembic.script import ScriptDirectory
script = ScriptDirectory.from_config(Config("/app/backend/alembic.ini"))
heads = script.get_heads()
assert len(heads) == 1, "expected exactly one alembic head"
print(heads[0])
"""

PY_VERSION_JSON = """\
import json, pathlib
try:
    data = json.loads(pathlib.Path("/app/version.json").read_text())
    print(json.dumps(data))
except Exception:
    print("{}")
"""


class ReleaseError(Exception):
    """用户可读的操作失败。"""


def log(message: str) -> None:
    print(f"[release] {message}", flush=True)


def utcnow() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run(cmd: list[str], *, capture: bool = False, cwd: str | None = None) -> str | None:
    try:
        result = subprocess.run(cmd, capture_output=capture, text=True, cwd=cwd)
    except FileNotFoundError as exc:
        raise ReleaseError(f"未找到命令 {cmd[0]}（{exc}）") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise ReleaseError(f"命令失败: {' '.join(cmd)}\n{detail}")
    return result.stdout if capture else None


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(str(tmp), str(path))


def _env_file_value(path: str, key: str) -> str | None:
    p = Path(path)
    if not p.exists():
        return None
    for line in p.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith(key + "="):
            value = stripped.split("=", 1)[1].strip()
            return value.strip('"').strip("'")
    return None


class Context:
    def __init__(
        self,
        *,
        compose_file: str,
        override_file: str | None,
        env_file: str,
        no_migrate: bool,
        root: Path,
    ) -> None:
        self.compose_file = compose_file
        self.override_file = override_file
        self.env_file = env_file
        self.no_migrate = no_migrate
        self.root = root
        self.releases_dir = root / RELEASE_DIR_NAME
        self.backups_dir = root / RELEASE_DIR_NAME / BACKUP_DIR_NAME
        self.manifest_path = root / RELEASE_DIR_NAME / MANIFEST_FILE_NAME
        self.lock_path = root / RELEASE_DIR_NAME / LOCK_FILE_NAME
        self.volume: str | None = None
        self._config: dict | None = None

    @property
    def compose_args(self) -> list[str]:
        args = ["docker", "compose", "--env-file", self.env_file, "-f", self.compose_file]
        if self.override_file:
            args += ["-f", self.override_file]
        return args


# ---------------------------------------------------------------------------
# docker compose 探测
# ---------------------------------------------------------------------------


def _compose_config(ctx: Context) -> dict:
    if ctx._config is None:
        out = _run([*ctx.compose_args, "config", "--format", "json"], capture=True)
        ctx._config = json.loads(out)
    return ctx._config


def _detect_names(ctx: Context, config: dict) -> tuple[str, str]:
    # compose v2 `config --format json` emits the project name under `name`.
    project = (
        config.get("project")
        or config.get("name")
        or os.environ.get("TRANS_PROJECT_NAME")
        or "trans-production"
    )
    volume = os.environ.get("TRANS_VOLUME_NAME")
    if not volume:
        app = config.get("services", {}).get("app", {})
        mounts = app.get("volumes") or []
        mount = next((m for m in mounts if m.get("target") == "/var/lib/trans"), None)
        local = (mount or {}).get("source") or "trans-state"
        if local.startswith(f"{project}_"):
            volume = local
        else:
            qualified = f"{project}_{local}"
            volume = qualified
            for key, info in config.get("volumes", {}).items():
                name = (info or {}).get("name") or key
                if name == qualified or name.endswith(f"_{local}"):
                    volume = name
                    break
    if not volume:
        raise ReleaseError("无法确定 trans-state 卷名，请设置 TRANS_VOLUME_NAME 环境变量")
    return project, volume


def _ensure_volume(ctx: Context) -> str:
    if not ctx.volume:
        _, volume = _detect_names(ctx, _compose_config(ctx))
        ctx.volume = volume
    return ctx.volume


def _ps_app(ctx: Context) -> str | None:
    out = _run([*ctx.compose_args, "ps", "-q", "app"], capture=True)
    lines = [line.strip() for line in out.splitlines() if line.strip()]
    return lines[0] if lines else None


def _container_image_tag(ctx: Context) -> str | None:
    cid = _ps_app(ctx)
    if not cid:
        return None
    ref = _run(["docker", "inspect", "-f", "{{index .Config.Image}}", cid], capture=True).strip()
    return ref.rsplit(":", 1)[-1] if ":" in ref else ref


def _published_http_port(ctx: Context) -> int | None:
    config = _compose_config(ctx)
    caddy = config.get("services", {}).get("caddy", {})
    for port in caddy.get("ports") or []:
        if port.get("protocol") == "udp":
            continue
        published = port.get("published")
        if published is None:
            continue
        try:
            return int(published)
        except (TypeError, ValueError):
            continue
    return None


# ---------------------------------------------------------------------------
# 容器内只读探测与迁移
# ---------------------------------------------------------------------------


def _image_identity(image: str) -> dict | None:
    out = _run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--entrypoint",
            "python",
            f"{IMAGE_NAME}:{image}",
            "-c",
            PY_VERSION_JSON,
        ],
        capture=True,
    )
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def _image_schema_head(image: str) -> str | None:
    out = _run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--workdir",
            "/app",
            "--entrypoint",
            "python",
            f"{IMAGE_NAME}:{image}",
            "-c",
            PY_SCHEMA_HEAD,
        ],
        capture=True,
    )
    return out.strip() or None


def _read_db_revision(ctx: Context, image: str) -> str | None:
    volume = _ensure_volume(ctx)
    out = _run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "-v",
            f"{volume}:/var/lib/trans",
            "--entrypoint",
            "python",
            f"{IMAGE_NAME}:{image}",
            "-c",
            PY_READ_REV,
        ],
        capture=True,
    ).strip()
    if out == "__MISSING__":
        return None
    return out or None


def _alembic(ctx: Context, image: str, command: str, target: str) -> None:
    volume = _ensure_volume(ctx)
    _run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "-v",
            f"{volume}:/var/lib/trans",
            "--env",
            "TRANS_DATABASE_URL=sqlite:////var/lib/trans/trans.db",
            "--workdir",
            "/app",
            "--entrypoint",
            "python",
            f"{IMAGE_NAME}:{image}",
            "-m",
            "alembic",
            "-c",
            "/app/backend/alembic.ini",
            command,
            target,
        ]
    )


def _backup(ctx: Context, image: str, label: str) -> Path:
    """对 trans-state 卷做只增 tar 备份；缺失或 0 字节则硬中止。"""
    volume = _ensure_volume(ctx)
    ctx.backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    name = f"backup-{stamp}-{label}.tar.gz"
    dest = ctx.backups_dir / name
    _run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "-v",
            f"{volume}:/var/lib/trans",
            "-v",
            f"{str(ctx.backups_dir.resolve())}:/backups",
            "--user",
            "root",
            "--entrypoint",
            "tar",
            f"{IMAGE_NAME}:{image}",
            "-czf",
            f"/backups/{name}",
            "-C",
            "/var/lib/trans",
            ".",
        ]
    )
    if not dest.exists() or dest.stat().st_size == 0:
        raise ReleaseError(f"备份文件缺失或为空，中止操作: {dest}")
    return dest


# ---------------------------------------------------------------------------
# tag 切换、容器重建、健康检查
# ---------------------------------------------------------------------------


def _switch_tag(ctx: Context, version: str) -> None:
    path = Path(ctx.env_file)
    if not path.exists():
        raise ReleaseError(
            f"未找到环境文件 {ctx.env_file}，无法切换 TRANS_IMAGE_TAG。"
            "请提供包含 TRANS_IMAGE_TAG 的 --env-file"
        )
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    found = False
    for line in lines:
        if line.strip().startswith("TRANS_IMAGE_TAG"):
            out.append(f"TRANS_IMAGE_TAG={version}")
            found = True
        else:
            out.append(line)
    if not found:
        if out and out[-1] != "":
            out.append("")
        out.append(f"TRANS_IMAGE_TAG={version}")
    _atomic_write(path, "\n".join(out) + "\n")
    log(f"TRANS_IMAGE_TAG → {version}（{ctx.env_file}）")


def _up(ctx: Context) -> None:
    _run([*ctx.compose_args, "up", "-d", "app"])


def _stop(ctx: Context) -> None:
    # Quiesce SQLite and the durable worker before copying the state volume or
    # changing schema. A tar of a live WAL database is not a consistent backup.
    _run([*ctx.compose_args, "stop", "app"])


def _health_check(ctx: Context, expected_sha: str | None = None) -> None:
    cid = _ps_app(ctx)
    if not cid:
        raise ReleaseError("找不到 app 容器，健康检查无法进行")
    deadline = time.monotonic() + HEALTH_TIMEOUT
    last = "starting"
    while time.monotonic() < deadline:
        status = _run(
            ["docker", "inspect", "-f", "{{.State.Health.Status}}", cid], capture=True
        ).strip()
        last = status
        if status == "healthy":
            if expected_sha:
                _crosscheck_git_sha(ctx, expected_sha)
            log("健康检查通过")
            return
        if status == "unhealthy":
            raise ReleaseError("健康检查失败（unhealthy）")
        time.sleep(2)
    raise ReleaseError(f"健康检查超时（{HEALTH_TIMEOUT}s），最后状态: {last}")


def _crosscheck_git_sha(ctx: Context, expected_sha: str) -> None:
    cid = _ps_app(ctx)
    if not cid:
        raise ReleaseError("找不到 app 容器，无法校验 git_sha")
    payload = json.loads(
        _run(["docker", "exec", cid, "python", "-c", PY_VERSION_JSON], capture=True)
    )
    actual = (payload or {}).get("git_sha")
    if actual != expected_sha:
        raise ReleaseError(f"git_sha 校验失败: 预期 {expected_sha}，实际 {actual}")


# ---------------------------------------------------------------------------
# 归档解压
# ---------------------------------------------------------------------------


def _safe_extract_legacy(tar: tarfile.TarFile, dest: Path) -> None:
    dest = dest.resolve()
    for member in tar.getmembers():
        target = (dest / member.name).resolve()
        if not target.is_relative_to(dest):
            raise ReleaseError(f"归档成员越界: {member.name}")
        if member.issym() or member.islnk():
            link = Path(member.linkname)
            if link.is_absolute() or not (dest / member.linkname).resolve().is_relative_to(dest):
                raise ReleaseError(f"归档链接逃逸: {member.name} -> {member.linkname}")
    tar.extractall(dest)


def _extract_archive(archive: Path, dest: Path) -> None:
    dest = Path(dest)
    tmp = dest.parent / f".extract-{int(time.time())}-{os.getpid()}"
    tmp.mkdir(parents=True)
    try:
        with tarfile.open(archive, "r:*") as tar:
            try:
                tar.extractall(tmp, filter="data")
            except tarfile.TarError as exc:
                raise ReleaseError(f"归档不安全，拒绝解压: {exc}") from exc
            except TypeError:
                _safe_extract_legacy(tar, tmp)
        children = [child for child in tmp.iterdir()]
        wrapper = None
        if (
            len(children) == 1
            and children[0].is_dir()
            and children[0].name in ("src", "trans-linux")
        ):
            wrapper = children[0]
        source = wrapper or tmp
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        for child in source.iterdir():
            shutil.move(str(child), str(dest / child.name))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _archive_version(archive: Path) -> str:
    match = re.match(r"^trans-(.+)\.(tar\.gz|tgz|tar|tar\.bz2|tbz2|tar\.xz|txz)$", archive.name)
    if match:
        return match.group(1)
    try:
        with tarfile.open(archive, "r:*") as tar:
            for member in tar.getmembers():
                if member.isfile() and member.name.replace("\\", "/").endswith("/version.json"):
                    stream = tar.extractfile(member)
                    if stream:
                        data = json.load(stream)
                        if data.get("version"):
                            return str(data["version"])
    except (tarfile.TarError, json.JSONDecodeError, KeyError):
        pass
    raise ReleaseError("无法从归档确定版本号，请使用 --version")


def _validate_version(version: str) -> None:
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$", version):
        raise ReleaseError(f"非法版本号: {version}")


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------


def _load_manifest(ctx: Context) -> dict:
    if not ctx.manifest_path.exists():
        raise ReleaseError(f"未找到 manifest（{ctx.manifest_path}），请先运行 init")
    return json.loads(ctx.manifest_path.read_text(encoding="utf-8"))


def _save_manifest(ctx: Context, manifest: dict) -> None:
    _atomic_write(ctx.manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


def _record_operation(
    manifest: dict, op: str, version: str | None, result: str, detail: str
) -> None:
    operations = manifest.setdefault("operations", [])
    operations.append(
        {
            "ts": utcnow(),
            "op": op,
            "version": version,
            "result": result,
            "detail": detail,
        }
    )
    manifest["operations"] = operations[-OPERATIONS_KEEP:]


def _previous_installed(manifest: dict, current: str | None) -> str | None:
    candidates: list[tuple[str, str]] = []
    for version, entry in manifest.get("releases", {}).items():
        if version == current:
            continue
        installed = entry.get("installed_at")
        if installed:
            candidates.append((installed, version))
    candidates.sort(reverse=True)
    return candidates[0][1] if candidates else None


@contextlib.contextmanager
def _release_lock(ctx: Context):
    ctx.releases_dir.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(ctx.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ReleaseError(f"已有 release.py 正在运行（{ctx.lock_path} 已存在）") from exc
    try:
        os.write(fd, str(os.getpid()).encode())
        yield
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            ctx.lock_path.unlink()
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------------------
# 命令
# ---------------------------------------------------------------------------


def cmd_init(ctx: Context) -> int:
    ctx.releases_dir.mkdir(parents=True, exist_ok=True)
    ctx.backups_dir.mkdir(parents=True, exist_ok=True)
    if ctx.manifest_path.exists():
        manifest = _load_manifest(ctx)
        log(f"manifest 已存在（幂等，跳过），当前版本: {manifest.get('current')}")
        return 0
    config = _compose_config(ctx)
    project, volume = _detect_names(ctx, config)
    env_tag = _env_file_value(ctx.env_file, "TRANS_IMAGE_TAG") or "latest"
    running = _container_image_tag(ctx) or env_tag
    identity: dict | None = None
    try:
        identity = _image_identity(running)
    except ReleaseError:
        pass
    releases: dict = {}
    current: str | None = None
    detail = f"env_tag={env_tag} running_tag={running}"
    if identity:
        version = str(identity.get("version") or running)
        head = _image_schema_head(running)
        db = _read_db_revision(ctx, running)
        now = utcnow()
        releases[version] = {
            "version": version,
            "git_sha": identity.get("git_sha"),
            "built_at": identity.get("build_time"),
            "schema_revision": head,
            "archive": None,
            "installed_at": now,
            "tag": running,
        }
        current = version
        detail += f" db={db} head={head}"
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "created_at": utcnow(),
        "compose_file": ctx.compose_file,
        "override_file": ctx.override_file,
        "project_name": project,
        "volume_name": volume,
        "image": IMAGE_NAME,
        "current": current,
        "releases": releases,
        "operations": [
            {
                "ts": utcnow(),
                "op": "init",
                "version": current,
                "result": "ok",
                "detail": detail,
            }
        ],
    }
    _save_manifest(ctx, manifest)
    log(f"已初始化 manifest（current={current or '无'}, 卷={volume}）")
    return 0


def cmd_status(ctx: Context) -> int:
    config = _compose_config(ctx)
    project, volume = _detect_names(ctx, config)
    env_tag = _env_file_value(ctx.env_file, "TRANS_IMAGE_TAG") or "latest"
    running = _container_image_tag(ctx)
    print(f"项目名: {project}")
    print(f"trans-state 卷: {volume}")
    print(f"env-file TRANS_IMAGE_TAG: {env_tag}")
    print(f"运行中镜像 tag: {running or '（未运行）'}")
    if ctx.manifest_path.exists():
        manifest = _load_manifest(ctx)
        print(f"manifest 当前版本: {manifest.get('current') or '（无）'}")
    if running and running != env_tag:
        print("[警告] env-file tag 与运行中镜像不一致（tag 漂移）")
    if not running:
        print("应用未运行，跳过镜像探测。")
        return 0
    identity = _image_identity(running) or {}
    print(f"版本: {identity.get('version') or running}")
    if identity.get("git_sha"):
        print(f"git_sha: {identity['git_sha']}")
    try:
        head = _image_schema_head(running)
        print(f"镜像迁移头: {head}")
    except ReleaseError as exc:
        head = None
        print(f"镜像迁移头: （读取失败: {exc}）")
    db = _read_db_revision(ctx, running)
    print(f"数据库当前修订: {db or '（未初始化）'}")
    if head and db:
        print(f"schema 一致: {'是' if head == db else '否'}")
    print("\n--- docker compose ps ---")
    _run([*ctx.compose_args, "ps"])
    backups = sorted(ctx.backups_dir.glob("backup-*.tar.gz"))[-5:]
    print("\n最近备份:")
    for backup in backups:
        print(f"  {backup.name}  {backup.stat().st_size} bytes")
    if not backups:
        print("  （无备份）")
    return 0


def cmd_history(ctx: Context) -> int:
    manifest = _load_manifest(ctx)
    operations = list(reversed(manifest.get("operations", [])))
    if not operations:
        print("暂无操作记录。")
        return 0
    print(f"{'时间':<22} {'操作':<9} {'版本':<12} {'结果':<8} 详情")
    for op in operations:
        print(
            f"{str(op.get('ts')):<22} {str(op.get('op')):<9} "
            f"{str(op.get('version') or '-'):<12} {str(op.get('result')):<8} "
            f"{op.get('detail') or ''}"
        )
    return 0


def cmd_install(ctx: Context, archive: Path, version: str, git_sha: str) -> int:
    _validate_version(version)
    manifest = _load_manifest(ctx)
    current = manifest.get("current")
    if current and version == current:
        raise ReleaseError(f"版本 {version} 就是当前版本，无需安装")
    if not archive.exists():
        raise ReleaseError(f"归档不存在: {archive}")

    src_dir = ctx.releases_dir / version / "src"
    log(f"解压归档 {archive.name} → {src_dir}")
    _extract_archive(archive, src_dir)
    if not (src_dir / "Dockerfile").exists():
        raise ReleaseError("归档内缺少 Dockerfile（应位于 src/Dockerfile）")

    built_at = utcnow()
    log(f"构建镜像 {IMAGE_NAME}:{version}（git_sha={git_sha}）")
    _run(
        [
            "docker",
            "build",
            "-f",
            str(src_dir / "Dockerfile"),
            "-t",
            f"{IMAGE_NAME}:{version}",
            "--build-arg",
            f"VERSION={version}",
            "--build-arg",
            f"GIT_SHA={git_sha}",
            "--build-arg",
            f"BUILD_TIME={built_at}",
            str(src_dir),
        ]
    )
    new_head = _image_schema_head(version)
    log(f"新镜像迁移头: {new_head}")

    old_tag = None
    if current and current in manifest.get("releases", {}):
        old_tag = manifest["releases"][current].get("tag") or current

    backup_path: Path | None = None
    changed = False
    stopped = False
    try:
        _stop(ctx)
        stopped = True
        backup_path = _backup(ctx, old_tag or version, version)
        log(f"已备份（只增）: {backup_path.name}")
        changed = True
        if not ctx.no_migrate:
            _alembic(ctx, version, "upgrade", "head")
            log("数据库已迁移至 head")
        _switch_tag(ctx, version)
        _up(ctx)
        expected_sha = git_sha if git_sha and git_sha != "unknown" else None
        _health_check(ctx, expected_sha)
    except Exception as exc:
        _record_operation(manifest, "install", version, "failed", str(exc))
        _save_manifest(ctx, manifest)
        log(f"安装失败: {exc}")
        if stopped and old_tag and current:
            try:
                log("自动回滚…")
                _stop(ctx)
                if changed and not ctx.no_migrate:
                    old_rev = manifest["releases"][current].get("schema_revision")
                    db_rev = _read_db_revision(ctx, version)
                    if old_rev and db_rev and db_rev != old_rev:
                        _alembic(ctx, version, "downgrade", old_rev)
                        log(f"数据库已降级至 {old_rev}")
                _switch_tag(ctx, old_tag)
                _up(ctx)
                current_sha = manifest["releases"][current].get("git_sha")
                _health_check(
                    ctx, current_sha if current_sha and current_sha != "unknown" else None
                )
                log("已回滚至原版本")
            except Exception as rollback_exc:
                print(f"[release] 自动回滚失败: {rollback_exc}", file=sys.stderr)
                if backup_path:
                    print(f"[release] 备份保留在: {backup_path}", file=sys.stderr)
                return 1
        elif stopped:
            # Even an unversioned installation may have been running before
            # init. Restore its compose service if backup/migration fails.
            _up(ctx)
        return 1

    manifest["releases"][version] = {
        "version": version,
        "git_sha": git_sha,
        "built_at": built_at,
        "schema_revision": new_head,
        "archive": archive.name,
        "installed_at": utcnow(),
        "tag": version,
    }
    manifest["current"] = version
    _record_operation(
        manifest,
        "install",
        version,
        "ok",
        f"backup={backup_path.name if backup_path else 'none'}",
    )
    _save_manifest(ctx, manifest)
    log(f"安装成功，当前版本 {version}")
    return 0


def cmd_rollback(ctx: Context, target: str | None) -> int:
    manifest = _load_manifest(ctx)
    current = manifest.get("current")
    if not current:
        raise ReleaseError("当前无版本记录，无法回滚")
    releases = manifest.get("releases", {})
    if not target:
        target = _previous_installed(manifest, current)
        if not target:
            raise ReleaseError("没有可回滚的旧版本")
    _validate_version(target)
    if target == current:
        raise ReleaseError(f"版本 {target} 就是当前版本，无需回滚")
    if target not in releases:
        raise ReleaseError(f"manifest 中无版本 {target}（可执行 history 查看）")
    entry = releases[target]
    tag = entry.get("tag") or target
    current_entry = releases.get(current, {})
    current_tag = current_entry.get("tag") or current
    current_sha = current_entry.get("git_sha")

    backup_path: Path | None = None
    schema_changed = False
    try:
        _stop(ctx)
        backup_path = _backup(ctx, current_tag, target)
        log(f"已备份（只增）: {backup_path.name}")
        if not ctx.no_migrate:
            db_rev = _read_db_revision(ctx, current_tag)
            target_rev = entry.get("schema_revision")
            if not target_rev:
                raise ReleaseError(f"目标版本 {target} 缺少 schema_revision，请使用 --no-migrate")
            if db_rev and db_rev != target_rev:
                schema_changed = True
                _alembic(ctx, current_tag, "downgrade", target_rev)
                log(f"数据库已降级至 {target_rev}")
        _switch_tag(ctx, tag)
        _up(ctx)
        _health_check(ctx, entry.get("git_sha") if entry.get("git_sha") != "unknown" else None)
    except Exception as exc:
        _record_operation(manifest, "rollback", target, "failed", str(exc))
        _save_manifest(ctx, manifest)
        try:
            _stop(ctx)
            if schema_changed:
                current_rev = current_entry.get("schema_revision")
                if not current_rev:
                    raise ReleaseError("原版本缺少 schema_revision，无法恢复数据库")
                _alembic(ctx, current_tag, "upgrade", current_rev)
            _switch_tag(ctx, current_tag)
            _up(ctx)
            _health_check(ctx, current_sha if current_sha and current_sha != "unknown" else None)
            log("已恢复原版本")
        except Exception as restore_exc:
            print(f"[release] 恢复原版本失败: {restore_exc}", file=sys.stderr)
        print(f"[release] 回滚失败: {exc}", file=sys.stderr)
        if backup_path:
            print(f"[release] 备份保留在: {backup_path}", file=sys.stderr)
        return 1

    manifest["current"] = target
    _record_operation(manifest, "rollback", target, "ok", f"backup={backup_path.name}")
    _save_manifest(ctx, manifest)
    log(f"已回滚至 {target}（tag={tag}）")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="release.py",
        description="砚译服务器端发布管理（安装 / 回滚 / 状态 / 历史）",
    )
    parser.add_argument("--compose-file", default=DEFAULT_COMPOSE_FILE)
    parser.add_argument("--override-file", default=DEFAULT_OVERRIDE_FILE)
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE)
    sub = parser.add_subparsers(dest="command", required=True, metavar="命令")

    sub.add_parser("init", help="初始化 .releases/manifest.json（幂等）")

    install = sub.add_parser("install", help="安装新版本（构建镜像 + 迁移 + 切 tag + 健康检查）")
    install.add_argument("archive", help="trans-<版本>.tar.gz 归档路径")
    install.add_argument("--version", help="显式版本号（否则从归档名或 version.json 推断）")
    install.add_argument("--git-sha", default="unknown", help="构建时写入镜像的 git_sha")
    install.add_argument("--no-migrate", action="store_true", help="跳过数据库迁移")

    rollback = sub.add_parser("rollback", help="回滚到指定版本（缺省为最近安装的前一版本）")
    rollback.add_argument("version", nargs="?", help="目标版本")
    rollback.add_argument("--no-migrate", action="store_true", help="跳过数据库降级")

    sub.add_parser("status", help="查看版本 / schema / tag 漂移 / 备份")
    sub.add_parser("history", help="查看 manifest 操作记录")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = Path.cwd()
    ctx = Context(
        compose_file=args.compose_file,
        override_file=args.override_file if Path(args.override_file).exists() else None,
        env_file=args.env_file,
        no_migrate=getattr(args, "no_migrate", False),
        root=root,
    )
    try:
        if args.command == "init":
            return cmd_init(ctx)
        if args.command == "status":
            return cmd_status(ctx)
        if args.command == "history":
            return cmd_history(ctx)
        if args.command == "install":
            version = args.version or _archive_version(Path(args.archive))
            with _release_lock(ctx):
                return cmd_install(ctx, Path(args.archive), version, args.git_sha)
        if args.command == "rollback":
            with _release_lock(ctx):
                return cmd_rollback(ctx, args.version)
        parser.error(f"未知命令: {args.command}")
    except ReleaseError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
