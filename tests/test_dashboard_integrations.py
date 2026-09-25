import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.test_web_actions import request, web


@pytest.mark.parametrize("path", ["/api/analytics?provider=codex", "/api/codex/status", "/api/preferences"])
def test_new_reads_require_header_auth_and_reject_query_credentials(web, path):
    web.state.analytics.get = Mock()
    web.state.codex_desktop.status = Mock()
    assert request(web, path, method="GET", token=False)[0] == 403
    separator = "&" if "?" in path else "?"
    assert request(web, path + separator + "token=" + web.token, method="GET", token=False)[0] == 403
    web.state.analytics.get.assert_not_called()
    web.state.codex_desktop.status.assert_not_called()


@pytest.mark.parametrize("query", ["", "provider=other", "provider=codex&provider=claude", "provider=codex&force=2", "provider=codex&force=", "provider=codex&path=/private", "provider=codex&unexpected="])
def test_analytics_rejects_invalid_queries_without_fetching(web, query):
    web.state.analytics.get = Mock()
    assert request(web, "/api/analytics?" + query, method="GET")[0] == 400
    web.state.analytics.get.assert_not_called()


def test_analytics_reads_are_separate_from_switching_and_provider_state(web):
    expected = {"provider": "claude", "scope": "local", "source": "Claude Code · This device", "accounts": []}
    web.state.analytics.get = Mock(return_value=expected)
    web.state.get = Mock(side_effect=AssertionError("must not fetch account quota"))
    code, body, headers = request(web, "/api/analytics?provider=claude&force=1", method="GET")
    assert code == 200 and body == expected
    assert headers["Cache-Control"] == "no-store"
    assert "Access-Control-Allow-Origin" not in headers
    web.state.analytics.get.assert_called_once_with("claude", force=True)
    assert web.state._claude.calls == web.state._codex.calls == []


def test_analytics_unexpected_errors_are_private(web):
    web.state.analytics.get = Mock(side_effect=RuntimeError("private-credential-path"))
    code, body, _ = request(web, "/api/analytics?provider=codex", method="GET")
    assert code == 400
    assert "private-credential-path" not in str(body)


@pytest.mark.parametrize("status,expected_code", [
    ({"available": True, "running": True}, "codex-running"),
    ({"available": False, "running": None}, "codex-status-unknown"),
    ({"available": True, "running": None}, "codex-status-unknown"),
    ({"available": 1, "running": False}, "codex-status-unknown"),
    ({"available": True, "running": 0}, "codex-status-unknown"),
])
@pytest.mark.parametrize("route", ["switch", "switch-best"])
def test_codex_prerequisites_are_structured_and_never_mutate(web, status, expected_code, route):
    web.state.codex_desktop.status = Mock(return_value=status)
    payload = {"provider": "codex"}
    if route == "switch":
        payload["number"] = "2"
    code, body, _ = request(web, "/api/" + route, payload)
    assert code == 400
    assert body["ok"] is False
    assert body["kind"] == "action-required"
    assert body["code"] == expected_code
    assert "not changed" in body["message"]
    assert web.state._codex.calls == []


def test_codex_status_does_not_gate_claude_code(web):
    web.state.codex_desktop.status = Mock(side_effect=AssertionError("wrong provider"))
    code, body, _ = request(web, "/api/switch", {"provider": "claude", "number": "2"})
    assert code == 200 and body["ok"]
    assert body["restartRequired"] is False
    assert body["followUp"]
    web.state.codex_desktop.status.assert_not_called()


@pytest.mark.parametrize("action", ["quit", "open"])
def test_codex_app_actions_only_dispatch_fixed_authenticated_contracts(web, action):
    def serialized_action(**kwargs):
        assert web.state._lock._is_owned()
        return {"ok": True}

    method = Mock(side_effect=serialized_action)
    setattr(web.state.codex_desktop, action, method)
    path = f"/api/codex/{action}"
    assert request(web, path, {"confirm": True}, token=False)[0] == 403
    assert request(web, path, {"confirm": True, "path": "/arbitrary"})[0] == 400
    assert request(web, path, {"confirm": True, "pid": 1})[0] == 400
    assert request(web, path, {})[0] == 400
    method.assert_not_called()
    assert request(web, path, {"confirm": True})[0] == 200
    method.assert_called_once_with(confirm=True)
    assert web.state._codex.calls == []


def test_preferences_can_be_loaded_without_provider_requests(web):
    web.state.get = Mock(side_effect=AssertionError("no provider data should be needed"))
    assert request(web, "/api/preferences", {"theme": "dark"})[0] == 200
    code, body, _ = request(web, "/api/preferences", method="GET")
    assert code == 200
    assert body["theme"] == "dark"
    assert body["profileNoticeVersion"] == 0
    assert request(web, "/api/preferences", {"profileNoticeVersion": 1, "confirm": 1})[0] == 400
    assert request(web, "/api/preferences", {"profileNoticeVersion": 1, "confirm": True})[0] == 200
    assert request(web, "/api/preferences", method="GET")[1]["profileNoticeVersion"] == 1


@pytest.mark.parametrize("mode", ["live", "dry-run"])
@pytest.mark.parametrize("status,blocked", [
    ({"available": True, "running": False}, False),
    ({"available": True, "running": True}, True),
    ({"available": False, "running": None}, True),
])
def test_dashboard_automation_uses_the_same_strict_prerequisite(web, monkeypatch, mode, status, blocked):
    from claude_swap.codex import autoswitch

    tick = Mock(return_value=SimpleNamespace(reason="No switch needed", action=SimpleNamespace(value="noop")))
    monkeypatch.setattr(autoswitch, "run_once", tick)
    scanned = []

    def readiness():
        scanned.append(web.state._lock._is_owned())
        return status

    web.state.codex_desktop.status = readiness
    completed = threading.Event()
    original_event = web.state.actions.auto._event

    def observe(provider, message, kind, at=None):
        original_event(provider, message, kind, at)
        if kind != "mode":
            completed.set()

    monkeypatch.setattr(web.state.actions.auto, "_event", observe)
    try:
        web.state.configure_auto("codex", mode, confirm=True)
        assert completed.wait(3)
    finally:
        web.state.configure_auto("codex", "stopped")
    assert scanned == [True]
    if blocked:
        tick.assert_not_called()
        assert any(event["kind"] == "blocked" for event in web.state.actions.auto.status("codex")["events"])
    else:
        tick.assert_called_once()
    assert web.state._codex.calls == []
