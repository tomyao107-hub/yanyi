#!/usr/bin/env python3
"""在 121.4.28.63 上运行 deploy/test_release_e2e.sh（隔离项目 trans-e2e，不触碰生产栈）。

本地完成:
  1. 把当前工作树打成快照 tar.gz（排除 node_modules/dist/.git/.claude/data 等）
  2. base64 流式上传（服务器 SFTP 不可用，走 exec_command 管道）
  3. 远程解压到 $WORKSPACE/srcbase，运行 E2E 脚本，实时回传输出

用法:
    python deploy/run_e2e_on_server.py                  # 密码从 getpass 交互读取
    python deploy/run_e2e_on_server.py --password '...'
    TRANS_E2E_PASSWORD='...' python deploy/run_e2e_on_server.py
"""

import argparse
import base64
import getpass
import hashlib
import io
import os
import re
import shlex
import sys
import tarfile
import time
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HOST = "121.4.28.63"
DEFAULT_USER = "root"
DEFAULT_PORT = 22
DEFAULT_WORKSPACE = "/opt/trans-e2e-workspace"
DEFAULT_HOST_KEY_SHA256 = "SHA256:BOR17kfKTw0BOxv60oxm8A/Zl5+hkYdbDUmSDys2x18"
REMOTE_TAR = "repo.tar.gz"

EXCLUDED_DIRS = {
    ".git", ".claude", ".releases", "node_modules", "dist", ".vite",
    "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    ".venv", "venv", "data", "exports", "work", ".idea", ".vscode",
}
EXCLUDED_FILES = {"settings.local.toml", ".env.local", "Thumbs.db", ".DS_Store"}


def out(data: bytes) -> None:
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def build_repo_tarball() -> bytes:
    buf = io.BytesIO()
    count = 0
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path in sorted(REPO_ROOT.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(REPO_ROOT)
            parts = rel.parts
            if any(part in EXCLUDED_DIRS for part in parts):
                continue
            name = rel.name
            if name in EXCLUDED_FILES:
                continue
            if name.startswith(".env") and name not in (".env.example", ".env.production.example"):
                continue
            if rel.suffix in (".pyc", ".pyo"):
                continue
            tar.add(path, arcname=rel.as_posix())
            count += 1
    payload = buf.getvalue()
    print(f"[本地] 快照 tar: {len(payload)} 字节, {count} 个文件", flush=True)
    return payload


def validate_workspace(workspace: str) -> PurePosixPath:
    """只允许 /opt 下独立的 E2E 目录，拒绝归一化前后不同的路径。"""
    path = PurePosixPath(workspace)
    if (
        not re.fullmatch(r"/opt/trans-e2e-[A-Za-z0-9][A-Za-z0-9._-]*", workspace)
        or path.as_posix() != workspace
    ):
        raise ValueError("E2E 工作区必须是 /opt/trans-e2e-* 下的独立目录")
    return path


def remote_path(workspace: str, *parts: str) -> str:
    root = validate_workspace(workspace)
    for part in parts:
        candidate = PurePosixPath(part)
        if (
            not part
            or "\\" in part
            or candidate.is_absolute()
            or ".." in candidate.parts
            or candidate.as_posix() != part
        ):
            raise ValueError("远程路径必须是工作区内的 POSIX 相对路径")
    return root.joinpath(*parts).as_posix()


def workspace_guard(workspace: str, *paths: str) -> str:
    """由远端 realpath 检查真实路径；在上传、删除或解压之前执行。"""
    root = validate_workspace(workspace)
    commands = ["set -eu"]
    for value in dict.fromkeys((workspace, *paths)):
        path = PurePosixPath(value)
        if (
            "\\" in value
            or ".." in path.parts
            or path.as_posix() != value
            or not path.is_relative_to(root)
        ):
            raise ValueError("远程路径必须位于 E2E 工作区内")
        quoted = shlex.quote(value)
        commands.append(
            f'[ "$(realpath -m -- {quoted})" = {quoted} ] || '
            "{ printf '%s\\n' '远程工作区存在不安全的路径或符号链接' >&2; exit 1; }"
        )
    return "\n".join(commands) + "\n"


def host_key_fingerprint(key_bytes: bytes) -> str:
    digest = hashlib.sha256(key_bytes).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def validate_host_key(host: str, key_bytes: bytes) -> None:
    if host == DEFAULT_HOST and host_key_fingerprint(key_bytes) != DEFAULT_HOST_KEY_SHA256:
        raise ValueError(f"{host} 的 SSH 主机密钥与固定指纹不符")


def connect(host, port, user, password):
    import paramiko

    class PinnedHostKeyPolicy(paramiko.MissingHostKeyPolicy):
        def missing_host_key(self, client, hostname, key):
            if host != DEFAULT_HOST:
                raise paramiko.SSHException(f"未知 SSH 主机密钥: {hostname}")
            validate_host_key(host, key.asbytes())
            client.get_host_keys().add(hostname, key.get_name(), key)

    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(PinnedHostKeyPolicy())
    try:
        client.connect(
            hostname=host, port=port, username=user, password=password,
            timeout=20, banner_timeout=30, auth_timeout=30,
        )
        transport = client.get_transport()
        if transport is None:
            raise RuntimeError("SSH 连接未建立有效传输通道")
        validate_host_key(host, transport.get_remote_server_key().asbytes())
    except Exception:
        client.close()
        raise
    return client


def drain_channel(channel, timeout: float, stdout_sink=None, stderr_sink=None):
    """同一 SSH channel 的两个流都读完后才等待退出码，避免窗口耗尽死锁。"""
    deadline = time.monotonic() + timeout
    stdout_data = bytearray()
    stderr_data = bytearray()
    while True:
        received = False
        if channel.recv_ready():
            data = channel.recv(65536)
            if data:
                received = True
                if stdout_sink is None:
                    stdout_data.extend(data)
                else:
                    stdout_sink(data)
        if channel.recv_stderr_ready():
            data = channel.recv_stderr(65536)
            if data:
                received = True
                if stderr_sink is None:
                    stderr_data.extend(data)
                else:
                    stderr_sink(data)
        if (
            channel.exit_status_ready()
            and not channel.recv_ready()
            and not channel.recv_stderr_ready()
        ):
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            channel.close()
            raise TimeoutError("远程命令执行超时")
        if not received:
            time.sleep(min(0.05, remaining))
    return channel.recv_exit_status(), bytes(stdout_data), bytes(stderr_data)


def run_simple(ssh, command: str, timeout: float = 60) -> tuple[int, str, str]:
    stdin, stdout, _ = ssh.exec_command(command, timeout=timeout)
    stdin.close()
    code, output, errors = drain_channel(stdout.channel, timeout)
    return code, output.decode("utf-8", "replace"), errors.decode("utf-8", "replace")


def upload_base64(ssh, path: str, data: bytes, *, workspace: str) -> None:
    """base64 流式上传：远程 `base64 -d > <path>`，本端按 32 KiB 块喂 stdin。"""
    parent = PurePosixPath(path).parent.as_posix()
    cmd = workspace_guard(workspace, parent, path)
    cmd += (
        f"umask 022\nmkdir -p -- {shlex.quote(parent)}\n"
        f"base64 -d > {shlex.quote(path)}"
    )
    stdin, stdout, _ = ssh.exec_command(cmd, timeout=900)
    payload = base64.b64encode(data)
    for i in range(0, len(payload), 32768):
        stdin.write(payload[i : i + 32768])
    stdin.flush()
    stdin.channel.shutdown_write()
    code, _, errors = drain_channel(stdout.channel, timeout=900)
    err = errors.decode("utf-8", "replace")
    if code != 0:
        raise RuntimeError(f"上传失败 {path}: {err.strip()}")
    command = workspace_guard(workspace, path) + f"wc -c < {shlex.quote(path)}"
    code, size, err = run_simple(ssh, command, timeout=60)
    if code != 0 or int(size.strip() or 0) != len(data):
        raise RuntimeError(
            f"上传校验失败 {path}: 期望 {len(data)} 字节, 实际 {size} ({err.strip()})"
        )
    print(f"[远程] {path} 已就位（{size.strip()} 字节）", flush=True)


def stream_command(ssh, command: str, timeout: float) -> int:
    """执行远程命令并实时回传 stdout/stderr，返回 exit status。"""
    stdin, stdout, _ = ssh.exec_command(command, timeout=timeout)
    stdin.close()
    code, _, _ = drain_channel(stdout.channel, timeout, stdout_sink=out, stderr_sink=out)
    return code


def e2e_command(workspace: str) -> str:
    archive = remote_path(workspace, REMOTE_TAR)
    srcbase = remote_path(workspace, "srcbase")
    script = remote_path(workspace, "srcbase", "deploy", "test_release_e2e.sh")
    run = workspace_guard(workspace, archive, srcbase, script)
    run += (
        f"mkdir -p -- {shlex.quote(workspace)}\n"
        f"rm -rf -- {shlex.quote(srcbase)}\n"
        f"mkdir -p -- {shlex.quote(srcbase)}\n"
        f"tar -xzf {shlex.quote(archive)} -C {shlex.quote(srcbase)}\n"
    )
    run += workspace_guard(workspace, srcbase, script)
    run += (
        f"printf '%s\\n' {shlex.quote(f'[远程] 解压完成，E2E 脚本: {script}')}\n"
        f"cd -- {shlex.quote(workspace)}\n"
        f"E2E_WORKSPACE={shlex.quote(workspace)} bash {shlex.quote(script)}\n"
    )
    return run


def main() -> int:
    ap = argparse.ArgumentParser(description="在 121.4.28.63 上运行 release.py E2E")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--user", default=DEFAULT_USER)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument(
        "--password", default=None, help="root 密码（缺省取 TRANS_E2E_PASSWORD 或交互输入）",
    )
    ap.add_argument("--workspace", default=DEFAULT_WORKSPACE)
    ap.add_argument("--skip-build", action="store_true", help="跳过本地快照打包（复用上次 tar）")
    ap.add_argument("--tar-file", default=None, help="本地上传的 tar 路径（缺省自动打快照）")
    args = ap.parse_args()
    workspace = validate_workspace(args.workspace).as_posix()

    password = args.password or os.environ.get("TRANS_E2E_PASSWORD")
    if not password:
        password = getpass.getpass(f"{args.user}@{args.host} 密码: ")

    if args.tar_file:
        data = Path(args.tar_file).read_bytes()
    else:
        data = build_repo_tarball()

    ssh = None
    try:
        print(f"[远程] 连接 {args.user}@{args.host}:{args.port} ...", flush=True)
        ssh = connect(args.host, args.port, args.user, password)
        print("[远程] 已连接", flush=True)

        # 预检：docker / compose / 磁盘 / 确认生产栈名，避免误伤
        preflight = """
set -e
echo '--- docker ---'; docker version --format '{{.Server.Version}}'
echo '--- compose ---'; docker compose version
echo '--- disk ---'; df -h /opt | tail -1
echo '--- 生产项目确认（期望 trans-production）---'
docker ps --filter 'name=trans-production' --format '{{.Names}}' || true
ls -d /opt/trans-linux 2>/dev/null || echo '/opt/trans-linux 不存在'
"""
        code = stream_command(ssh, preflight, timeout=120)
        if code != 0:
            print("\n[远程] 预检失败，中止", flush=True)
            return 1

        remote_tar = remote_path(workspace, REMOTE_TAR)

        upload_base64(ssh, remote_tar, data, workspace=workspace)
        if not args.skip_build:
            # 上传 E2E 脚本自身（独立副本，避免快照遗漏）
            local_script = REPO_ROOT / "deploy" / "test_release_e2e.sh"
            upload_base64(
                ssh, remote_path(workspace, "test_release_e2e.sh"), local_script.read_bytes(),
                workspace=workspace,
            )

        run = e2e_command(workspace)
        print(f"\n[远程] 开始运行 E2E（工作区 {workspace}）...", flush=True)
        code = stream_command(ssh, run, timeout=3600)
        print(f"\n[远程] E2E 退出码: {code}", flush=True)
        return code
    finally:
        if ssh is not None:
            ssh.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        raise SystemExit(130) from None
    except Exception as exc:  # noqa: BLE001 - 顶层出口，向用户报告全部错误
        print(f"错误: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
