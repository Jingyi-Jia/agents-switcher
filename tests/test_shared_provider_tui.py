import asyncio
import threading
from datetime import datetime

import pytest
from textual.widgets import ListView, Static

from claude_swap.codex.usage import CodexUsage, CodexWindow
from claude_swap.tui import app as app_module
from claude_swap.tui import codex as codex_module
from claude_swap.tui.app import CswapApp
from claude_swap.tui.autoview import AutoView
from claude_swap.tui.codex import CodexScreen, codex_snapshot
from claude_swap.tui.dashboard import DashboardScreen, SwitchScreen, WatchScreen
from claude_swap.tui.widgets import AccountsPanel, MenuItem, usage_rows
from tests.test_codex_tui import account, healthy
from tests.test_provider_navigation import ManagedCodexSwitcher, choose_menu
from tests.test_tui import FakeSwitcher, make_account, settle


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["claude", "codex"])
async def test_shared_menu_cards_and_subscreen_navigation(monkeypatch, tmp_path, provider):
    claude = FakeSwitcher([make_account(1, active=True)], tmp_path)
    codex = ManagedCodexSwitcher()
    monkeypatch.setattr(app_module, "ClaudeAccountSwitcher", lambda: claude)
    monkeypatch.setattr(codex_module, "CodexSwitcher", lambda: codex)
    app = CswapApp()
    async with app.run_test(size=(110, 36)) as pilot:
        await settle(pilot)
        await pilot.press("c" if provider == "claude" else "x")
        await settle(pilot)
        dashboard = app.screen
        assert isinstance(dashboard, DashboardScreen)
        assert dashboard.provider == provider
        assert len(dashboard._menu_stack) == 1
        assert dashboard.query_one(AccountsPanel).source is dashboard.controller
        assert [item.action_id for item in dashboard.query(MenuItem)] == [
            "switch", "watch", "auto", "codex" if provider == "claude" else "claude",
            "add-menu", "disable-menu", "remove-menu", "theme-menu", "quit",
        ]
        await choose_menu(pilot, "add-menu")
        assert [item.action_id for item in dashboard.query(MenuItem)] == (
            ["add-login", "add-token", "back"] if provider == "claude"
            else ["add-login", "back"]
        )
        await pilot.press("escape")
        await settle(pilot)
        assert len(dashboard._menu_stack) == 1
        await pilot.press("s")
        await settle(pilot)
        assert isinstance(app.screen, SwitchScreen)
        assert app.screen.source is dashboard.controller
        await pilot.press("escape", "w")
        await settle(pilot)
        assert isinstance(app.screen, WatchScreen)
        assert app.screen.source is dashboard.controller
        await pilot.press("escape")
        await settle(pilot)
        assert app.screen is dashboard
        await choose_menu(pilot, "theme-menu")
        await choose_menu(pilot, "theme:light")
        assert not app.current_theme.dark
        assert len(dashboard._menu_stack) == 1


@pytest.mark.asyncio
async def test_provider_menu_reuses_the_correct_dashboard(monkeypatch, tmp_path):
    claude = FakeSwitcher([make_account(1, active=True)], tmp_path)
    codex = ManagedCodexSwitcher()
    monkeypatch.setattr(app_module, "ClaudeAccountSwitcher", lambda: claude)
    monkeypatch.setattr(codex_module, "CodexSwitcher", lambda: codex)
    app = CswapApp(start="codex")
    async with app.run_test(size=(110, 36)) as pilot:
        await settle(pilot)
        codex_dashboard = app.screen
        await choose_menu(pilot, "claude")
        assert app.screen.provider == "claude"
        assert app._claude_active
        await choose_menu(pilot, "codex")
        assert app.screen is codex_dashboard
        assert not app._claude_active
        assert sum(isinstance(screen, CodexScreen) for screen in app.screen_stack) == 1
        assert not codex_dashboard.actions._closed


@pytest.mark.asyncio
async def test_codex_watch_only_switches_after_selection_is_armed(monkeypatch):
    codex = ManagedCodexSwitcher()
    codex.add_current()
    monkeypatch.setattr(codex_module, "CodexSwitcher", lambda: codex)
    app = CswapApp(start="codex")
    async with app.run_test(size=(110, 36)) as pilot:
        await settle(pilot)
        dashboard = app.screen
        await pilot.press("w")
        await settle(pilot)
        watch = app.screen
        await pilot.press("enter")
        assert codex.switched_to == []
        await pilot.press("s", "down", "enter")
        await settle(pilot)
        assert codex.switched_to == ["2"]
        assert app.screen is watch
        assert not watch._selecting
        assert watch.query_one("#accounts", ListView).index is None
        assert watch.focused is None
        await pilot.press("f")
        await settle(pilot)
        assert dashboard.snapshot.active_number == "2"
        assert app.snapshot is None and app.switcher is None


def test_codex_card_adapter_keeps_real_window_labels_and_reset_times():
    acc = account("1", "codex@example.test")
    usage = CodexUsage(windows=(
        CodexWindow(20, 7200, reset_after_seconds=1800),
        CodexWindow(60, 604800, reset_at=700000),
    ))
    snapshot = codex_snapshot([acc], "1", {(acc.number, acc.account_id): usage}, 1000)
    entry = snapshot.accounts[0].usage
    rows = usage_rows(entry.last_good, now=1000, fetched_at=1000)
    assert [(row[0], row[1]) for row in rows] == [("2h", 20), ("7d", 60)]
    assert datetime.fromisoformat(entry.last_good["windows"][0]["resets_at"]).timestamp() == 2800
    assert datetime.fromisoformat(entry.last_good["windows"][1]["resets_at"]).timestamp() == 700000


def test_codex_replaced_slot_does_not_inherit_old_identity_quota():
    acc = account("1", "codex@example.test")
    snapshot = codex_snapshot(
        [acc], "1", {("1", "previous-account-id"): CodexUsage(windows=(CodexWindow(80, 7200),))}, 1000,
    )
    assert snapshot.accounts[0].usage.last_good is None


@pytest.mark.asyncio
async def test_codex_auto_notifications_render_and_exit_stops_worker(monkeypatch):
    codex = ManagedCodexSwitcher()
    codex.add_current()
    monkeypatch.setattr(codex_module, "CodexSwitcher", lambda: codex)
    app = CswapApp(start="codex")
    async with app.run_test(size=(110, 36), notifications=True) as pilot:
        await settle(pilot)
        dashboard = app.screen
        await pilot.press("g")
        await settle(pilot)
        assert isinstance(app.screen, AutoView)
        assert dashboard.actions.auto.status("codex")["mode"] == "dry-run"
        assert dashboard.actions.auto._workers
        assert any("Codex auto-switch is dry-run." in toast.render().plain for toast in app.screen.query("Toast"))
        await pilot.press("escape")
        await settle(pilot)
        assert app.screen is dashboard
        assert "stopped" in dashboard.query_one("#codex-status", Static).render().plain
        assert not dashboard.actions.auto._workers
        await pilot.press("g")
        await settle(pilot)
        assert dashboard.actions.auto._workers
    assert not dashboard.actions.auto._workers
    assert dashboard.actions.auto.status("codex")["mode"] == "stopped"


@pytest.mark.asyncio
async def test_return_to_codex_refreshes_quota_immediately(monkeypatch, tmp_path):
    codex = ManagedCodexSwitcher()
    monkeypatch.setattr(codex_module, "CodexSwitcher", lambda: codex)
    monkeypatch.setattr(app_module, "ClaudeAccountSwitcher", lambda: FakeSwitcher([], tmp_path))
    app = CswapApp(start="codex")
    async with app.run_test(size=(110, 36)) as pilot:
        await settle(pilot)
        dashboard = app.screen
        await choose_menu(pilot, "claude")
        codex._usage["1"] = healthy(12)
        await choose_menu(pilot, "codex")
        assert app.screen is dashboard
        assert dashboard.snapshot.accounts[0].usage.last_good["windows"][0]["pct"] == 12


@pytest.mark.asyncio
async def test_busy_auto_start_blocks_exit_and_preserves_threshold_edit(monkeypatch):
    codex = ManagedCodexSwitcher()
    monkeypatch.setattr(codex_module, "CodexSwitcher", lambda: codex)
    app = CswapApp(start="codex")
    started = threading.Event()
    release = threading.Event()
    async with app.run_test(size=(110, 36)) as pilot:
        await settle(pilot)
        dashboard = app.screen
        configure = dashboard.actions.auto.configure

        def delayed_configure(provider, mode, **kwargs):
            if mode == "dry-run" and not release.is_set():
                started.set()
                assert release.wait(5)
            return configure(provider, mode, **kwargs)

        monkeypatch.setattr(dashboard.actions.auto, "configure", delayed_configure)
        try:
            await pilot.press("g")
            assert await asyncio.to_thread(started.wait, 2)
            auto = app.screen
            await pilot.press("escape")
            assert app.screen is auto
            assert dashboard._busy
            await pilot.press("t", "left", "enter")
            assert auto._adjusting
            assert auto._threshold == 89
        finally:
            release.set()
        await settle(pilot)
        await pilot.press("enter")
        await settle(pilot)
        assert dashboard.actions.auto.status("codex")["threshold"] == 89
        assert not auto._adjusting
        await pilot.press("escape")
        await settle(pilot)
        assert app.screen is dashboard
        assert dashboard.actions.auto.status("codex")["mode"] == "stopped"
        assert not dashboard.actions.auto._workers


@pytest.mark.asyncio
async def test_busy_dashboard_does_not_open_an_unstarted_auto_view(monkeypatch):
    codex = ManagedCodexSwitcher()
    monkeypatch.setattr(codex_module, "CodexSwitcher", lambda: codex)
    app = CswapApp(start="codex")
    release = threading.Event()
    async with app.run_test(size=(110, 36)) as pilot:
        await settle(pilot)
        dashboard = app.screen

        def slow_action():
            assert release.wait(5)
            return {"ok": True, "message": "Finished"}

        try:
            dashboard._start_action("slow action", slow_action)
            await pilot.press("g")
            assert app.screen is dashboard
        finally:
            release.set()
        await settle(pilot)
        await pilot.press("g")
        await settle(pilot)
        assert isinstance(app.screen, AutoView)
        assert dashboard.actions.auto.status("codex")["mode"] == "dry-run"
