"""Live Codex auth, not the saved roster marker, determines the active slot."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from claude_swap.codex import autoswitch, cli
from claude_swap.codex.auth_file import CodexAuthError, read_auth, write_auth
from claude_swap.codex.autoswitch import Action, run_once
from claude_swap.codex.usage import CodexUsage, CodexWindow
from claude_swap.exceptions import SwitchError
from claude_swap.providers import ProviderActions
from claude_swap.web.server import DashboardState
from tests.test_codex_switcher import auth_for, env
from tests.test_web import dashboard, get, post


@pytest.fixture
def live_codex(env, monkeypatch):
    env.login_as(account_id="acct-1", email="one@example.com")
    env.switcher.add_current()
    env.login_as(account_id="acct-2", email="two@example.com")
    env.switcher.add_current()
    env.login_as(account_id="acct-1", email="one@example.com", refresh="rotated")
    usage = {
        "1": CodexUsage(allowed=True, windows=(CodexWindow(95, 604800),)),
        "2": CodexUsage(allowed=True, windows=(CodexWindow(5, 604800),)),
    }
    monkeypatch.setattr(env.switcher, "usage_all", Mock(return_value=usage))
    monkeypatch.setattr(env.switcher, "usage_for", lambda number: usage[number])
    monkeypatch.setattr(cli, "CodexSwitcher", lambda: env.switcher)
    monkeypatch.setattr(autoswitch, "running_codex_processes", list)
    monkeypatch.setattr("claude_swap.providers.running_codex_processes", list)
    return env


def test_status_tracks_external_logins_without_writing(live_codex):
    env = live_codex
    saved = {p: p.read_bytes() for p in env.store.root.rglob("*") if p.is_file()}
    live = env.auth_path.read_bytes()

    status = env.switcher.status()
    assert status.logged_in and status.is_managed
    assert status.active_number == status.account.number == "1"
    assert status.identity.account_id == "acct-1"
    assert env.auth_path.read_bytes() == live
    assert env.store.read_credentials("1")["tokens"]["refresh_token"] == "rt-v1"

    env.login_as(account_id="acct-2", email="two@example.com")
    assert env.switcher.status().active_number == "2"
    env.login_as(account_id="acct-1", email="one@example.com")
    assert env.switcher.status().active_number == "1"
    assert env.store.active_number() == "2"
    assert {p: p.read_bytes() for p in env.store.root.rglob("*") if p.is_file()} == saved


@pytest.mark.parametrize("auth,logged_in", [
    (None, False),
    ({}, False),
    (auth_for(account_id="stranger", email="outside@example.com"), True),
    (auth_for(account_id="", email="one@example.com"), True),
    ({"tokens": {"access_token": "opaque"}}, True),
    ({"OPENAI_API_KEY": "synthetic-api-key"}, True),
])
def test_status_has_no_active_slot_without_a_known_managed_login(live_codex, auth, logged_in):
    env = live_codex
    if auth is None:
        env.logout()
    else:
        write_auth(auth, env.auth_path)
    status = env.switcher.status()
    assert status.logged_in is logged_in
    assert status.active_number is None
    assert status.account is None and not status.is_managed
    assert env.store.active_number() == "2"
    assert read_auth(env.auth_path) == auth


@pytest.mark.parametrize("contents", ["", '{"tokens":', "[]"])
def test_status_keeps_persistently_torn_auth_an_error(live_codex, contents):
    env = live_codex
    env.auth_path.write_text(contents)
    with pytest.raises(CodexAuthError, match="Refusing to treat it as logged out"):
        env.switcher.status()
    assert env.store.active_number() == "2"
    assert env.auth_path.read_text() == contents


def test_status_keeps_unreadable_auth_an_error(live_codex, monkeypatch):
    original = Path.read_text

    def read(path, *args, **kwargs):
        if path == live_codex.auth_path:
            raise PermissionError("permission denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    with pytest.raises(CodexAuthError, match="permission denied"):
        live_codex.switcher.status()
    assert live_codex.store.active_number() == "2"


@pytest.mark.parametrize("command", ["status", "list"])
def test_cli_json_uses_live_active_slot(live_codex, capsys, command):
    cli.codex_command([command, "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["activeNumber"] == "1"
    if command == "status":
        assert payload["account"]["number"] == "1"
    assert live_codex.store.active_number() == "2"


@pytest.mark.parametrize("command", ["list", "usage"])
def test_cli_text_marks_only_the_live_account(live_codex, capsys, command):
    cli.codex_command([command])
    lines = capsys.readouterr().out.splitlines()
    assert "*" in next(line for line in lines if "one@example.com" in line)
    assert "*" not in next(line for line in lines if "two@example.com" in line)
    assert live_codex.store.active_number() == "2"


@pytest.mark.parametrize("command", ["status", "list"])
@pytest.mark.parametrize("logged_out", [False, True])
def test_cli_json_does_not_mark_a_saved_slot_without_live_match(live_codex, capsys, command, logged_out):
    if logged_out:
        live_codex.logout()
    else:
        live_codex.login_as(account_id="stranger", email="outside@example.com")
    cli.codex_command([command, "--json"])
    assert json.loads(capsys.readouterr().out)["activeNumber"] is None


@pytest.mark.parametrize("command", ["status", "list", "usage"])
def test_cli_reports_unreadable_auth_as_an_error(live_codex, capsys, command):
    live_codex.auth_path.write_text('{"tokens":')
    args = [command] if command == "usage" else [command, "--json"]
    with pytest.raises(SystemExit) as caught:
        cli.codex_command(args)
    assert caught.value.code == 1
    output = capsys.readouterr()
    if command == "usage":
        assert "Refusing to treat it as logged out" in output.err
    else:
        assert "Refusing to treat it as logged out" in json.loads(output.out)["error"]


def test_switch_best_does_not_mistake_saved_target_for_live_account(live_codex):
    actions = ProviderActions(codex=live_codex.switcher)
    try:
        result = actions.switch_best("codex")
        assert result["ok"] and result["switched"]
        assert live_codex.switcher.status().active_number == "2"
        assert live_codex.store.read_credentials("1")["tokens"]["refresh_token"] == "rotated"
    finally:
        actions.close()


@pytest.mark.parametrize("other_used", [5, 95])
def test_switch_best_prefers_the_live_account_on_equal_or_better_quota(live_codex, other_used):
    usage = live_codex.switcher.usage_all.return_value
    usage["1"] = CodexUsage(allowed=True, windows=(CodexWindow(5, 604800),))
    usage["2"] = CodexUsage(allowed=True, windows=(CodexWindow(other_used, 604800),))
    actions = ProviderActions(codex=live_codex.switcher)
    before = live_codex.auth_path.read_bytes()
    try:
        result = actions.switch_best("codex")
        assert result["ok"] and not result["switched"]
        assert result["reason"] == "already-active"
        assert live_codex.auth_path.read_bytes() == before
        assert live_codex.store.active_number() == "2"
    finally:
        actions.close()


@pytest.mark.parametrize("dry_run", [False, True])
def test_automation_selects_using_live_account(live_codex, dry_run):
    decision = run_once(live_codex.switcher, now=1000, dry_run=dry_run)
    assert decision.action is Action.SWITCH
    assert decision.active.account.number == "1"
    assert decision.target.account.number == "2"
    assert live_codex.switcher.status().active_number == ("1" if dry_run else "2")
    assert live_codex.store.active_number() == "2"
    assert live_codex.store.read_credentials("1")["tokens"]["refresh_token"] == (
        "rt-v1" if dry_run else "rotated"
    )


def test_automation_holds_actual_healthy_login_after_external_change(live_codex):
    live_codex.store.set_active("1")
    live_codex.login_as(account_id="acct-2", email="two@example.com")
    decision = run_once(live_codex.switcher, now=1000)
    assert decision.action is Action.HOLD
    assert decision.active.account.number == "2"
    assert live_codex.store.active_number() == "1"


@pytest.mark.parametrize("automatic", [False, True])
@pytest.mark.parametrize("logged_out", [False, True])
def test_selection_without_managed_live_login_preserves_switch_safeguards(live_codex, automatic, logged_out):
    if logged_out:
        live_codex.logout()
    else:
        live_codex.login_as(account_id="stranger", email="outside@example.com")
    before = read_auth(live_codex.auth_path)
    actions = ProviderActions(codex=live_codex.switcher)
    try:
        if logged_out:
            result = run_once(live_codex.switcher) if automatic else actions.switch_best("codex")
            assert result.should_switch if automatic else result["switched"]
            assert live_codex.switcher.status().active_number == "2"
        else:
            with pytest.raises(SwitchError, match="not managed"):
                run_once(live_codex.switcher) if automatic else actions.switch_best("codex")
            assert read_auth(live_codex.auth_path) == before
    finally:
        actions.close()


@pytest.mark.parametrize("automatic", [False, True])
@pytest.mark.parametrize("usage_available", [False, True])
def test_selection_refuses_unreadable_live_auth(live_codex, automatic, usage_available):
    live_codex.auth_path.write_text('{"tokens":')
    if not usage_available:
        live_codex.switcher.usage_all.return_value = {
            "1": CodexAuthError("unreadable auth"), "2": CodexAuthError("unreadable auth"),
        }
    actions = ProviderActions(codex=live_codex.switcher)
    try:
        with pytest.raises(CodexAuthError):
            run_once(live_codex.switcher) if automatic else actions.switch_best("codex")
        assert live_codex.auth_path.read_text() == '{"tokens":'
        assert live_codex.store.active_number() == "2"
    finally:
        actions.close()


def test_dashboard_reuses_one_live_status_and_does_not_mutate(live_codex, monkeypatch):
    status = Mock(wraps=live_codex.switcher.status)
    monkeypatch.setattr(live_codex.switcher, "status", status)
    state = DashboardState(codex_switcher=live_codex.switcher)
    before = live_codex.auth_path.read_bytes()
    try:
        codex = state.get()["codex"]
        status.assert_called_once_with()
        assert codex["activeNumber"] == "1"
        assert [a["number"] for a in codex["accounts"] if a["active"]] == ["1"]
        assert codex["liveLogin"]["email"] == "one@example.com"
        assert codex["liveLogin"]["managed"]
        assert live_codex.store.active_number() == "2"
        assert live_codex.auth_path.read_bytes() == before
    finally:
        state.actions.close()


@pytest.mark.parametrize("logged_out", [False, True])
def test_dashboard_has_no_active_row_without_a_managed_live_login(live_codex, logged_out):
    if logged_out:
        live_codex.logout()
    else:
        live_codex.login_as(account_id="stranger", email="outside@example.com")
    state = DashboardState(codex_switcher=live_codex.switcher)
    try:
        codex = state.get()["codex"]
        assert codex["available"] and codex["activeNumber"] is None
        assert not any(account["active"] for account in codex["accounts"])
        if logged_out:
            assert codex["liveLogin"] is None
        else:
            assert codex["liveLogin"]["email"] == "outside@example.com"
            assert not codex["liveLogin"]["managed"]
    finally:
        state.actions.close()


def test_dashboard_surfaces_torn_auth_instead_of_showing_stale_active(live_codex):
    state = DashboardState(codex_switcher=live_codex.switcher)
    try:
        assert state.get()["codex"]["activeNumber"] == "1"
        live_codex.auth_path.write_text('{"tokens":')
        codex = state.get(force=True)["codex"]
        assert not codex["available"]
        assert "Refusing to treat it as logged out" in codex["error"]
        assert codex["accounts"] == []
        assert "activeNumber" not in codex and "liveLogin" not in codex
    finally:
        state.actions.close()


def test_dashboard_refreshes_live_account_after_already_active_switch(live_codex, dashboard):
    live_codex.login_as(account_id="acct-2", email="two@example.com")
    base, token, _ = dashboard(codex=live_codex.switcher)
    assert json.loads(get(base, "/api/state", token=token)[1])["codex"]["activeNumber"] == "2"
    live_codex.login_as(account_id="acct-1", email="one@example.com", refresh="rotated")
    before = live_codex.auth_path.read_bytes()
    code, response = post(base, "/api/switch", {"provider": "codex", "number": "1"}, token)
    assert code == 400 and not response["ok"]
    assert "already the active Codex account" in response["message"]
    codex = json.loads(get(base, "/api/state", token=token)[1])["codex"]
    assert codex["activeNumber"] == "1"
    assert [a["number"] for a in codex["accounts"] if a["active"]] == ["1"]
    assert live_codex.auth_path.read_bytes() == before
    assert live_codex.store.active_number() == "2"


@pytest.mark.asyncio
async def test_tui_initial_and_refreshed_snapshots_follow_live_auth(live_codex, monkeypatch, tmp_path):
    from claude_swap.tui import codex as codex_module
    from claude_swap.tui.codex import CodexScreen
    from tests.test_tui import FakeSwitcher, make_app, settle

    monkeypatch.setattr(codex_module, "CodexSwitcher", lambda: live_codex.switcher)
    screen = CodexScreen()
    screen._rebuild()
    assert screen.snapshot.active_number == "1"
    assert [a.number for a in screen.snapshot.accounts if a.is_active] == ["1"]

    app = make_app(FakeSwitcher([], tmp_path))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        app.push_screen(screen)
        await settle(pilot)
        assert screen.snapshot.active_number == "1"
        live_codex.login_as(account_id="acct-2", email="two@example.com")
        screen.request_refresh()
        await settle(pilot)
        assert screen.snapshot.active_number == "2"
        live_codex.logout()
        screen.request_refresh()
        await settle(pilot)
        assert screen.snapshot.active_number is None
        assert not any(a.is_active for a in screen.snapshot.accounts)
        live_codex.login_as(account_id="stranger", email="outside@example.com")
        screen.request_refresh()
        await settle(pilot)
        assert screen.snapshot.active_number is None
        assert not any(a.is_active for a in screen.snapshot.accounts)
        assert live_codex.store.active_number() == "2"


@pytest.mark.asyncio
@pytest.mark.parametrize("initially_torn", [False, True])
async def test_tui_surfaces_unreadable_auth_without_stale_active(live_codex, monkeypatch, tmp_path, initially_torn):
    from textual.widgets import Static

    from claude_swap.tui import codex as codex_module
    from claude_swap.tui.codex import CodexScreen
    from tests.test_tui import FakeSwitcher, make_app, settle

    monkeypatch.setattr(codex_module, "CodexSwitcher", lambda: live_codex.switcher)
    if initially_torn:
        live_codex.auth_path.write_text('{"tokens":')
    app = make_app(FakeSwitcher([], tmp_path))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        screen = CodexScreen()
        app.push_screen(screen)
        await settle(pilot)
        if not initially_torn:
            assert screen.snapshot.active_number == "1"
            live_codex.auth_path.write_text('{"tokens":')
            screen.request_refresh()
            await settle(pilot)
        assert screen.snapshot is None
        assert "Refusing to treat it as logged out" in screen.query_one("#codex-status", Static).render().plain
        assert live_codex.store.active_number() == "2"
