"""Synthetic render and keyboard coverage for the desktop-aligned terminal UI."""

from dataclasses import replace
import time
from xml.etree import ElementTree

import pytest
from textual.containers import VerticalScroll
from textual.widgets import Button, Input, ListView, Static

from claude_swap.codex.usage import CodexUsage, CodexWindow
from claude_swap.codex.processes import CodexProcess
from claude_swap.codex.switcher import SwitchResult
from claude_swap.tui.app import CswapApp
from claude_swap.tui.dashboard import SwitchScreen, WatchScreen
from claude_swap.tui.modals import AddTokenModal, ConfirmModal
from claude_swap.tui.theme import CSWAP_DARK, CSWAP_LIGHT, Palette
from claude_swap.tui.widgets import (
    AccountCard, AccountItem, AccountsPanel, AppHeader, account_card_text, mini_account_text,
)
from tests.test_codex_tui import account, on_credits
from tests.test_provider_navigation import ManagedCodexSwitcher, choose_menu
from tests.test_theme import _contrast
from tests.test_tui import FakeSwitcher, _FakeEngine, make_account, make_entry, settle


def save_render(app, tmp_path, name):
    svg = app.export_screenshot(title=f"Agent Switch · synthetic {name}")
    (tmp_path / f"{name}.svg").write_text(svg)
    root = ElementTree.fromstring(svg)
    return " ".join(root.itertext()).replace("\u00a0", " ")


@pytest.fixture
def design_switchers(monkeypatch, tmp_path):
    claude = FakeSwitcher([
        make_account(1, email="alex@example.test", alias="Personal", active=True,
                     entry=make_entry(32, 58)),
        make_account(2, email="studio@example.test", alias="Studio",
                     entry=make_entry(76, 42)),
        make_account(3, email="archive@example.test", disabled=True,
                     entry=make_entry(sentinel="Sign-in needed")),
    ], tmp_path)
    codex = ManagedCodexSwitcher()
    codex._accounts = [
        account("1", "alex@example.test", "Personal"),
        account("2", "studio@example.test", "Studio"),
        replace(account("3", "archive@example.test"), disabled=True),
    ]
    codex._usage = {
        "1": CodexUsage(windows=(CodexWindow(32, 7200, reset_after_seconds=1800),
                                 CodexWindow(58, 604800, reset_after_seconds=172800))),
        "2": CodexUsage(windows=(CodexWindow(76, 7200), CodexWindow(42, 604800))),
        "3": on_credits(),
    }
    monkeypatch.setattr("claude_swap.tui.app.ClaudeAccountSwitcher", lambda: claude)
    monkeypatch.setattr("claude_swap.tui.app.os.geteuid", lambda: 1000, raising=False)
    monkeypatch.setattr("claude_swap.tui.codex.CodexSwitcher", lambda: codex)
    monkeypatch.setattr("claude_swap.providers.running_codex_processes", lambda: ())
    monkeypatch.setattr("claude_swap.tui.autoview.AutoSwitchEngine", _FakeEngine)
    return claude, codex


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["claude", "codex"])
@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("size", [(120, 38), (56, 28)])
async def test_responsive_dashboard_cards_and_keyboard(design_switchers, tmp_path, provider, theme, size):
    claude, codex = design_switchers
    app = CswapApp(claude) if provider == "claude" else CswapApp(start="codex")
    async with app.run_test(size=size) as pilot:
        await settle(pilot)
        app.apply_theme(theme)
        await settle(pilot)
        menu = app.screen.query_one("#menu", ListView)
        overview = app.screen.query_one("#overview", VerticalScroll)
        assert menu.has_focus
        assert menu.region.height >= 4
        assert overview.region.height >= 8
        if size[0] >= 96:
            assert menu.region.right <= overview.region.x
        else:
            assert overview.region.bottom <= menu.region.y
        assert menu.region.bottom <= size[1] - 1
        assert app.screen.query_one(AppHeader).provider == provider
        text = save_render(app, tmp_path, f"{provider}-{theme}-{size[0]}-dashboard")
        assert "Agent Switch" in text and "Your accounts" in text
        panel = app.screen.query_one(AccountsPanel).render().plain
        assert "32%" in panel and "58%" in panel and "(disabled)" in panel
        assert ("2h" if provider == "codex" else "5h") in panel
        await pilot.press("j", "k", "enter")
        await settle(pilot)
        assert isinstance(app.screen, SwitchScreen)
        cards = list(app.screen.query(AccountItem))
        assert cards[0].has_class("active-account")
        assert cards[2].has_class("disabled-account")
        await pilot.press("down")
        await settle(pilot)
        assert app.screen.query_one(ListView).index == 1
        assert cards[1].has_class("-highlight")
        assert cards[0].has_class("active-account")
        card = cards[1].query_one(AccountCard)
        expected = Palette.from_theme(app.current_theme, provider)
        assert any(expected.accent in str(span.style) for span in card.render().spans)
        save_render(app, tmp_path, f"{provider}-{theme}-{size[0]}-switch")
        await pilot.press("enter")
        await settle(pilot)
        if provider == "claude":
            assert ("switch_to", "2") in claude.calls
        else:
            assert codex.switched_to == ["2"]
        await pilot.press("w")
        await settle(pilot)
        assert isinstance(app.screen, WatchScreen)
        assert app.screen.query_one(ListView).index is None
        await pilot.press("enter")
        assert app.screen.query_one(ListView).index is None
        await pilot.press("s", "escape", "escape")
        await settle(pilot)
        await choose_menu(pilot, "remove-menu")
        await choose_menu(pilot, "remove:3")
        assert isinstance(app.screen, ConfirmModal)
        save_render(app, tmp_path, f"{provider}-{theme}-{size[0]}-confirm")
        await pilot.press("right", "enter")
        await settle(pilot)
        assert not any(call[0] == "remove" for call in claude.calls + codex.calls)


@pytest.mark.asyncio
async def test_narrow_dashboard_can_scroll_notices_and_all_actions(design_switchers, tmp_path):
    app = CswapApp(design_switchers[0])
    async with app.run_test(size=(48, 24)) as pilot:
        await settle(pilot)
        overview = app.screen.query_one("#overview", VerticalScroll)
        assert overview.styles.padding.left == 2
        await pilot.press("tab", "end")
        await settle(pilot)
        assert overview.has_focus
        assert overview.scroll_y > 0
        assert overview.scroll_y == overview.max_scroll_y
        text = save_render(app, tmp_path, "claude-48-notice")
        assert "restarting alone" in text
        assert app.screen.query_one("#claude-client-notice").region.bottom <= overview.region.bottom
        await pilot.press("shift+tab", *(["down"] * 8))
        await settle(pilot)
        menu = app.screen.query_one("#menu", ListView)
        assert menu.has_focus
        assert menu.index == len(menu.children) - 1
        assert menu.highlighted_child.region.bottom <= menu.region.bottom
        save_render(app, tmp_path, "claude-48-actions")
        await pilot.resize_terminal(120, 38)
        await settle(pilot)
        assert menu.region.right <= overview.region.x


@pytest.mark.asyncio
async def test_failed_read_and_restart_required_remain_visible(design_switchers, monkeypatch, tmp_path):
    codex = design_switchers[1]
    app = CswapApp(start="codex")
    async with app.run_test(size=(56, 28)) as pilot:
        await settle(pilot)
        dashboard = app.screen
        await pilot.press("w")
        await settle(pilot)
        with monkeypatch.context() as patch:
            def fail_read():
                raise RuntimeError("Synthetic auth file unreadable; existing login preserved")

            patch.setattr(codex, "usage_all", fail_read)
            dashboard.request_refresh()
            await settle(pilot)
            assert dashboard.snapshot is None
            assert "Could not load quota" in dashboard.query_one(AccountsPanel).render().plain
            assert not app.screen.query_one("#accounts", ListView).display
            assert "Could not load quota" in app.screen.query_one("#list-empty", Static).render().plain
            await pilot.press("s", "enter")
            assert codex.switched_to == []
            await pilot.press("escape", "escape")
            await settle(pilot)
            text = save_render(app, tmp_path, "codex-56-error")
            assert "Could not load quota" in text
            assert "active" not in dashboard.query_one(AccountsPanel).render().plain
        dashboard.request_refresh()
        await settle(pilot)
        def switched_with_process(number):
            codex.active = number
            codex.switched_to.append(number)
            return SwitchResult(
                account=codex._accounts[int(number) - 1], previous=None, synced_back=False,
                processes=(CodexProcess(9, "/synthetic/codex", "codex", tty="pts/3"),),
            )

        monkeypatch.setattr(codex, "switch_to", switched_with_process)
        await pilot.press("s", "down", "enter")
        await settle(pilot)
        status = dashboard.query_one("#codex-status", Static)
        assert "Restart Codex" in status.render().plain
        assert status.region.bottom < app.size.height
        assert dashboard.query_one("#menu").region.height >= 4
        text = save_render(app, tmp_path, "codex-56-restart")
        assert "Restart Codex" in text


@pytest.mark.asyncio
async def test_provider_chooser_and_empty_switch_are_rendered(design_switchers, tmp_path):
    design_switchers[1]._accounts = []
    app = CswapApp()
    async with app.run_test(size=(56, 28)) as pilot:
        await settle(pilot)
        text = save_render(app, tmp_path, "providers-56")
        assert "Claude Code" in text and "Codex" in text
        await pilot.press("down", "enter")
        await settle(pilot)
        text = save_render(app, tmp_path, "codex-56-empty")
        assert "No managed accounts" in text
        await pilot.press("s")
        await settle(pilot)
        assert app.screen.query_one("#list-empty", Static).display
        assert "Add account" in app.screen.query_one("#list-empty", Static).render().plain
        await pilot.press("enter")
        assert design_switchers[1].switched_to == []


@pytest.mark.asyncio
async def test_narrow_auto_controls_and_token_form_remain_reachable(design_switchers, tmp_path):
    app = CswapApp(design_switchers[0])
    async with app.run_test(size=(48, 24)) as pilot:
        await settle(pilot)
        await pilot.press("g")
        await settle(pilot)
        assert app.screen.query_one("#event-log").region.height >= 3
        await pilot.press("t", "left", "enter")
        assert app.threshold_pct == 89
        assert app.screen.query_one("#event-log").max_scroll_x == 0
        save_render(app, tmp_path, "claude-48-auto")
        await pilot.press("l")
        assert isinstance(app.screen, ConfirmModal)
        await pilot.press("n", "escape")
        await settle(pilot)
        app.push_screen(AddTokenModal())
        await settle(pilot)
        assert app.screen.query_one("#token", Input).password
        await pilot.press("enter")
        assert "required" in app.screen.query_one("#form-error", Static).render().plain
        await pilot.press("tab", "tab", "tab", "tab")
        button = app.screen.query_one("#cancel", Button)
        assert button.has_focus
        assert button.region.bottom < app.size.height
        save_render(app, tmp_path, "claude-48-token")
        await pilot.press("enter")
        assert not isinstance(app.screen, AddTokenModal)


@pytest.mark.asyncio
async def test_theme_toggle_repaints_existing_provider_cards(design_switchers, tmp_path):
    app = CswapApp(design_switchers[0])
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(pilot)
        app.apply_theme("dark")
        await pilot.press("s")
        await settle(pilot)
        await pilot.press("ctrl+t")
        await settle(pilot)
        assert not app.current_theme.dark
        svg = app.export_screenshot()
        assert CSWAP_LIGHT.variables["claude-accent"] in svg
        assert CSWAP_DARK.variables["claude-accent"] not in svg
        save_render(app, tmp_path, "claude-80-light-switch")
        app.push_screen(ConfirmModal("Remove [red]@example.test?", yes_label="Remove"))
        await settle(pilot)
        assert app.screen.query_one(".modal-body", Static).render().plain == "Remove [red]@example.test?"
        assert app.screen.query_one("#yes", Button).has_focus
        await pilot.press("right", "enter")
        assert isinstance(app.screen, SwitchScreen)


def test_desktop_palette_and_provider_accents_keep_text_contrast():
    for theme in (CSWAP_DARK, CSWAP_LIGHT):
        claude = Palette.from_theme(theme, "claude")
        codex = Palette.from_theme(theme, "codex")
        assert claude.accent != codex.accent
        assert claude.active == codex.active
        for palette in (claude, codex):
            for color in (palette.foreground, palette.muted, palette.accent,
                          palette.sev_warn, palette.sev_crit):
                for bg in (theme.background, theme.surface, theme.panel, theme.variables["accent-soft"]):
                    assert _contrast(color, bg) >= 4.5


def test_narrow_quota_rows_keep_resets_amounts_and_real_labels():
    entry = make_entry(spend={"used": 12.5, "limit": 50, "pct": 25},
                       scoped=[("Opus", 100)])
    text = account_card_text(make_account(1, entry=entry), 36, now=time.time()).plain
    assert "$12.50 / $50.00" in text and "resets" in text and "(!)" in text
    assert "usage unknown" not in text
    for line in text.splitlines():
        if "━" in line or "─" in line:
            assert len(line) <= 36


def test_compact_account_wraps_complete_warning_below_identity():
    acc = make_account(3, entry=make_entry(sentinel="paid credits · manual switch only"))
    text = mini_account_text(acc, time.time(), width=64).plain
    assert "\n    paid credits · manual switch only" in text
