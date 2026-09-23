"""Authenticated action contracts over a real threaded HTTP server."""

from __future__ import annotations

import http.client
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from claude_swap.web.server import MAX_BODY_BYTES, DashboardState, serve
from tests.test_provider_actions import Claude, Codex, watch_event


@pytest.fixture
def web(tmp_path, monkeypatch):
    from claude_swap.codex import autoswitch

    monkeypatch.setattr(autoswitch, "running_codex_processes", list)
    state = DashboardState(Claude(), Codex(tmp_path / "codex"))
    server, _ = serve(state, host="127.0.0.1", port=0, token="test-web-auth")
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    yield SimpleNamespace(state=state, server=server, token="test-web-auth")
    server.shutdown()
    server.server_close()
    thread.join(3)


def request(web, path, payload=None, *, token=True, raw=None, headers=None, method="POST"):
    request_headers = {"Content-Type": "application/json"}
    if token:
        request_headers["X-Auth-Token"] = web.token if token is True else token
    request_headers.update(headers or {})
    body = raw if raw is not None else json.dumps(payload or {}).encode()
    connection = http.client.HTTPConnection("127.0.0.1", web.server.server_port, timeout=5)
    try:
        connection.request(method, path, body=body if method == "POST" else None, headers=request_headers)
        response = connection.getresponse()
        data = json.loads(response.read())
        return response.status, data, dict(response.getheaders())
    finally:
        connection.close()


ROUTES = ["switch", "add", "remove", "disabled", "switch-best", "token", "auto"]


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("auth", [False, "wrong", "\xe9"])
def test_every_action_requires_header_auth(web, route, auth):
    code, _, _ = request(web, f"/api/{route}", {"provider": "claude"}, token=auth)
    assert code == 403
    assert web.state._claude.calls == []


def test_query_token_cannot_authorize_cross_origin_post(web):
    code, _, _ = request(web, f"/api/add?token={web.token}", {"provider": "claude"}, token=False)
    assert code == 403
    assert web.state._claude.calls == []


def test_get_still_supports_query_token_and_never_logs_it(web, caplog):
    caplog.set_level(logging.DEBUG, logger="claude-swap")
    code, body, headers = request(web, f"/api/state?token={web.token}", token=False, method="GET")
    assert code == 200
    assert body["claude"]["capabilities"][-1] == "token"
    assert "token" not in body["codex"]["capabilities"]
    assert "Code tab" in body["claude"]["switchNotice"]
    assert body["claude"]["auto"]["mode"] == "stopped"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["Cache-Control"] == "no-store"
    assert "Access-Control-Allow-Origin" not in headers
    assert web.token not in caplog.text


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_peer_provider_routes_dispatch_the_same_operations(web, provider):
    for route, body in [
        ("switch", {"number": 2}), ("add", {}),
        ("disabled", {"number": "2", "disabled": True}),
        ("disabled", {"number": "2", "disabled": False}),
        ("remove", {"number": 2, "confirm": True}),
        ("switch-best", {}),
    ]:
        code, result, _ = request(web, f"/api/{route}", {"provider": provider, **body})
        assert code == 200, result
        assert result["ok"]
    assert len(web.state.actions._switchers[provider].calls) >= 5


@pytest.mark.parametrize("provider", ["claude", "codex"])
@pytest.mark.parametrize("confirm", [None, False, "true", 1])
def test_remove_confirmation_cannot_be_coerced(web, provider, confirm):
    code, _, _ = request(web, "/api/remove", {"provider": provider, "number": 1, "confirm": confirm})
    assert code == 400
    assert web.state.actions._switchers[provider].calls == []


@pytest.mark.parametrize("raw", [b"[]", b"null", b"true", b"1", b'"hello"', b'{', b'\xff',
    b'{"provider":"claude","provider":"codex"}', b'{"provider":"claude","threshold":NaN}',
])
def test_nonobject_malformed_and_duplicate_json_are_refused(web, raw):
    assert request(web, "/api/add", raw=raw)[0] == 400
    assert web.state._claude.calls == []
    assert web.state._codex.calls == []


@pytest.mark.parametrize("headers", [
    {"Content-Length": "-1"}, {"Content-Length": "invalid"},
    {"Content-Length": "99999999999999999999999"},
    {"Content-Length": str(MAX_BODY_BYTES + 1)}, {"Content-Length": "0"},
    {"Content-Type": "text/plain"}, {"Content-Type": "application/x-www-form-urlencoded"},
    {"Transfer-Encoding": "chunked"},
])
def test_body_length_and_content_type_are_bounded(web, headers):
    assert request(web, "/api/add", {"provider": "claude"}, headers=headers)[0] == 400
    assert web.state._claude.calls == []


@pytest.mark.parametrize("number", [True, False, 0, -1, 1.5, None, [], {}, "", "1@example.com", "-1", "\uff11", "9" * 11])
def test_slot_validation_does_not_coerce_arbitrary_json(web, number):
    assert request(web, "/api/switch", {"provider": "claude", "number": number})[0] == 400
    assert web.state._claude.calls == []


@pytest.mark.parametrize("route,payload", [
    ("add", {}), ("add", {"provider": None}), ("add", {"provider": []}),
    ("add", {"provider": "other"}), ("add", {"provider": "claude", "unexpected": 1}),
    ("disabled", {"provider": "codex", "number": 1, "disabled": "false"}),
    ("disabled", {"provider": "codex", "number": 1, "disabled": 1}),
    ("token", {"provider": "codex", "token": "not-supported"}),
    ("token", {"provider": "claude", "token": "-"}),
    ("token", {"provider": "claude", "token": ""}),
    ("token", {"provider": "claude", "token": "test-token", "slot": True}),
    ("token", {"provider": "claude", "token": "test-token", "slot": "1"}),
    ("auto", {"provider": "codex", "mode": "live"}),
    ("auto", {"provider": "codex", "mode": "live", "confirm": "true"}),
    ("auto", {"provider": "codex", "mode": "invalid"}),
    ("auto", {"provider": "codex", "mode": "dry-run", "threshold": True}),
    ("auto", {"provider": "codex", "mode": "dry-run", "threshold": None}),
    ("auto", {"provider": "codex", "mode": "dry-run", "threshold": 100}),
])
def test_invalid_contracts_and_unsupported_operations_do_not_mutate(web, route, payload):
    assert request(web, f"/api/{route}", payload)[0] == 400
    assert web.state._claude.calls == []
    assert web.state._codex.calls == []


def test_token_route_guards_implicit_email_and_explicit_slot_overwrite(web):
    web.state._claude.accounts = [SimpleNamespace(number="1", email="one@example.com")]
    payload = {"provider": "claude", "token": "private-test-sentinel", "email": "one@example.com", "slot": None, "confirm": False}
    code, body, _ = request(web, "/api/token", payload)
    assert code == 400
    assert "confirm" in body["message"]
    payload.update(confirm=True)
    assert request(web, "/api/token", payload)[0] == 200
    payload.update(confirm=False, email="new@example.com", slot=1)
    assert request(web, "/api/token", payload)[0] == 400
    payload.update(slot=2)
    assert request(web, "/api/token", payload)[0] == 200


def test_submitted_token_never_appears_in_errors_or_logs(web, caplog):
    caplog.set_level(logging.DEBUG, logger="claude-swap")
    token = "private-test-sentinel"
    web.state._claude.add_account_from_token = Mock(side_effect=RuntimeError(f"remote rejected {token}"))
    code, body, _ = request(web, "/api/token", {"provider": "claude", "token": token})
    assert code == 400
    assert token not in json.dumps(body)
    assert token not in caplog.text


def test_claude_no_switch_is_not_reported_as_success(web):
    web.state._claude.result = {"switched": False, "reason": "no-target", "message": "No usable account"}
    code, body, _ = request(web, "/api/switch", {"provider": "claude", "number": 2})
    assert code == 200
    assert body["ok"] is False
    assert body["switched"] is False
    assert body["reason"] == "no-target"
    assert "separate sign-in" in body["switchNotice"]


def test_auto_contract_state_and_server_close_join_workers(web, monkeypatch):
    ready = watch_event(web.state.actions.auto, monkeypatch, "switch")
    code, _, _ = request(web, "/api/auto", {"provider": "codex", "mode": "dry-run", "threshold": 80})
    assert code == 200
    assert ready.wait(2)
    assert web.state._codex.calls == []
    _, state, _ = request(web, "/api/state", method="GET")
    auto = state["codex"]["auto"]
    assert auto["mode"] == "dry-run"
    assert auto["threshold"] == 80
    assert all(set(event) == {"message", "kind", "at"} for event in auto["events"])
    assert state["claude"]["auto"]["mode"] == "stopped"
    assert request(web, "/api/auto", {"provider": "codex", "mode": "live"})[0] == 400
    ready.clear()
    assert request(web, "/api/auto", {"provider": "codex", "mode": "live", "confirm": True})[0] == 200
    assert ready.wait(2)
    assert web.state._codex.calls == [("switch", "2")]
    worker = web.state.actions.auto._workers["codex"][0]
    web.server.shutdown()
    web.server.server_close()
    assert not worker.is_alive()
    assert web.state.actions.auto.status("codex")["mode"] == "stopped"


def test_collect_and_mutate_are_serialized_without_stale_cache_republish(web):
    entered, release, switch_started = threading.Event(), threading.Event(), threading.Event()
    original_snapshot = web.state._claude.accounts_snapshot
    original_switch = web.state._claude.switch_to

    def blocked_snapshot(**kwargs):
        entered.set()
        assert release.wait(3)
        return original_snapshot(**kwargs)

    def observed_switch(*args, **kwargs):
        switch_started.set()
        return original_switch(*args, **kwargs)

    web.state._claude.accounts_snapshot = blocked_snapshot
    web.state._claude.switch_to = observed_switch
    with ThreadPoolExecutor(max_workers=2) as pool:
        collecting = pool.submit(request, web, "/api/state", method="GET")
        assert entered.wait(2)
        mutation = pool.submit(request, web, "/api/switch", {"provider": "claude", "number": 2})
        try:
            assert not switch_started.wait(0.05)
        finally:
            release.set()
        assert collecting.result()[0] == 200
        assert mutation.result()[0] == 200
    assert web.state._cached is None
    assert switch_started.is_set()
