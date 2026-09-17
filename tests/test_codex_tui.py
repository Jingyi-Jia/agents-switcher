"""Tests for the Codex accounts screen.

The screen is reached from the shared dashboard but owns its own data, so these
stub the switcher rather than the app's Claude snapshot.
"""

from __future__ import annotations

import pytest

from claude_swap.codex import switcher as switcher_mod
from claude_swap.codex.store import CodexAccount
from claude_swap.codex.switcher import SwitchResult
from claude_swap.codex.usage import CodexCredits, CodexUsage, CodexWindow
from claude_swap.tui import codex as codex_screen_mod
from claude_swap.tui.codex import CodexAccountItem, CodexScreen
from tests.test_tui import FakeSwitcher, make_account, make_app, settle

pytestmark = pytest.mark.asyncio


def account(number, email, alias=""):
    return CodexAccount(number=number, email=email,
                        account_id=f"acct-{number}", plan="pro", alias=alias)


def healthy(used):
    return CodexUsage(allowed=True, plan="pro", windows=(CodexWindow(used, 604800),))


def on_credits():
    return CodexUsage(allowed=False, limit_reached=True, plan="pro",
                      credits=CodexCredits(has_credits=True),
                      windows=(CodexWindow(100, 604800),))


class StubCodexSwitcher:
    """Stands in for CodexSwitcher so the screen never touches the network."""

    def __init__(self, accounts=(), usage=None, active="1", switch_result=None,
                 switch_error=None):
        self._accounts = list(accounts)
        self._usage = usage or {}
        self.switched_to: list[str] = []
        self._switch_result = switch_result
        self._switch_error = switch_error

        class Store:
            root = None

            def active_number(_self):
                return active

        self.store = Store()

    def list_accounts(self):
        return list(self._accounts)

    def usage_all(self):
        return dict(self._usage)

    def switch_to(self, number, **kw):
        self.switched_to.append(number)
        if self._switch_error:
            raise self._switch_error
        return self._switch_result


@pytest.fixture
def stub(monkeypatch):
    def install(**kwargs):
        instance = StubCodexSwitcher(**kwargs)
        monkeypatch.setattr(codex_screen_mod, "CodexSwitcher", lambda: instance)
        return instance

    return install


async def open_codex(tmp_path, pilot_ready=None):
    fake = FakeSwitcher([make_account(1, active=True)], tmp_path)
    return make_app(fake)


def row_text(screen) -> list[str]:
    return [item._body.render().plain for item in screen.query(CodexAccountItem)]


class TestListing:
    async def test_shows_managed_accounts_with_quota(self, tmp_path, stub):
        stub(
            accounts=[account("1", "one@e.com"), account("2", "two@e.com", "work")],
            usage={"1": healthy(20), "2": healthy(75)},
        )
        app = await open_codex(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await settle(pilot)
            app.push_screen(CodexScreen())
            await settle(pilot)
            text = " | ".join(row_text(app.screen))
            assert "one@e.com" in text and "two@e.com" in text
            assert "(work)" in text
            assert "20% of 7d" in text and "75% of 7d" in text

    async def test_marks_the_active_account(self, tmp_path, stub):
        stub(accounts=[account("1", "one@e.com"), account("2", "two@e.com")],
             usage={"1": healthy(10), "2": healthy(10)}, active="2")
        app = await open_codex(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await settle(pilot)
            app.push_screen(CodexScreen())
            await settle(pilot)
            rows = row_text(app.screen)
            assert rows[0].strip().startswith("1:")
            assert rows[1].strip().startswith("* 2:")

    async def test_empty_state_names_the_command_to_run(self, tmp_path, stub):
        stub(accounts=[], usage={})
        app = await open_codex(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await settle(pilot)
            app.push_screen(CodexScreen())
            await settle(pilot)
            from textual.widgets import Static

            status = app.screen.query_one("#codex-status", Static)
            assert "codex add" in status.render().plain

    async def test_a_failed_quota_read_shows_on_its_own_row(self, tmp_path, stub):
        # One unreachable account must not blank the whole screen.
        stub(accounts=[account("1", "one@e.com"), account("2", "two@e.com")],
             usage={"1": RuntimeError("network down"), "2": healthy(30)})
        app = await open_codex(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await settle(pilot)
            app.push_screen(CodexScreen())
            await settle(pilot)
            text = " | ".join(row_text(app.screen))
            assert "network down" in text
            assert "30% of 7d" in text

    async def test_a_credits_account_says_manual_switch_only(self, tmp_path, stub):
        stub(accounts=[account("1", "one@e.com")], usage={"1": on_credits()})
        app = await open_codex(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await settle(pilot)
            app.push_screen(CodexScreen())
            await settle(pilot)
            assert "manual switch only" in " ".join(row_text(app.screen))


class TestSwitching:
    async def test_enter_switches_the_highlighted_account(self, tmp_path, stub):
        instance = stub(
            accounts=[account("1", "one@e.com"), account("2", "two@e.com")],
            usage={"1": healthy(90), "2": healthy(5)},
            switch_result=SwitchResult(
                account=account("2", "two@e.com"), previous=None, synced_back=False,
            ),
        )
        app = await open_codex(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await settle(pilot)
            app.push_screen(CodexScreen())
            await settle(pilot)
            await pilot.press("down")
            await pilot.press("enter")
            await settle(pilot)
            assert instance.switched_to == ["2"]

    async def test_a_running_codex_is_reported_after_the_switch(self, tmp_path, stub):
        from claude_swap.codex.processes import CodexProcess

        stub(
            accounts=[account("1", "one@e.com")],
            usage={"1": healthy(90)},
            switch_result=SwitchResult(
                account=account("1", "one@e.com"), previous=None, synced_back=False,
                processes=(CodexProcess(9, "/bin/codex", "codex", tty="pts/3"),),
            ),
        )
        app = await open_codex(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await settle(pilot)
            app.push_screen(CodexScreen())
            await settle(pilot)
            await pilot.press("enter")
            await settle(pilot)
            from textual.widgets import Static

            status = app.screen.query_one("#codex-status", Static).render().plain
            assert "Restart Codex" in status

    async def test_a_refused_switch_is_shown_not_swallowed(self, tmp_path, stub):
        from claude_swap.exceptions import SwitchError

        stub(accounts=[account("1", "one@e.com")], usage={"1": healthy(10)},
             switch_error=SwitchError("already the active Codex account"))
        app = await open_codex(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await settle(pilot)
            app.push_screen(CodexScreen())
            await settle(pilot)
            await pilot.press("enter")
            await settle(pilot)
            from textual.widgets import Static

            status = app.screen.query_one("#codex-status", Static).render().plain
            assert "Could not switch" in status


class TestStatusPinning:
    async def test_the_restart_notice_survives_the_usage_reload(self, tmp_path, stub):
        """A switch triggers a quota reload, and the reload used to clear the
        status -- wiping the one line the user MUST read a second after it
        appeared."""
        from claude_swap.codex.processes import CodexProcess
        from textual.widgets import Static

        stub(
            accounts=[account("1", "one@e.com")],
            usage={"1": healthy(90)},
            switch_result=SwitchResult(
                account=account("1", "one@e.com"), previous=None, synced_back=False,
                processes=(CodexProcess(9, "/bin/codex", "codex", tty="pts/3"),),
            ),
        )
        app = await open_codex(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await settle(pilot)
            app.push_screen(CodexScreen())
            await settle(pilot)
            await pilot.press("enter")
            await settle(pilot)
            # Let the follow-up reload finish, which is what used to clear it.
            await settle(pilot)
            status = app.screen.query_one("#codex-status", Static).render().plain
            assert "Restart Codex" in status

    async def test_an_explicit_refresh_clears_a_pinned_notice(self, tmp_path, stub):
        from textual.widgets import Static

        stub(accounts=[account("1", "one@e.com")], usage={"1": healthy(10)},
             switch_error=RuntimeError("nope"))
        app = await open_codex(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await settle(pilot)
            app.push_screen(CodexScreen())
            await settle(pilot)
            await pilot.press("enter")
            await settle(pilot)
            assert "Could not switch" in app.screen.query_one(
                "#codex-status", Static).render().plain
            await pilot.press("r")   # the user asked for fresh state
            await settle(pilot)
            assert app.screen.query_one("#codex-status", Static).render().plain == ""


class TestDashboardEntry:
    async def test_the_menu_offers_codex(self, tmp_path):
        from textual.widgets import ListView

        from claude_swap.tui.widgets import MenuItem

        app = await open_codex(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await settle(pilot)
            menu = app.screen.query_one("#menu", ListView)
            assert "codex" in [item.action_id for item in menu.query(MenuItem)]
