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
import io
import json
import os
import select
import sys
import tarfile
from pathlib import Path

import paramiko

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HOST = "121.4.28.63"
DEFAULT_USER = "root"
DEFAULT_PORT = 22
DEFAULT_WORKSPACE = "/opt/trans-e2e-workspace"
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


def connect(host, port, user, password) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host, port=port, username=user, password=password,
        timeout=20, banner_timeout=30, auth_timeout=30,
    )
    return client


def run_simple(ssh, command: str, timeout: float = 60) -> tuple[int, str, str]:
    _, stdout, stderr = ssh.exec_command(command, timeout=timeout)
    code = stdout.channel.recv_exit_status()
    return code, stdout.read().decode("utf-8", "replace"), stderr.read().decode("utf-8", "replace")


def upload_base64(ssh, remote_path: str, data: bytes) -> None:
    """base64 流式上传：远程 `base64 -d > <path>`，本端按 32 KiB 块喂 stdin。"""
    parent = str(Path(remote_path).parent)
    cmd = f"umask 022 && mkdir -p '{parent}' && base64 -d > '{remote_path}'"
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=900)
    payload = base64.b64encode(data)
    for i in range(0, len(payload), 32768):
        stdin.write(payload[i : i + 32768])
    stdin.flush()
    stdin.channel.shutdown_write()
    code = stdout.channel.recv_exit_status()
    err = stderr.read().decode("utf-8", "replace")
    if code != 0:
        raise RuntimeError(f"上传失败 {remote_path}: {err.strip()}")
    code, size, err = run_simple(ssh, f"wc -c < '{remote_path}'", timeout=60)
    if code != 0 or int(size.strip() or 0) != len(data):
        raise RuntimeError(f"上传校验失败 {remote_path}: 期望 {len(data)} 字节, 实际 {size} ({err.strip()})")
    print(f"[远程] {remote_path} 已就位（{size.strip()} 字节）", flush=True)


def stream_command(ssh, command: str, timeout: float) -> int:
    """执行远程命令并实时回传 stdout/stderr，返回 exit status。"""
    stdin, stdout, stderr = ssh.exec_command(command, timeout=timeout)
    stdin.close()
    channels = [stdout.channel, stderr.channel]
    while channels:
        rl, _, _ = select.select(channels, [], [], 1.0)
        if not rl:
            continue
        for ch in list(channels):
            if ch in rl:
                try:
                    data = ch.recv(65536)
                except (EOFError, OSError):
                    channels.remove(ch)
                    continue
                if not data:
                    channels.remove(ch)
                else:
                    out(data)
    code = stdout.channel.recv_exit_status()
    return code


def main() -> int:
    ap = argparse.ArgumentParser(description="在 121.4.28.63 上运行 release.py E2E")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--user", default=DEFAULT_USER)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--password", default=None, help="root 密码（缺省取 TRANS_E2E_PASSWORD 或交互输入）")
    ap.add_argument("--workspace", default=DEFAULT_WORKSPACE)
    ap.add_argument("--skip-build", action="store_true", help="跳过本地快照打包（复用上次 tar）")
    ap.add_argument("--tar-file", default=None, help="本地上传的 tar 路径（缺省自动打快照）")
    args = ap.parse_args()

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
        preflight = f"""
set -e
echo '--- docker ---'; docker version --format '{{{{.Server.Version}}}}'
echo '--- compose ---'; docker compose version
echo '--- disk ---'; df -h /opt | tail -1
echo '--- 生产项目确认（期望 trans-production）---'
docker ps --filter 'name=trans-production' --format '{{{{.Names}}}}' || true
ls -d /opt/trans-linux 2>/dev/null || echo '/opt/trans-linux 不存在'
"""
        code = stream_command(ssh, preflight, timeout=120)
        if code != 0:
            print("\n[远程] 预检失败，中止", flush=True)
            return 1

        workspace = args.workspace
        remote_tar = f"{workspace}/{REMOTE_TAR}"
        script = f"{workspace}/srcbase/deploy/test_release_e2e.sh"

        upload_base64(ssh, remote_tar, data)
        if not args.skip_build:
            # 上传 E2E 脚本自身（独立副本，避免快照遗漏）
            local_script = REPO_ROOT / "deploy" / "test_release_e2e.sh"
            upload_base64(ssh, f"{workspace}/test_release_e2e.sh", local_script.read_bytes())

        run = (
            "set -e\n"
            f"mkdir -p '{workspace}'\n"
            f"rm -rf '{workspace}/srcbase'\n"
            f"mkdir -p '{workspace}/srcbase'\n"
            f"tar -xzf '{remote_tar}' -C '{workspace}/srcbase'\n"
            f"echo '[远程] 解压完成，E2E 脚本: {script}'\n"
            f"cd '{workspace}'\n"
            f"E2E_WORKSPACE='{workspace}' bash '{script}'\n"
        )
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
        raise SystemExit(130)
    except Exception as exc:  # noqa: BLE001 - 顶层出口，向用户报告全部错误
        print(f"错误: {exc}", file=sys.stderr)
        raise SystemExit(1)
