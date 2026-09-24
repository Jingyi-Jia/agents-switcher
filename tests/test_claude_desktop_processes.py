from __future__ import annotations

import os
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from claude_swap import claude_desktop as cd
from claude_swap.providers import ProviderActionError


@pytest.mark.parametrize("zombie", ["1000 Z", "1000 Z+", "0 Zs", "1000 Z Claude", "1000 Z [claude-desktop]"])
@pytest.mark.parametrize("live,expected", [("/usr/bin/python3", False), ("/Applications/Claude.app/Contents/MacOS/Claude", True)])
def test_zombies_do_not_block_scanning_live_processes(monkeypatch, zombie, live, expected):
    monkeypatch.setattr(cd.os, "getuid", lambda: 1000, raising=False)
    monkeypatch.setattr(cd.subprocess, "run", Mock(return_value=SimpleNamespace(stdout=f"{zombie}  \n1000 S {live}\n")))
    assert cd.running() is expected


@pytest.mark.parametrize("output", ["1000 S", "1000 R+ ", "1000 U\n1000 S python", "1000 S Claude\n1000 S", "unknown Z"])
def test_missing_live_command_or_invalid_owner_still_fails_closed(monkeypatch, output):
    monkeypatch.setattr(cd.os, "getuid", lambda: 1000, raising=False)
    monkeypatch.setattr(cd.subprocess, "run", Mock(return_value=SimpleNamespace(stdout=output)))
    with pytest.raises(ProviderActionError, match="unreadable process list"):
        cd.running()


@pytest.mark.parametrize("failure,diagnostic", [
    (subprocess.TimeoutExpired("private-command", 3, stderr="private-data"), "timed out"),
    (subprocess.CalledProcessError(1, "private-command", stderr="private-data"), "process check failed"),
    (PermissionError("private-data"), "could not be started"),
    (UnicodeDecodeError("utf-8", b"\xff", 0, 1, "private-data"), "unreadable process list"),
])
def test_process_failures_report_the_stage_without_private_output(monkeypatch, failure, diagnostic):
    monkeypatch.setattr(cd.subprocess, "run", Mock(side_effect=failure))
    with pytest.raises(ProviderActionError, match=diagnostic) as error:
        cd.running()
    assert "private" not in str(error.value)
    assert "No launch was attempted" in str(error.value)


@pytest.mark.skipif(sys.platform != "darwin", reason="Exercises native macOS ps zombie formatting")
def test_native_macos_zombie_has_no_command_and_does_not_break_scan():
    source = (
        "import os, sys\n"
        "pid = os.fork()\n"
        "if pid == 0: os._exit(0)\n"
        "print(pid, flush=True)\n"
        "sys.stdin.buffer.read(1)\n"
        "os.waitpid(pid, 0)\n"
    )
    with subprocess.Popen(
        [sys.executable, "-c", source], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    ) as process:
        try:
            pid = process.stdout.readline().strip()
            assert pid.isdigit()
            deadline = time.monotonic() + 5
            while True:
                row = subprocess.run(
                    ["/bin/ps", "-p", pid, "-o", "uid=,stat=,comm="],
                    capture_output=True, text=True, timeout=3, check=True,
                ).stdout.split()
                if len(row) >= 2 and row[1].startswith("Z"):
                    break
                assert time.monotonic() < deadline, "Disposable child did not become a zombie"
                time.sleep(0.05)
            assert row[0] == str(os.getuid())
            assert len(row) == 2, "macOS zombie command formatting changed"
            assert isinstance(cd.running(), bool)
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
