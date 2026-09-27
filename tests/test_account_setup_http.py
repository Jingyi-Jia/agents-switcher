"""Authenticated import and enrollment boundaries over a real local HTTP server."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from agents_switcher.exceptions import MigrationError, ValidationError
from tests.test_web_actions import request, web


@pytest.fixture
def setup_web(web):
    web.state.codex_enrollment.close()
    web.state.codex_enrollment = Mock()
    web.state.codex_enrollment.prepare.return_value = {
        "ok": True, "sessionId": "synthetic-session", "command": "isolated command", "shell": "posix",
    }
    web.state.codex_enrollment.complete.return_value = {
        "ok": True, "account": {"number": "2", "email": "two@example.test"}, "activationRequired": True,
    }
    web.state.codex_enrollment.activate.return_value = {"ok": True, "switched": True, "message": "Saved and switched."}
    web.state.codex_enrollment.cancel.return_value = {"ok": True}
    web.state.legacy_import = Mock()
    web.state.legacy_import.status.return_value = {
        "destination": "/synthetic/agents-switcher", "sources": [], "imported": False, "canImport": False, "warnings": [],
    }
    web.state.legacy_import.import_accounts.return_value = {
        "ok": True, "counts": {"claude": 1, "codex": 2}, "warnings": [], "message": "Imported without modifying the source.",
    }
    return web


ROUTES = [
    ("codex/login/prepare", {"confirm": True}),
    ("codex/login/complete", {"sessionId": "synthetic-session", "confirm": True}),
    ("codex/login/cancel", {"sessionId": "synthetic-session", "confirm": True}),
    ("storage/import", {"source": "legacy", "confirm": True}),
]


@pytest.mark.parametrize("route,payload", ROUTES)
@pytest.mark.parametrize("auth", [False, "wrong"])
def test_all_setup_mutations_require_header_auth(setup_web, route, payload, auth):
    assert request(setup_web, f"/api/{route}?token={setup_web.token}", payload, token=auth)[0] == 403
    assert setup_web.state.codex_enrollment.mock_calls == []
    assert setup_web.state.legacy_import.mock_calls == []


@pytest.mark.parametrize("route,payload", ROUTES)
@pytest.mark.parametrize("confirmation", [False, None, "true", 1])
def test_setup_confirmation_must_be_exact_true(setup_web, route, payload, confirmation):
    assert request(setup_web, f"/api/{route}", {**payload, "confirm": confirmation})[0] == 400
    assert setup_web.state.codex_enrollment.mock_calls == []
    assert setup_web.state.legacy_import.mock_calls == []


@pytest.mark.parametrize("route,payload", ROUTES)
@pytest.mark.parametrize("field", ["path", "executable", "auth", "token", "url", "pid"])
def test_setup_never_accepts_request_selected_credentials_or_paths(setup_web, route, payload, field):
    assert request(setup_web, f"/api/{route}", {**payload, field: "untrusted"})[0] == 400
    assert setup_web.state.codex_enrollment.mock_calls == []
    assert setup_web.state.legacy_import.mock_calls == []


def test_storage_discovery_is_header_only_and_does_not_collect_provider_usage(setup_web):
    setup_web.state.get = Mock(side_effect=AssertionError("Must not collect provider data"))
    assert request(setup_web, f"/api/storage?token={setup_web.token}", token=False, method="GET")[0] == 403
    assert request(setup_web, "/api/storage?path=/other", method="GET")[0] == 400
    status, payload, headers = request(setup_web, "/api/storage", method="GET")
    assert status == 200
    assert payload["destination"] == "/synthetic/agents-switcher"
    assert headers["Cache-Control"] == "no-store"
    setup_web.state.legacy_import.status.assert_called_once_with()
    setup_web.state.get.assert_not_called()


def test_preparation_does_not_require_quitting_or_start_a_provider_process(setup_web):
    setup_web.state.codex_desktop.status.side_effect = AssertionError("No switch occurs during preparation")
    status, result, _ = request(setup_web, "/api/codex/login/prepare", {"number": "2", "confirm": True})
    assert status == 200 and result["sessionId"] == "synthetic-session"
    setup_web.state.codex_enrollment.prepare.assert_called_once_with(number="2", confirm=True)
    setup_web.state.codex_enrollment.complete.assert_not_called()
    setup_web.state.codex_enrollment.activate.assert_not_called()
    assert setup_web.state._codex.calls == []


@pytest.mark.parametrize("status,code", [
    ({"available": True, "running": True}, "codex-running"),
    ({"available": False, "running": False}, "codex-status-unknown"),
    ({"available": True, "running": None}, "codex-status-unknown"),
])
def test_save_and_switch_requires_confirmed_codex_exit_before_either_action(setup_web, status, code):
    setup_web.state.codex_desktop.status.return_value = status
    response, result, _ = request(setup_web, "/api/codex/login/complete", {"sessionId": "synthetic-session", "confirm": True})
    assert response == 400
    assert result["kind"] == "action-required" and result["code"] == code
    setup_web.state.codex_enrollment.complete.assert_not_called()
    setup_web.state.codex_enrollment.activate.assert_not_called()


def test_confirmed_completion_saves_then_activates_the_same_pinned_session(setup_web):
    setup_web.state._cached = {"old": True}
    code, result, _ = request(setup_web, "/api/codex/login/complete", {"sessionId": "synthetic-session", "confirm": True})
    assert code == 200 and result["ok"] and result["switched"]
    assert result["activationRequired"] is False
    assert result["account"]["number"] == "2"
    calls = setup_web.state.codex_enrollment.mock_calls
    assert [call[0] for call in calls] == ["complete", "activate"]
    assert all(call.args == ("synthetic-session",) and call.kwargs == {"confirm": True} for call in calls)
    assert setup_web.state._cached is None


def test_failed_capture_cannot_activate_and_successful_capture_survives_activation_failure(setup_web):
    setup_web.state.codex_enrollment.complete.side_effect = ValidationError("Sign in to the expected account.")
    code, result, _ = request(setup_web, "/api/codex/login/complete", {"sessionId": "synthetic-session", "confirm": True})
    assert code == 400 and "expected account" in result["message"]
    setup_web.state.codex_enrollment.activate.assert_not_called()
    setup_web.state.codex_enrollment.complete.side_effect = None
    setup_web.state.codex_enrollment.activate.side_effect = ValidationError("Quit all Codex sessions before retrying.")
    code, result, _ = request(setup_web, "/api/codex/login/complete", {"sessionId": "synthetic-session", "confirm": True})
    assert code == 200 and result["ok"] is False
    assert result["activationRequired"] is True
    assert result["account"]["number"] == "2"
    assert "login was saved" in result["message"]
    setup_web.state.codex_enrollment.cancel.assert_not_called()


def test_import_cannot_run_while_this_sessions_automation_is_enabled(setup_web):
    setup_web.state.actions.auto.status = Mock(return_value={"mode": "dry-run"})
    code, result, _ = request(setup_web, "/api/storage/import", {"source": "legacy", "confirm": True})
    assert code == 400 and "Stop this session" in result["message"]
    setup_web.state.legacy_import.import_accounts.assert_not_called()


def test_import_calls_only_explicit_importer_and_invalidates_cached_state(setup_web):
    setup_web.state._cached = {"old": True}
    code, result, _ = request(setup_web, "/api/storage/import", {"source": "xdg", "confirm": True})
    assert code == 200 and result["counts"] == {"claude": 1, "codex": 2}
    setup_web.state.legacy_import.import_accounts.assert_called_once_with("xdg", confirm=True)
    assert setup_web.state._cached is None
    assert setup_web.state._claude.calls == setup_web.state._codex.calls == []


def test_import_and_enrollment_failures_are_sanitized(setup_web):
    secret = "refresh_token=synthetic-sensitive-value"
    setup_web.state.legacy_import.import_accounts.side_effect = MigrationError(secret)
    setup_web.state.codex_enrollment.prepare.side_effect = RuntimeError(secret)
    for path, payload in [("/api/storage/import", {"source": "legacy", "confirm": True}), ("/api/codex/login/prepare", {"confirm": True})]:
        code, result, _ = request(setup_web, path, payload)
        assert code == 400
        assert secret not in json.dumps(result)


@pytest.mark.parametrize("action", ["import", "complete"])
def test_import_and_enrollment_share_the_provider_action_lock(setup_web, action):
    entered, release, other_started = threading.Event(), threading.Event(), threading.Event()

    def paused(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return {"ok": True, "account": {"number": "2"}}

    if action == "import":
        setup_web.state.legacy_import.import_accounts.side_effect = paused
        path, payload = "/api/storage/import", {"source": "legacy", "confirm": True}
    else:
        setup_web.state.codex_enrollment.complete.side_effect = paused
        path, payload = "/api/codex/login/complete", {"sessionId": "synthetic-session", "confirm": True}
    original = setup_web.state._codex.add_current

    def add(**kwargs):
        other_started.set()
        return original(**kwargs)

    setup_web.state._codex.add_current = add
    with ThreadPoolExecutor(max_workers=2) as pool:
        changing = pool.submit(request, setup_web, path, payload)
        assert entered.wait(2)
        other = pool.submit(request, setup_web, "/api/add", {"provider": "codex"})
        assert not other_started.wait(0.1)
        release.set()
        assert changing.result()[0] == 200
        assert other.result()[0] == 200
    assert other_started.is_set()


def test_server_shutdown_discards_owned_enrollment_state(setup_web):
    setup_web.server.shutdown()
    setup_web.server.server_close()
    setup_web.state.codex_enrollment.close.assert_called_once_with()
