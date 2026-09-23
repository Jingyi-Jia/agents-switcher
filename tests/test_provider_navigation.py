"""Provider dispatch and lazy, independent TUI navigation."""

from __future__ import annotations

import sys
from dataclasses import replace
from unittest.mock import MagicMock

import pytest
from textual.widgets import Input, ListView, Static

from claude_swap import cli, tui
from claude_swap.codex import cli as codex_cli
from claude_swap.codex import autoswitch as codex_auto
from claude_swap.codex.switcher import SwitchResult
from claude_swap.tui import app as app_module
from claude_swap.tui import codex as codex_module
from claude_swap.tui.app import CswapApp
from claude_swap.tui.codex import CodexScreen
from claude_swap.tui.dashboard import DashboardScreen
from claude_swap.tui.providers import ProviderScreen
from claude_swap.tui.widgets import MenuItem
from tests.test_codex_tui import StubCodexSwitcher, account, healthy, on_credits
from tests.test_tui import FakeSwitcher, make_account, settle


@pytest.mark.parametrize("prefix", [[], ["claude"]])
def test_claude_list_preserves_legacy_dispatch(monkeypatch, tmp_path, prefix):
    switcher = MagicMock(backup_dir=tmp_path)
    switcher.list_accounts.return_value = {"accounts": []}
    monkeypatch.setattr(cli, "ClaudeAccountSwitcher", lambda **_: switcher)
    monkeypatch.setattr(sys, "argv", ["agent-switch", *prefix, "list", "--json"])
    cli.main()
    switcher.list_accounts.assert_called_once_with(
        show_token_status=False, json_output=True
    )


def test_bare_explicit_claude_defaults_to_status(monkeypatch, tmp_path):
    switcher = MagicMock(backup_dir=tmp_path)
    monkeypatch.setattr(cli, "ClaudeAccountSwitcher", lambda **_: switcher)
    monkeypatch.setattr(sys, "argv", ["agent-switch", "claude"])
    monkeypatch.setattr("claude_swap.update_check.check_for_update", lambda _: None)
    cli.main()
    switcher.status.assert_called_once_with(json_output=False)


@pytest.mark.parametrize(
    "command",
    ["run", "auto", "config", "map", "unmap", "alias", "swap", "move", "unclaimed"],
)
def test_explicit_claude_pre_dispatch(monkeypatch, command):
    handler = MagicMock()
    monkeypatch.setattr(cli, f"_{command}_command", handler)
    monkeypatch.setattr(sys, "argv", ["agent-switch", "claude", command, "--help"])
    cli.main()
    handler.assert_called_once_with(["--help"])


@pytest.mark.parametrize(
    "argv,start",
    [
        (["tui"], "dashboard"),
        (["--tui"], "dashboard"),
        (["codex", "tui"], "codex"),
        ([], "dashboard"),
    ],
)
def test_generic_tui_never_initializes_claude(monkeypatch, argv, start):
    constructor = MagicMock(side_effect=RuntimeError("Claude unavailable"))
    run = MagicMock(return_value=0)
    monkeypatch.setattr(cli, "ClaudeAccountSwitcher", constructor)
    monkeypatch.setattr(tui, "run", run)
    monkeypatch.setattr(sys, "argv", ["agent-switch", *argv])
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    constructor.assert_not_called()
    if start == "codex":
        run.assert_called_once_with(None, start="codex")
    else:
        run.assert_called_once_with(None)


def test_explicit_claude_tui_keeps_direct_dashboard(monkeypatch, tmp_path):
    switcher = MagicMock(backup_dir=tmp_path)
    run = MagicMock(return_value=0)
    monkeypatch.setattr(cli, "ClaudeAccountSwitcher", lambda **_: switcher)
    monkeypatch.setattr(tui, "run", run)
    monkeypatch.setattr(sys, "argv", ["agent-switch", "claude", "tui"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    run.assert_called_once_with(switcher)


def test_explicit_codex_dispatch_does_not_touch_claude(monkeypatch):
    constructor = MagicMock(side_effect=RuntimeError("Claude unavailable"))
    command = MagicMock()
    monkeypatch.setattr(cli, "ClaudeAccountSwitcher", constructor)
    monkeypatch.setattr(codex_cli, "codex_command", command)
    monkeypatch.setattr(sys, "argv", ["agent-switch", "codex", "list", "--json"])
    cli.main()
    command.assert_called_once_with(["list", "--json"])
    constructor.assert_not_called()


@pytest.mark.asyncio
async def test_provider_chooser_codex_and_back_without_claude(monkeypatch):
    constructor = MagicMock(side_effect=RuntimeError("Claude unavailable"))
    monkeypatch.setattr(app_module, "ClaudeAccountSwitcher", constructor)
    monkeypatch.setattr(codex_module, "CodexSwitcher", StubCodexSwitcher)
    app = CswapApp()
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        assert app.title == "agent-switch"
        assert isinstance(app.screen, ProviderScreen)
        assert [item.action_id for item in app.screen.query(MenuItem)] == [
            "claude",
            "codex",
        ]
        await pilot.press("down", "enter")
        await settle(pilot)
        assert isinstance(app.screen, CodexScreen)
        assert app.switcher is None and app.source is None
        constructor.assert_not_called()
        await pilot.press("escape")
        await settle(pilot)
        assert isinstance(app.screen, ProviderScreen)


@pytest.mark.asyncio
async def test_failed_claude_initialization_does_not_block_codex(monkeypatch):
    constructor = MagicMock(side_effect=RuntimeError("Claude unavailable"))
    monkeypatch.setattr(app_module, "ClaudeAccountSwitcher", constructor)
    monkeypatch.setattr(codex_module, "CodexSwitcher", StubCodexSwitcher)
    app = CswapApp()
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.press("c")
        await settle(pilot)
        constructor.assert_called_once()
        assert isinstance(app.screen, ProviderScreen)
        assert app.switcher is None
        await pilot.press("x")
        await settle(pilot)
        assert isinstance(app.screen, CodexScreen)


@pytest.mark.asyncio
async def test_lazy_claude_preserves_actions_and_provider_navigation(
    monkeypatch, tmp_path
):
    switcher = FakeSwitcher([make_account(1, active=True)], tmp_path)
    monkeypatch.setattr(app_module, "ClaudeAccountSwitcher", lambda: switcher)
    monkeypatch.setattr(app_module.os, "geteuid", lambda: 1000, raising=False)
    monkeypatch.setattr(codex_module, "CodexSwitcher", StubCodexSwitcher)
    app = CswapApp()
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.press("c")
        await settle(pilot)
        assert isinstance(app.screen, DashboardScreen)
        assert app.switcher is switcher
        actions = {item.action_id for item in app.screen.query(MenuItem)}
        assert {
            "switch",
            "watch",
            "auto",
            "add-menu",
            "disable-menu",
            "remove-menu",
        } <= actions
        notice = app.screen.query_one("#claude-client-notice", Static).render().plain
        assert "Claude Desktop" in notice and "restarting alone" in notice
        await pilot.press("p")
        await settle(pilot)
        assert isinstance(app.screen, ProviderScreen)
        assert not app._claude_active
        await pilot.press("x")
        await settle(pilot)
        assert isinstance(app.screen, CodexScreen)
        assert not app._claude_active
        await pilot.press("escape")
        await settle(pilot)
        assert isinstance(app.screen, ProviderScreen)
        await pilot.press("c")
        await settle(pilot)
        assert isinstance(app.screen, DashboardScreen)
        assert app._claude_active


class ManagedCodexSwitcher(StubCodexSwitcher):
    def __init__(self):
        super().__init__(
            accounts=[account("1", "one@example.com")], usage={"1": healthy(70)}
        )
        self.calls = []
        self.active = "1"
        self.store.active_number = lambda: self.active

    def add_current(self, *, refresh_existing=False):
        added = account("2", "two@example.com")
        self.calls.append(("add",))
        self._accounts.append(added)
        self._usage["2"] = healthy(10)
        return added

    def remove_account(self, number):
        self.calls.append(("remove", number))
        self._accounts = [acc for acc in self._accounts if acc.number != number]

    def set_account_disabled(self, number, disabled):
        self.calls.append(("disabled", number, disabled))
        self._accounts = [
            replace(acc, disabled=disabled) if acc.number == number else acc
            for acc in self._accounts
        ]
        return next(acc for acc in self._accounts if acc.number == number)

    def switch_to(self, number):
        self.switched_to.append(number)
        self.active = number
        return SwitchResult(
            next(acc for acc in self._accounts if acc.number == number),
            previous=None,
            synced_back=False,
        )


@pytest.fixture
def managed_codex(monkeypatch):
    switcher = ManagedCodexSwitcher()
    monkeypatch.setattr(codex_module, "CodexSwitcher", lambda: switcher)
    return switcher


@pytest.mark.asyncio
async def test_codex_add_and_remove_require_confirmation(managed_codex):
    app = CswapApp(start="codex")
    async with app.run_test(size=(110, 36)) as pilot:
        await settle(pilot)
        screen = app.screen
        assert isinstance(screen, CodexScreen)
        await pilot.click("#codex-add")
        await pilot.press("n")
        await settle(pilot)
        assert managed_codex.calls == []
        await pilot.click("#codex-add")
        await pilot.press("y")
        await settle(pilot)
        assert managed_codex.calls == [("add",)]
        screen.query_one(ListView).index = 1
        await pilot.click("#codex-remove")
        await pilot.press("n")
        await settle(pilot)
        assert ("remove", "2") not in managed_codex.calls
        await pilot.click("#codex-remove")
        await pilot.press("y")
        await settle(pilot)
        assert ("remove", "2") in managed_codex.calls
        assert [acc.number for acc in managed_codex.list_accounts()] == ["1"]


@pytest.mark.asyncio
async def test_codex_disable_enable_and_best_preserve_eligibility(managed_codex):
    managed_codex.add_current()
    managed_codex._accounts.append(account("3", "paid@example.com"))
    managed_codex._usage["3"] = on_credits()
    app = CswapApp(start="codex")
    async with app.run_test(size=(110, 36)) as pilot:
        await settle(pilot)
        screen = app.screen
        screen.query_one(ListView).index = 1
        await pilot.click("#codex-disable")
        await settle(pilot)
        assert managed_codex.list_accounts()[1].disabled
        assert (
            "disabled"
            in screen.query(codex_module.CodexAccountItem)[1]._body.render().plain
        )
        await pilot.click("#codex-best")
        await settle(pilot)
        assert managed_codex.switched_to == []
        await pilot.click("#codex-disable")
        await settle(pilot)
        assert not managed_codex.list_accounts()[1].disabled
        await pilot.click("#codex-best")
        await settle(pilot)
        assert managed_codex.switched_to == ["2"]


@pytest.mark.asyncio
async def test_codex_auto_dry_run_live_stop_and_exit(monkeypatch, managed_codex):
    calls = []

    def tick(switcher, *, settings, dry_run):
        calls.append((settings.threshold, dry_run))
        return codex_auto.AutoDecision(codex_auto.Action.HOLD, "No switch needed")

    monkeypatch.setattr(codex_auto, "run_once", tick)
    app = CswapApp(start="codex")
    async with app.run_test(size=(110, 36)) as pilot:
        await settle(pilot)
        screen = app.screen
        assert screen.actions.auto.status("codex")["mode"] == "stopped"
        screen.query_one(Input).value = "85"
        await pilot.click("#codex-dry-run")
        await settle(pilot)
        assert screen.actions.auto.status("codex")["mode"] == "dry-run"
        assert (85, True) in calls
        await pilot.click("#codex-live")
        await pilot.press("n")
        await settle(pilot)
        assert screen.actions.auto.status("codex")["mode"] == "dry-run"
        await pilot.click("#codex-live")
        await pilot.press("y")
        await settle(pilot)
        assert screen.actions.auto.status("codex")["mode"] == "live"
        assert (85, False) in calls
        screen.query_one(Input).value = ""
        await pilot.click("#codex-stop")
        await settle(pilot)
        assert screen.actions.auto.status("codex")["mode"] == "stopped"
        screen.query_one(Input).value = "85"
        await pilot.click("#codex-dry-run")
        await settle(pilot)
        await pilot.press("p")
        await settle(pilot)
        assert isinstance(app.screen, ProviderScreen)
        assert screen.actions.auto.status("codex")["mode"] == "stopped"
        assert not screen.actions.auto._workers


@pytest.mark.parametrize("command,disabled", [("disable", True), ("enable", False)])
def test_codex_cli_enable_disable(
    monkeypatch, managed_codex, capsys, command, disabled
):
    monkeypatch.setattr(codex_cli, "CodexSwitcher", lambda: managed_codex)
    codex_cli.codex_command([command, "1", "--json"])
    assert ("disabled", "1", disabled) in managed_codex.calls
    import json

    assert json.loads(capsys.readouterr().out)["account"]["disabled"] is disabled
