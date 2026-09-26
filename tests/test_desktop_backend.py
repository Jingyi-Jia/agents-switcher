from __future__ import annotations

import base64
import hashlib
import http.client
import io
import json
import os
import re
import secrets
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from unittest.mock import Mock

import pytest

from claude_swap import desktop


def start_message(token=None):
    return {"type": "start", "protocol": 1, "token": token or secrets.token_urlsafe(32)}


def lines(*messages):
    return io.BytesIO(b"".join(json.dumps(message).encode() + b"\n" for message in messages))


@pytest.mark.parametrize("message", [
    {}, [], None, {"type": "start"},
    {"type": "start", "protocol": True, "token": "x" * 32},
    {"type": "start", "protocol": 2, "token": "x" * 32},
    {"type": "start", "protocol": 1, "token": "short"},
    {"type": "start", "protocol": 1, "token": "x" * 129},
    {"type": "start", "protocol": 1, "token": "\n" * 32},
    {"type": "start", "protocol": 1, "token": "x" * 32, "host": "0.0.0.0"},
])
def test_invalid_start_does_not_touch_provider_credentials(monkeypatch, message):
    build = Mock()
    monkeypatch.setattr(desktop, "_build_state", build)
    status = io.StringIO()
    assert desktop.run(lines(message), status) == 1
    build.assert_not_called()
    assert json.loads(status.getvalue()) == {"type": "error", "code": "startup-failed"}


@pytest.mark.parametrize("content", [b"x" * 4097, b'{"type":"start"}', b"\xff\n"])
def test_control_messages_are_bounded_and_newline_delimited(monkeypatch, content):
    build = Mock()
    monkeypatch.setattr(desktop, "_build_state", build)
    assert desktop.run(io.BytesIO(content), io.StringIO()) == 1
    build.assert_not_called()


def test_initial_eof_exits_without_creating_a_store(monkeypatch):
    build = Mock()
    monkeypatch.setattr(desktop, "_build_state", build)
    status = io.StringIO()
    assert desktop.run(io.BytesIO(), status) == 0
    build.assert_not_called()
    assert not status.getvalue()


def test_native_tls_is_initialized_before_provider_state_and_server(monkeypatch):
    events = []
    serve = desktop.serve

    def build_state(*, state_class):
        assert events == ["tls"]
        events.append("state")
        return state_class()

    def start_server(*args, **kwargs):
        assert events == ["tls", "state"]
        events.append("server")
        return serve(*args, **kwargs)

    monkeypatch.setattr(desktop, "use_native_tls", lambda: events.append("tls"))
    monkeypatch.setattr(desktop, "_build_state", build_state)
    monkeypatch.setattr(desktop, "serve", start_server)
    status = io.StringIO()

    assert desktop.run(lines(start_message(), {"type": "shutdown"}), status) == 0
    assert events == ["tls", "state", "server"]
    assert json.loads(status.getvalue())["type"] == "ready"


@pytest.mark.parametrize("finish", [[], [{"type": "shutdown"}]])
def test_shutdown_and_eof_close_server_and_actions_without_exposing_token(monkeypatch, finish):
    state = desktop.DesktopState()
    state.close = Mock(wraps=state.close)
    monkeypatch.setattr(desktop, "_build_state", lambda **_: state)
    token = secrets.token_urlsafe(32)
    status = io.StringIO()
    assert desktop.run(lines(start_message(token), *finish), status) == 0
    ready = json.loads(status.getvalue())
    assert set(ready) == {"type", "protocol", "port"}
    assert ready["type"] == "ready" and ready["protocol"] == 1
    assert 0 < ready["port"] < 65536
    assert token not in status.getvalue()
    state.close.assert_called_once()
    assert state.actions._closed


def test_bind_failure_closes_state_and_only_emits_safe_error(monkeypatch):
    state = desktop.DesktopState()
    monkeypatch.setattr(desktop, "_build_state", lambda **_: state)
    monkeypatch.setattr(desktop, "serve", Mock(side_effect=RuntimeError("sensitive diagnostic")))
    status = io.StringIO()
    assert desktop.run(lines(start_message()), status) == 1
    assert json.loads(status.getvalue()) == {"type": "error", "code": "startup-failed"}
    assert state.actions._closed


@pytest.mark.parametrize("endpoint", ["profile", "analytics"])
@pytest.mark.parametrize("finish", [b'{"type":"shutdown"}\n', b""])
def test_shutdown_drains_profile_and_analytics_requests(monkeypatch, endpoint, finish):
    ready, started, stopping, release = (threading.Event() for _ in range(4))
    control = Queue()
    token = secrets.token_urlsafe(32)
    control.put(json.dumps(start_message(token)).encode() + b"\n")

    class Status(io.StringIO):
        def flush(self):
            ready.set()

    def perform(*args, **kwargs):
        started.set()
        assert release.wait(10)
        return {"ok": True, "message": "Completed the request"}

    state = desktop.DesktopState()
    close = state.close

    def close_state():
        stopping.set()
        close()

    monkeypatch.setattr(state, "close", close_state)
    monkeypatch.setattr(state.claude_desktop, "create", perform)
    monkeypatch.setattr(state.analytics, "get", perform)
    monkeypatch.setattr(desktop, "_build_state", lambda **_: state)
    status = Status()

    def request(port):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            headers = {"X-Auth-Token": token, "Content-Type": "application/json"}
            if endpoint == "profile":
                connection.request("POST", "/api/claude-desktop/create",
                                   json.dumps({"name": "Synthetic", "confirm": True}), headers)
            else:
                connection.request("GET", "/api/analytics?provider=claude", headers=headers)
            response = connection.getresponse()
            assert response.status == 200
            return json.loads(response.read())
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        backend = pool.submit(desktop.run, Mock(readline=lambda _: control.get(timeout=10)), status)
        try:
            assert ready.wait(10)
            pending = pool.submit(request, json.loads(status.getvalue())["port"])
            assert started.wait(10)
            control.put(finish)
            assert stopping.wait(10)
            with pytest.raises(TimeoutError):
                backend.result(timeout=0.2)
        finally:
            release.set()
            control.put(b"")
        assert pending.result(timeout=10)["ok"]
        assert backend.result(timeout=10) == 0
        assert state.actions._closed


def test_desktop_metadata_is_not_added_to_browser_state(monkeypatch):
    monkeypatch.setattr(desktop, "client_installed", lambda name: name == "claude")
    state = desktop.DesktopState()
    result = state.get()
    assert result["desktop"]["providers"] == {"claude": {"installed": True}, "codex": {"installed": False}}
    assert result["desktop"]["windowClose"] == "quit"
    assert "desktop" not in desktop.DashboardState().get()
    assert "desktop" not in state._cached


def test_client_detection_checks_native_install_directory_without_running_it(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("PATH", "/system/bin")
    which = Mock(return_value=None)
    monkeypatch.setattr(desktop.shutil, "which", which)
    assert not desktop.client_installed("claude")
    assert str(tmp_path / ".local" / "bin") in which.call_args.kwargs["path"].split(os.pathsep)
    assert which.call_args.args == ("claude",)


def test_frozen_helper_restores_external_tool_library_search_path(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/bundled")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/original")
    monkeypatch.setenv("LIBPATH", "/bundled")
    monkeypatch.delenv("LIBPATH_ORIG", raising=False)
    desktop._restore_external_library_paths()
    assert os.environ["LD_LIBRARY_PATH"] == "/original"
    assert "LD_LIBRARY_PATH_ORIG" not in os.environ
    assert "LIBPATH" not in os.environ


@pytest.mark.parametrize("shutdown", [True, False])
def test_private_pipe_helper_serves_authenticated_page_and_exits(tmp_path, shutdown):
    env = dict(os.environ)
    env.update({"HOME": str(tmp_path), "USERPROFILE": str(tmp_path), "XDG_DATA_HOME": str(tmp_path / "data")})
    process = subprocess.Popen([
        sys.executable, "-c",
        "from claude_swap import desktop; "
        "desktop._build_state = lambda state_class: state_class(); "
        "raise SystemExit(desktop.main())",
    ], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    try:
        token = secrets.token_urlsafe(32)
        process.stdin.write(json.dumps(start_message(token)).encode() + b"\n")
        process.stdin.flush()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(process.stdout.readline)
            try:
                ready = json.loads(future.result(timeout=10))
            except Exception:
                process.kill()
                raise
        assert ready["type"] == "ready"
        connection = http.client.HTTPConnection("127.0.0.1", ready["port"], timeout=5)
        try:
            connection.request("GET", "/api/state", headers={"X-Auth-Token": token})
            response = connection.getresponse()
            assert response.status == 200
            assert json.loads(response.read())["desktop"]["windowClose"] == "quit"
            connection.request("GET", "/", headers={"X-Auth-Token": token})
            response = connection.getresponse()
            assert response.status == 200
            policy = response.getheader("Content-Security-Policy")
            page = response.read().decode()
            script = re.search(r"<script>(.*?)</script>", page, re.DOTALL).group(1)
            digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
            assert "'sha256-" + digest + "'" in policy
            assert "default-src 'none'" in policy
            assert "connect-src 'self'" in policy
            assert "frame-ancestors 'none'" in policy
            connection.request("GET", "/")
            response = connection.getresponse()
            assert response.status == 403
            response.read()
        finally:
            connection.close()
        if shutdown:
            process.stdin.write(b'{"type":"shutdown"}\n')
            process.stdin.flush()
        process.stdin.close()
        process.wait(timeout=10)
        assert process.returncode == 0
        assert not process.stdout.read()
        assert token.encode() not in process.stderr.read()
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
