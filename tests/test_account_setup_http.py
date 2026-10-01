"""Authenticated import and enrollment boundaries over a real local HTTP server."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agents_switcher.exceptions import MigrationError, ValidationError
from agents_switcher.codex.enrollment import EnrollmentError
from agents_switcher.codex.store import CodexAccountStore
from agents_switcher.codex.switcher import CodexSwitcher
from agents_switcher.codex.usage import UsageError, UsageLoginRequiredError
from agents_switcher.web.server import DashboardState, serve
from tests.test_codex_enrollment import env, prepare, save_current, snapshot
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
    web.state.codex_enrollment.open_browser.return_value = {"ok": True, "status": "waiting"}
    web.state.codex_enrollment.status.return_value = {"ok": True, "status": "ready"}
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
    ("codex/login/open", {"sessionId": "synthetic-session", "confirm": True}),
    ("codex/login/status", {"sessionId": "synthetic-session", "confirm": True}),
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


@pytest.mark.parametrize("route,method", [("open", "open_browser"), ("status", "status")])
def test_browser_open_and_status_cannot_save_activate_or_collect_usage(setup_web, route, method):
    setup_web.state.get = Mock(side_effect=AssertionError("Must not collect quota"))
    setup_web.state.codex_desktop.status.side_effect = AssertionError("Must not inspect processes for sign-in")
    code, result, _ = request(setup_web, f"/api/codex/login/{route}", {"sessionId": "synthetic-session", "confirm": True})
    assert code == 200 and result["ok"]
    getattr(setup_web.state.codex_enrollment, method).assert_called_once_with("synthetic-session", confirm=True)
    setup_web.state.codex_enrollment.complete.assert_not_called()
    setup_web.state.codex_enrollment.activate.assert_not_called()
    setup_web.state.get.assert_not_called()


def test_browser_save_only_is_allowed_while_codex_runs_and_retains_pending_activation(setup_web):
    setup_web.state.codex_desktop.status.side_effect = AssertionError("Save-only must not need process readiness")
    setup_web.state._cached = {"old": True}
    revision = setup_web.state.actions.revision
    code, result, _ = request(setup_web, "/api/codex/login/complete", {
        "sessionId": "synthetic-session", "confirm": True, "activate": False,
    })
    assert code == 200 and result["ok"] and result["activationRequired"] is True
    setup_web.state.codex_enrollment.complete.assert_called_once_with("synthetic-session", confirm=True)
    setup_web.state.codex_enrollment.activate.assert_not_called()
    assert setup_web.state._cached is None
    assert setup_web.state.actions.revision == revision + 1


@pytest.mark.parametrize("activate", [None, "false", 0, 1, [], {}])
def test_browser_activation_option_requires_an_exact_boolean(setup_web, activate):
    code, result, _ = request(setup_web, "/api/codex/login/complete", {
        "sessionId": "synthetic-session", "confirm": True, "activate": activate,
    })
    assert code == 400 and "boolean" in result["message"]
    setup_web.state.codex_enrollment.complete.assert_not_called()
    setup_web.state.codex_enrollment.activate.assert_not_called()


def test_wrong_browser_repair_account_offers_restart_not_retrying_the_same_save(setup_web):
    setup_web.state.codex_enrollment.complete.side_effect = EnrollmentError(
        "The sign-in belongs to a different Codex account. Nothing was saved.", code="wrong-account",
    )
    code, result, _ = request(setup_web, "/api/codex/login/complete", {
        "sessionId": "synthetic-session", "confirm": True, "activate": False,
    })
    assert code == 400 and result["code"] == "wrong-account"
    assert "Nothing was saved" in result["message"]
    setup_web.state.codex_enrollment.activate.assert_not_called()


@pytest.mark.parametrize("route", ["open", "status", "cancel"])
def test_browser_controls_remain_responsive_outside_the_quota_action_lock(setup_web, route):
    with setup_web.state._lock:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(request, setup_web, f"/api/codex/login/{route}", {
                "sessionId": "synthetic-session", "confirm": True,
            })
            assert pending.result(timeout=2)[0] == 200


@pytest.mark.parametrize("permanent", [True, False])
def test_private_account_state_distinguishes_signin_required_from_transient_errors(web, permanent):
    web.state._codex.usage["1"] = (
        UsageLoginRequiredError("Sign in again to repair this login.") if permanent
        else UsageError("The provider could not be reached.")
    )
    code, state, _ = request(web, "/api/state", method="GET")
    assert code == 200
    account = state["codex"]["accounts"][0]
    assert account["loginRequired"] is permanent
    assert account["error"]
    assert len(state["codex"]["accounts"]) == 3


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


@pytest.fixture
def pending_repair_web(env, monkeypatch):
    _, original = save_current(env)
    session_id, home = prepare(env, number="1")
    repaired = env.authority.sign_in(home, "one", "repaired")
    env.controller.complete(session_id, confirm=True)
    env.controller.cancel(session_id, confirm=True)
    env.controller.close()
    switcher = CodexSwitcher(CodexAccountStore(env.store.root))
    monkeypatch.setattr(switcher, "usage_all", lambda: {})
    monkeypatch.setattr("agents_switcher.providers.running_codex_processes", list)
    state = DashboardState(codex_switcher=switcher)
    state.codex_desktop.status = Mock(return_value={"available": True, "running": False})
    server, _ = serve(state, host="127.0.0.1", port=0, token="test-web-auth")
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    yield SimpleNamespace(state=state, server=server, token="test-web-auth", env=env,
                          original=original, repaired=repaired)
    server.shutdown()
    server.server_close()
    thread.join(3)


def test_saved_repair_can_be_activated_after_cancellation_and_server_restart(pending_repair_web):
    service = pending_repair_web
    assert service.state.codex_enrollment._session is None
    code, state, _ = request(service, "/api/state", method="GET")
    assert code == 200
    account = state["codex"]["accounts"][0]
    assert account["active"] is True and account["activationRequired"] is True
    assert service.repaired["tokens"]["refresh_token"] not in json.dumps(state)
    code, result, _ = request(service, "/api/switch", {"provider": "codex", "number": "1", "useSavedLogin": True})
    assert code == 200 and result["ok"] and result["switched"]
    assert snapshot(service.env)[0] == service.repaired
    assert service.env.store.read_credentials("1") == service.repaired
    assert service.state._codex._pending_imports() == {}
    _, state, _ = request(service, "/api/state", method="GET")
    assert state["codex"]["accounts"][0]["activationRequired"] is False


@pytest.mark.parametrize("status,code", [
    ({"available": True, "running": True}, "codex-running"),
    ({"available": False, "running": None}, "codex-status-unknown"),
])
def test_saved_repair_activation_still_requires_confirmed_exit(pending_repair_web, status, code):
    service = pending_repair_web
    before = snapshot(service.env)
    service.state.codex_desktop.status.return_value = status
    response, result, _ = request(service, "/api/switch", {"provider": "codex", "number": "1", "useSavedLogin": True})
    assert response == 400 and result["code"] == code
    assert snapshot(service.env) == before
    assert service.state._codex._pending_imports() == {"1": "one"}


def test_add_existing_login_cannot_replace_the_saved_repair(pending_repair_web):
    service = pending_repair_web
    before = snapshot(service.env)
    code, result, _ = request(service, "/api/add", {"provider": "codex"})
    assert code == 400 and "saved login awaiting activation" in result["message"]
    assert snapshot(service.env) == before
    assert service.state._codex._pending_imports() == {"1": "one"}


@pytest.mark.parametrize("value", [None, "true", 1, [], {}])
def test_saved_login_switch_intent_is_an_exact_boolean(web, value):
    code, _, _ = request(web, "/api/switch", {"provider": "codex", "number": "1", "useSavedLogin": value})
    assert code == 400
    assert web.state._codex.calls == []


def test_saved_login_switch_intent_is_codex_only(web):
    code, _, _ = request(web, "/api/switch", {"provider": "claude", "number": "1", "useSavedLogin": True})
    assert code == 400
    assert web.state._claude.calls == []
