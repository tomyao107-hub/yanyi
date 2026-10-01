"""Exercise remote E2E command boundaries without SSH or Paramiko installed."""

from __future__ import annotations

import shlex
import sys
from collections import deque
from types import SimpleNamespace
from typing import Any

import pytest

from deploy import run_e2e_on_server as runner


@pytest.mark.parametrize(
    "workspace",
    [
        "/", "/opt", "/opt/trans-linux", "/opt/trans-e2e-", "/tmp/trans-e2e-check",
        "/opt/trans-e2e-check/child", "/opt/trans-e2e-check/../trans-linux",
        "/opt//trans-e2e-check", "/opt/trans-e2e-check/", r"\opt\trans-e2e-check",
        "/opt/trans-e2e-check;touch /tmp/escaped", "/opt/trans-e2e-check\n",
    ],
)
def test_workspace_rejects_production_and_noncanonical_paths(workspace: str) -> None:
    with pytest.raises(ValueError):
        runner.validate_workspace(workspace)


def test_remote_paths_use_posix_separators_on_all_platforms() -> None:
    path = runner.remote_path("/opt/trans-e2e-test", "srcbase", "deploy/test_release_e2e.sh")
    assert path == "/opt/trans-e2e-test/srcbase/deploy/test_release_e2e.sh"
    assert runner.validate_workspace("/opt/trans-e2e-test").parent.as_posix() == "/opt"


@pytest.mark.parametrize("part", ["../trans-linux", "/etc/passwd", r"src\deploy", "a//b"])
def test_remote_path_rejects_escapes(part: str) -> None:
    with pytest.raises(ValueError):
        runner.remote_path("/opt/trans-e2e-test", part)


@pytest.mark.parametrize(
    "path", ["/opt/trans-linux/a", "/opt/trans-e2e-other/a", "/opt/trans-e2e-test/../a"]
)
def test_guard_rejects_paths_outside_selected_workspace(path: str) -> None:
    with pytest.raises(ValueError):
        runner.workspace_guard("/opt/trans-e2e-test", path)


def test_e2e_command_checks_real_paths_before_deleting_and_after_extracting() -> None:
    command = runner.e2e_command("/opt/trans-e2e-test")
    srcbase = "/opt/trans-e2e-test/srcbase"
    script = srcbase + "/deploy/test_release_e2e.sh"
    first_guard = command.index(f"realpath -m -- {srcbase}")
    removal = command.index(f"rm -rf -- {srcbase}")
    extraction = command.index("tar -xzf ")
    final_guard = command.rindex(f"realpath -m -- {script}")
    execution = command.index(f"bash {script}")
    assert first_guard < removal < extraction < final_guard < execution
    assert "\\" not in command.replace("\\n", "")


class FakeChannel:
    def __init__(
        self,
        stdout: list[bytes],
        stderr: list[bytes],
        *,
        exit_early: bool = False,
        stays_open: bool = False,
        status: int = 7,
    ) -> None:
        self.stdout = deque(stdout)
        self.stderr = deque(stderr)
        self.exit_early = exit_early
        self.stays_open = stays_open
        self.status = status
        self.events: list[str] = []
        self.closed = False

    def recv_ready(self) -> bool:
        return bool(self.stdout)

    def recv_stderr_ready(self) -> bool:
        return bool(self.stderr)

    def _read(self, data: deque[bytes], size: int, event: str) -> bytes:
        self.events.append(event)
        chunk = data.popleft()
        if len(chunk) > size:
            data.appendleft(chunk[size:])
        return chunk[:size]

    def recv(self, size: int) -> bytes:
        return self._read(self.stdout, size, "stdout")

    def recv_stderr(self, size: int) -> bytes:
        return self._read(self.stderr, size, "stderr")

    def exit_status_ready(self) -> bool:
        return self.exit_early or (not self.stays_open and not self.stdout and not self.stderr)

    def recv_exit_status(self) -> int:
        assert not self.stdout and not self.stderr, "waiting for status before drain can deadlock"
        self.events.append("status")
        return self.status

    def close(self) -> None:
        self.closed = True

    def shutdown_write(self) -> None:
        self.events.append("shutdown_write")


class FakeStream:
    def __init__(self, channel: FakeChannel) -> None:
        self.channel = channel
        self.writes: list[bytes] = []

    def read(self) -> bytes:
        raise AssertionError("both SSH streams must be drained through the shared channel")

    def close(self) -> None:
        pass

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    def flush(self) -> None:
        pass


class FakeSSH:
    def __init__(self, *channels: FakeChannel) -> None:
        self.channels = deque(channels)
        self.commands: list[str] = []

    def exec_command(self, command: str, timeout: float):
        self.commands.append(command)
        channel = self.channels.popleft()
        return FakeStream(channel), FakeStream(channel), FakeStream(channel)


def test_run_simple_drains_large_stderr_fairly_before_waiting_for_exit() -> None:
    errors = b"warning\n" * 25000
    channel = FakeChannel([b"one\n", b"two\n"], [errors])
    result = runner.run_simple(FakeSSH(channel), "irrelevant")
    assert result == (7, "one\ntwo\n", errors.decode())
    assert channel.events[:4] == ["stdout", "stderr", "stdout", "stderr"]
    assert channel.events[-1] == "status"


def test_stream_command_drains_stderr_after_exit_status_is_already_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunks: list[bytes] = []
    monkeypatch.setattr(runner, "out", chunks.append)
    channel = FakeChannel([b"out\n"], [b"err\n" * 20000], exit_early=True)
    assert runner.stream_command(FakeSSH(channel), "irrelevant", 10) == 7
    assert b"".join(chunks) == b"out\n" + b"err\n" * 20000
    assert channel.events[-1] == "status"


def test_channel_timeout_closes_unfinished_command(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = iter([100.0, 101.0])
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(clock))
    channel = FakeChannel([], [], stays_open=True)
    with pytest.raises(TimeoutError):
        runner.drain_channel(channel, 0.5)
    assert channel.closed


def test_upload_quotes_paths_and_guards_before_creating_or_writing() -> None:
    path = "/opt/trans-e2e-test/space and 'quote.tar.gz"
    ssh = FakeSSH(FakeChannel([], [], status=0), FakeChannel([b"3\n"], [], status=0))
    runner.upload_base64(ssh, path, b"abc", workspace="/opt/trans-e2e-test")
    upload, verify = ssh.commands
    quoted = shlex.quote(path)
    assert upload.index(f"realpath -m -- {quoted}") < upload.index("mkdir -p -- ")
    assert upload.index("mkdir -p -- ") < upload.index(f"base64 -d > {quoted}")
    assert verify.index(f"realpath -m -- {quoted}") < verify.index(f"wc -c < {quoted}")
    assert "\\opt" not in upload


def test_upload_rejects_unconfined_paths_before_ssh() -> None:
    ssh = FakeSSH()
    with pytest.raises(ValueError):
        runner.upload_base64(ssh, "/opt/trans-linux/production", b"abc",
                             workspace="/opt/trans-e2e-test")
    assert ssh.commands == []


def test_host_fingerprint_and_default_server_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    fingerprint = "SHA256:47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU"
    assert runner.host_key_fingerprint(b"") == fingerprint
    with pytest.raises(ValueError, match="固定指纹"):
        runner.validate_host_key(runner.DEFAULT_HOST, b"untrusted-key")
    monkeypatch.setattr(runner, "DEFAULT_HOST_KEY_SHA256", fingerprint)
    runner.validate_host_key(runner.DEFAULT_HOST, b"")


@pytest.mark.parametrize("known_key", [False, True])
def test_connect_loads_known_hosts_and_checks_pin_without_network(
    monkeypatch: pytest.MonkeyPatch, known_key: bool,
) -> None:
    key = SimpleNamespace(asbytes=lambda: b"accepted-key", get_name=lambda: "ssh-ed25519")
    events: list[str] = []

    class FakeClient:
        def load_system_host_keys(self) -> None:
            events.append("load_known_hosts")

        def set_missing_host_key_policy(self, policy: Any) -> None:
            self.policy = policy

        def connect(self, **kwargs: Any) -> None:
            events.append("connect")
            if not known_key:
                self.policy.missing_host_key(self, kwargs["hostname"], key)

        def get_host_keys(self):
            return SimpleNamespace(add=lambda *args: events.append("accept_pinned_key"))

        def get_transport(self):
            return SimpleNamespace(get_remote_server_key=lambda: key)

        def close(self) -> None:
            events.append("close")

    fake_paramiko = SimpleNamespace(
        SSHClient=FakeClient, MissingHostKeyPolicy=object, SSHException=RuntimeError,
    )
    monkeypatch.setitem(sys.modules, "paramiko", fake_paramiko)
    monkeypatch.setattr(
        runner, "DEFAULT_HOST_KEY_SHA256", runner.host_key_fingerprint(b"accepted-key"),
    )
    runner.connect(runner.DEFAULT_HOST, 22, "user", "test-placeholder")
    assert events[:2] == ["load_known_hosts", "connect"]
    assert ("accept_pinned_key" in events) is not known_key
    if not known_key:
        with pytest.raises(RuntimeError, match="未知 SSH 主机密钥"):
            runner.connect("example.invalid", 22, "user", "test-placeholder")
        assert events[-1] == "close"
    monkeypatch.setattr(runner, "DEFAULT_HOST_KEY_SHA256", "SHA256:wrong")
    with pytest.raises(ValueError, match="固定指纹"):
        runner.connect(runner.DEFAULT_HOST, 22, "user", "test-placeholder")
    assert events[-1] == "close"
