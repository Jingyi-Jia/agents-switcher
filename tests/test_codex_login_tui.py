"""Browser enrollment stays save-only and disposable in the terminal UI."""

import asyncio
import threading
from xml.etree import ElementTree

import pytest
from textual.widgets import Button, ListView, Static

from agents_switcher.codex.enrollment import EnrollmentError
from agents_switcher.tui import codex as codex_module
from agents_switcher.tui import codex_login as login_module
from agents_switcher.tui.app import CswapApp
from agents_switcher.tui.codex_login import CodexLoginModal
from agents_switcher.tui.widgets import AccountsPanel, MenuItem
from tests.test_codex_tui import account, codex_readiness, healthy
from tests.test_provider_navigation import ManagedCodexSwitcher, choose_menu
from tests.test_tui import settle

pytestmark = pytest.mark.asyncio


class FakeEnrollment:
    def __init__(self, switcher, *, browser):
        assert browser is True
        self.switcher = switcher
        self.calls = []
        self.threads = []
        self.session = None
        self.number = None
        self.closed = False
        self.result = None
        self.open_error = None
        self.status_error = None
        self.save_error = None
        self.status_result = {"ok": True, "status": "waiting", "message": "Waiting for sign-in."}
        self.status_started = threading.Event()
        self.status_release = None

    def record(self, method, session=None, confirm=False):
        assert confirm is True
        self.calls.append((method, session))
        self.threads.append(threading.get_ident())

    def prepare(self, *, number=None, confirm=False):
        self.record("prepare", number, confirm)
        if self.closed:
            raise EnrollmentError("The sign-in is closed.")
        self.number = number
        self.session = f"session-{sum(c[0] == 'prepare' for c in self.calls)}"
        return {"ok": True, "sessionId": self.session, "method": "browser",
                "status": "waiting", "message": "Ready to open browser."}

    def open_browser(self, session, *, confirm=False):
        self.record("open", session, confirm)
        assert session == self.session
        if self.open_error:
            raise self.open_error
        return {"ok": True, "message": "Browser opened."}

    def status(self, session, *, confirm=False):
        self.record("status", session, confirm)
        assert session == self.session
        self.status_started.set()
        if self.status_release is not None:
            assert self.status_release.wait(5)
        if self.status_error:
            raise self.status_error
        return dict(self.status_result)

    def complete(self, session, *, confirm=False):
        self.record("complete", session, confirm)
        assert session == self.session and not self.closed
        if self.save_error:
            raise self.save_error
        if self.result is None:
            number = self.number or "2"
            if not self.number:
                self.switcher._accounts.append(account(number, "saved@example.test"))
                self.switcher._usage[number] = healthy(10)
            self.switcher.pending.add(number)
            self.result = {"ok": True, "account": {"number": number}, "activationRequired": True}
        return dict(self.result)

    def cancel(self, session, *, confirm=False):
        self.record("cancel", session, confirm)
        self.session = None
        return {"ok": True, "warning": False, "message": "Closed; saved accounts remain saved."}

    def close(self):
        self.calls.append(("close", None))
        self.threads.append(threading.get_ident())
        self.closed = True
        self.session = None


@pytest.fixture
def login_setup(monkeypatch):
    switcher = ManagedCodexSwitcher()
    controllers = []

    def create(switcher, *, browser):
        controller = FakeEnrollment(switcher, browser=browser)
        controllers.append(controller)
        return controller

    monkeypatch.setattr(codex_module, "CodexSwitcher", lambda: switcher)
    monkeypatch.setattr(login_module, "CodexEnrollment", create)
    return switcher, controllers


async def open_login(pilot, *, number=None):
    await settle(pilot)
    await choose_menu(pilot, "add-menu")
    if number is None:
        await choose_menu(pilot, "add-browser")
    else:
        await choose_menu(pilot, "repair-menu")
        await choose_menu(pilot, f"repair:{number}")
    assert isinstance(pilot.app.screen, CodexLoginModal)
    return pilot.app.screen


async def begin_login(pilot, modal):
    await pilot.press("enter")
    await settle(pilot)
    modal._stop_polling()


async def poll_once(pilot, modal):
    modal._poll()
    await settle(pilot)
    await settle(pilot)


async def test_browser_add_is_primary_and_requires_explicit_save_consent(login_setup):
    switcher, controllers = login_setup
    app = CswapApp(start="codex")
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        dashboard = app.screen
        await choose_menu(pilot, "add-menu")
        assert [item.action_id for item in dashboard.query(MenuItem)] == [
            "add-browser", "add-login", "repair-menu", "back",
        ]
        await choose_menu(pilot, "add-browser")
        modal = app.screen
        assert modal.query_one("#codex-login-continue", Button).has_focus
        text = " ".join(widget.render().plain for widget in modal.query(Static))
        assert "authorizes automatically saving" in text
        assert "does not switch your active login" in text
        assert "auto-switch rules" not in text
        assert controllers[0].calls == []
        await pilot.press("escape")
        await settle(pilot)
        assert app.screen is dashboard
        assert dashboard.query_one("#menu", ListView).has_focus
        assert controllers[0].closed and controllers[0].session is None
        assert not any(c[0] in {"prepare", "open", "complete"} for c in controllers[0].calls)
        assert not switcher.switched_to


async def test_ready_sign_in_saves_once_without_activation_and_refreshes_roster(login_setup):
    switcher, controllers = login_setup
    app = CswapApp(start="codex")
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        dashboard = app.screen
        revision = dashboard.actions.revision
        modal = await open_login(pilot)
        await begin_login(pilot, modal)
        controller = controllers[0]
        controller.status_result = {"ok": True, "status": "ready", "message": "Sign-in is ready."}
        lock_owned = []
        complete = controller.complete

        def locked_complete(*args, **kwargs):
            lock_owned.append(dashboard.actions.lock._is_owned())
            return complete(*args, **kwargs)

        controller.complete = locked_complete
        await poll_once(pilot, modal)
        assert app.screen is dashboard
        assert lock_owned == [True]
        assert [c[0] for c in controller.calls].count("complete") == 1
        assert dashboard.actions.revision == revision + 1
        assert [a.number for a in dashboard.snapshot.accounts] == ["1", "2"]
        assert dashboard.snapshot.active_number == "1"
        assert "saved login pending" in dashboard.query_one(AccountsPanel).render().plain
        assert "saved without switching" in dashboard.query_one("#codex-status", Static).render().plain
        assert switcher.switched_to == [] and switcher.pending == {"2"}
        assert controller.closed and controller.session is None
        assert all(thread != threading.get_ident() for thread in controller.threads)


async def test_saved_active_account_repair_is_pinned_and_switch_is_deliberate(login_setup):
    switcher, controllers = login_setup
    app = CswapApp(start="codex")
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        dashboard = app.screen
        modal = await open_login(pilot, number="1")
        text = " ".join(widget.render().plain for widget in modal.query(Static))
        assert "one@example.com" in text and "replace its saved Codex login" in text
        await begin_login(pilot, modal)
        controller = controllers[0]
        assert ("prepare", "1") in controller.calls
        controller.status_result = {"ok": True, "status": "ready", "message": "Sign-in is ready."}
        await poll_once(pilot, modal)
        assert not switcher.switched_to
        assert switcher.pending == {"1"}
        await pilot.press("s", "enter")
        await settle(pilot)
        assert switcher.switched_to == ["1"]
        assert switcher.switch_options == [{"allow_same": True}]
        assert not switcher.pending
        assert "saved login pending" not in dashboard.query_one(AccountsPanel).render().plain


@pytest.mark.parametrize("operation", ["switch", "best"])
@pytest.mark.parametrize("readiness", [
    {"available": True, "running": True},
    {"available": False, "running": None},
    {"available": True, "running": "false"},
])
async def test_pending_switch_keeps_strict_process_safeguards(login_setup, monkeypatch, readiness, operation):
    switcher, _ = login_setup
    switcher.pending.add("1")
    monkeypatch.setattr(codex_module.CodexDesktop, "status", lambda _: readiness)
    app = CswapApp(start="codex")
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        dashboard = app.screen
        if operation == "switch":
            dashboard.do_switch("1")
        else:
            dashboard.action_switch_best()
        await settle(pilot)
        assert not switcher.switched_to
        assert switcher.pending == {"1"}
        assert "login has not changed" in dashboard.query_one("#codex-status", Static).render().plain


async def test_browser_open_failure_retries_the_same_session_and_offers_safe_fallback(login_setup):
    switcher, controllers = login_setup
    app = CswapApp(start="codex")
    async with app.run_test(size=(100, 32)) as pilot:
        modal = await open_login(pilot, number="1")
        controller = controllers[0]
        controller.open_error = EnrollmentError("Could not open the default browser.")
        await begin_login(pilot, modal)
        assert str(modal.query_one("#codex-login-continue", Button).label) == "Retry opening browser"
        text = modal.query_one("#codex-login-message", Static).render().plain
        assert "agent-switch codex login --account 1" in text
        assert "http" not in text
        controller.open_error = None
        await pilot.click("#codex-login-continue")
        await settle(pilot)
        assert [c for c in controller.calls if c[0] == "prepare"] == [("prepare", "1")]
        assert [c for c in controller.calls if c[0] == "open"] == [("open", "session-1")] * 2
        assert not switcher.switched_to
        await pilot.press("escape")
        await settle(pilot)
        assert controller.closed


async def test_browser_retry_is_available_before_the_previous_press_animation_ends(login_setup):
    switcher, controllers = login_setup
    app = CswapApp(start="codex")
    async with app.run_test(size=(100, 32)) as pilot:
        modal = await open_login(pilot, number="1")
        button = modal.query_one("#codex-login-continue", Button)
        button.active_effect_duration = 60
        controller = controllers[0]
        controller.open_error = EnrollmentError("Could not open the default browser.")
        await begin_login(pilot, modal)
        assert str(button.label) == "Retry opening browser"
        assert not button.disabled
        controller.open_error = None
        assert await pilot.click("#codex-login-continue")
        await settle(pilot)
        assert [call for call in controller.calls if call[0] == "prepare"] == [("prepare", "1")]
        assert [call for call in controller.calls if call[0] == "open"] == [("open", "session-1")] * 2
        assert not switcher.switched_to


async def test_save_failure_retries_without_repeating_browser_sign_in(login_setup):
    switcher, controllers = login_setup
    app = CswapApp(start="codex")
    async with app.run_test(size=(100, 32)) as pilot:
        modal = await open_login(pilot)
        await begin_login(pilot, modal)
        controller = controllers[0]
        controller.status_result = {"ok": True, "status": "ready", "message": "Ready."}
        controller.save_error = EnrollmentError("Could not save the login. Try again.")
        await poll_once(pilot, modal)
        assert app.screen is modal
        assert str(modal.query_one("#codex-login-continue", Button).label) == "Retry save"
        assert modal._poll_timer is None
        controller.save_error = None
        await pilot.click("#codex-login-continue")
        await settle(pilot)
        await settle(pilot)
        assert not isinstance(app.screen, CodexLoginModal)
        assert [c for c in controller.calls if c[0] == "complete"] == [("complete", "session-1")] * 2
        assert [c[0] for c in controller.calls].count("prepare") == 1
        assert [c[0] for c in controller.calls].count("open") == 1
        assert not switcher.switched_to


async def test_wrong_repair_identity_starts_a_new_pinned_sign_in_instead_of_retrying_save(login_setup):
    switcher, controllers = login_setup
    app = CswapApp(start="codex")
    async with app.run_test(size=(100, 32)) as pilot:
        modal = await open_login(pilot, number="1")
        await begin_login(pilot, modal)
        controller = controllers[0]
        controller.status_result = {"ok": True, "status": "ready", "message": "Ready."}
        controller.save_error = EnrollmentError("Sign in with the selected account.", code="wrong-account")
        await poll_once(pilot, modal)
        assert str(modal.query_one("#codex-login-continue", Button).label) == "Start again"
        assert modal._poll_timer is None
        assert len(switcher.list_accounts()) == 1
        assert not switcher.pending and not switcher.switched_to
        controller.save_error = None
        await pilot.click("#codex-login-continue")
        await settle(pilot)
        modal._stop_polling()
        assert ("cancel", "session-1") in controller.calls
        assert [call for call in controller.calls if call[0] == "prepare"] == [("prepare", "1")] * 2
        assert ("open", "session-2") in controller.calls
        await poll_once(pilot, modal)
        assert [call for call in controller.calls if call[0] == "complete"] == [
            ("complete", "session-1"), ("complete", "session-2"),
        ]
        assert switcher.pending == {"1"} and not switcher.switched_to


@pytest.mark.parametrize("status", ["waiting", "exchanging", "ready"])
async def test_status_failure_checks_the_same_session_and_resumes_polling(login_setup, status):
    switcher, controllers = login_setup
    app = CswapApp(start="codex")
    async with app.run_test(size=(100, 32)) as pilot:
        modal = await open_login(pilot)
        await begin_login(pilot, modal)
        controller = controllers[0]
        controller.status_error = EnrollmentError("Could not check sign-in right now.")
        await poll_once(pilot, modal)
        assert str(modal.query_one("#codex-login-continue", Button).label) == "Check again"
        assert modal._poll_timer is None
        controller.status_error = None
        controller.status_result = {"ok": True, "status": status, "message": f"Sign-in {status}."}
        await pilot.click("#codex-login-continue")
        await settle(pilot)
        if status != "ready":
            assert app.screen is modal
            assert modal._poll_timer is not None
            controller.status_result = {"ok": True, "status": "ready", "message": "Ready."}
            await pilot.pause(1.2)
        await settle(pilot)
        assert not isinstance(app.screen, CodexLoginModal)
        assert [call for call in controller.calls if call[0] == "prepare"] == [("prepare", None)]
        assert [call for call in controller.calls if call[0] == "open"] == [("open", "session-1")]
        assert [call for call in controller.calls if call[0] == "complete"] == [("complete", "session-1")]
        assert controller.calls.index(("complete", "session-1")) < controller.calls.index(("cancel", "session-1"))
        assert switcher.pending == {"2"} and not switcher.switched_to


async def test_opener_timeout_keeps_retry_visible_but_saves_a_later_ready_callback(login_setup):
    switcher, controllers = login_setup
    app = CswapApp(start="codex")
    async with app.run_test(size=(100, 32)) as pilot:
        modal = await open_login(pilot)
        controller = controllers[0]
        controller.open_error = EnrollmentError("The default browser did not report opening in time.")
        await pilot.press("enter")
        await settle(pilot)
        assert modal._poll_timer is not None
        await poll_once(pilot, modal)
        assert str(modal.query_one("#codex-login-continue", Button).label) == "Retry opening browser"
        assert "did not report opening in time" in modal.query_one("#codex-login-message", Static).render().plain
        assert modal._poll_timer is not None
        controller.status_result = {"ok": True, "status": "ready", "message": "Ready."}
        await pilot.pause(1.2)
        await settle(pilot)
        await settle(pilot)
        assert not isinstance(app.screen, CodexLoginModal)
        assert [call for call in controller.calls if call[0] == "open"] == [("open", "session-1")]
        assert [call for call in controller.calls if call[0] == "complete"] == [("complete", "session-1")]
        assert switcher.pending == {"2"} and not switcher.switched_to


@pytest.mark.parametrize("status", ["error", "expired"])
async def test_terminal_error_stops_polling_and_start_again_replaces_only_the_session(login_setup, status):
    switcher, controllers = login_setup
    app = CswapApp(start="codex")
    async with app.run_test(size=(100, 32)) as pilot:
        modal = await open_login(pilot)
        await begin_login(pilot, modal)
        controller = controllers[0]
        controller.status_result = {"ok": False, "status": status, "message": "Sign-in must start again."}
        await poll_once(pilot, modal)
        assert str(modal.query_one("#codex-login-continue", Button).label) == "Start again"
        assert modal._poll_timer is None
        await pilot.click("#codex-login-continue")
        await settle(pilot)
        assert ("cancel", "session-1") in controller.calls
        assert ("open", "session-2") in controller.calls
        assert controller.session == "session-2"
        assert len(switcher.list_accounts()) == 1 and not switcher.switched_to


@pytest.mark.parametrize("close", ["escape", "unmount"])
async def test_cancel_or_unmount_discards_late_ready_status(login_setup, close):
    switcher, controllers = login_setup
    app = CswapApp(start="codex")
    async with app.run_test(size=(100, 32)) as pilot:
        modal = await open_login(pilot)
        await begin_login(pilot, modal)
        controller = controllers[0]
        controller.status_result = {"ok": True, "status": "ready", "message": "Ready."}
        controller.status_release = threading.Event()
        try:
            modal._poll()
            assert await asyncio.to_thread(controller.status_started.wait, 2)
            modal._poll()
            assert [c[0] for c in controller.calls].count("status") == 1
            if close == "escape":
                await pilot.press("escape")
            else:
                app.pop_screen()
            await pilot.pause()
            assert modal._cancelled.is_set()
            assert controller.closed and controller.session is None
            controller.status_release.set()
            await settle(pilot)
            assert not any(c[0] == "complete" for c in controller.calls)
            assert not switcher.switched_to and len(switcher.list_accounts()) == 1
            assert not app.screen._busy and app.screen._login_modal is None
        finally:
            controller.status_release.set()


async def test_cancel_prevents_a_save_waiting_for_the_action_lock(login_setup):
    switcher, controllers = login_setup
    app = CswapApp(start="codex")
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        dashboard = app.screen
        modal = await open_login(pilot)
        await begin_login(pilot, modal)
        controller = controllers[0]
        controller.status_result = {"ok": True, "status": "ready", "message": "Ready."}
        dashboard.actions.lock.acquire()
        try:
            modal._poll()
            assert await asyncio.to_thread(controller.status_started.wait, 2)
            await pilot.pause()
            assert "Saving" in modal.query_one("#codex-login-message", Static).render().plain
            await pilot.press("escape")
            await pilot.pause()
            assert modal._cancelled.is_set()
        finally:
            dashboard.actions.lock.release()
        await settle(pilot)
        assert not any(c[0] == "complete" for c in controller.calls)
        assert controller.closed and controller.session is None
        assert len(switcher.list_accounts()) == 1


async def test_app_exit_closes_an_owned_waiting_session(login_setup):
    _, controllers = login_setup
    app = CswapApp(start="codex")
    async with app.run_test(size=(100, 32)) as pilot:
        modal = await open_login(pilot)
        await begin_login(pilot, modal)
        assert controllers[0].session is not None
    assert controllers[0].closed and controllers[0].session is None
    assert modal._cancelled.is_set() and modal._poll_timer is None


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("size", [(100, 32), (48, 24)])
async def test_modal_consent_and_retry_remain_keyboard_accessible(login_setup, tmp_path, theme, size):
    _, controllers = login_setup
    app = CswapApp(start="codex")
    async with app.run_test(size=size) as pilot:
        await settle(pilot)
        app.apply_theme(theme)
        modal = await open_login(pilot)
        button = modal.query_one("#codex-login-continue", Button)
        assert button.has_focus
        assert button.region.right <= size[0]
        assert button.region.bottom <= size[1]
        svg = app.export_screenshot(title="Agent Switch · synthetic Codex sign-in")
        (tmp_path / "codex-login-consent.svg").write_text(svg, encoding="utf-8")
        text = " ".join(ElementTree.fromstring(svg).itertext()).replace("\u00a0", " ")
        assert "Continue in browser" in text and "Cancel" in text
        controllers[0].open_error = EnrollmentError("Could not open the default browser.")
        await begin_login(pilot, modal)
        await pilot.press("shift+tab")
        await settle(pilot)
        assert button.has_focus
        svg = app.export_screenshot(title="Agent Switch · synthetic browser retry")
        (tmp_path / "codex-login-retry.svg").write_text(svg, encoding="utf-8")
        text = " ".join(ElementTree.fromstring(svg).itertext()).replace("\u00a0", " ")
        assert "Retry opening browser" in text
        await pilot.press("escape")
        await settle(pilot)
        assert controllers[0].closed


async def test_waiting_and_exchanging_polls_do_not_move_keyboard_focus(login_setup):
    _, controllers = login_setup
    app = CswapApp(start="codex")
    async with app.run_test(size=(100, 32)) as pilot:
        modal = await open_login(pilot)
        await begin_login(pilot, modal)
        cancel = modal.query_one("#codex-login-cancel", Button)
        cancel.focus()
        for status in ("waiting", "exchanging"):
            controllers[0].status_result = {"ok": True, "status": status, "message": f"Sign-in {status}."}
            await poll_once(pilot, modal)
            assert cancel.has_focus
            assert status in modal.query_one("#codex-login-message", Static).render().plain
        assert not any(c[0] == "complete" for c in controllers[0].calls)


async def test_cancel_does_not_remove_a_save_already_committing(login_setup):
    switcher, controllers = login_setup
    app = CswapApp(start="codex")
    committed = threading.Event()
    release = threading.Event()
    async with app.run_test(size=(100, 32)) as pilot:
        modal = await open_login(pilot)
        await begin_login(pilot, modal)
        controller = controllers[0]
        controller.status_result = {"ok": True, "status": "ready", "message": "Ready."}
        complete = controller.complete

        def delayed_result(*args, **kwargs):
            result = complete(*args, **kwargs)
            committed.set()
            assert release.wait(5)
            return result

        controller.complete = delayed_result
        try:
            modal._poll()
            assert await asyncio.to_thread(committed.wait, 2)
            await pilot.press("escape")
            await pilot.pause()
        finally:
            release.set()
        await settle(pilot)
        assert [a.number for a in switcher.list_accounts()] == ["1", "2"]
        assert switcher.pending == {"2"} and not switcher.switched_to
        assert controller.closed and controller.session is None
        assert app.screen.snapshot.active_number == "1"


async def test_unknown_process_status_blocks_auto_ticks_too(login_setup, monkeypatch):
    switcher, _ = login_setup
    monkeypatch.setattr(codex_module.CodexDesktop, "status", lambda _: {
        "available": False, "running": None,
    })
    ticks = []
    monkeypatch.setattr("agents_switcher.codex.autoswitch.run_once", lambda *args, **kwargs: ticks.append(True))
    app = CswapApp(start="codex")
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        dashboard = app.screen
        await pilot.press("g")
        await settle(pilot)
        events = dashboard.actions.auto.status("codex")["events"]
        assert any(event["kind"] == "blocked" for event in events)
        assert not ticks and not switcher.switched_to
        await pilot.press("escape")
        await settle(pilot)


async def test_save_only_copy_acknowledges_enabled_auto_switch_rules(login_setup, monkeypatch):
    _, controllers = login_setup
    app = CswapApp(start="codex")
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        dashboard = app.screen
        status = dashboard.actions.auto.status("codex")
        monkeypatch.setattr(dashboard.actions.auto, "status", lambda _: {**status, "mode": "live"})
        modal = await open_login(pilot)
        text = " ".join(widget.render().plain for widget in modal.query(Static))
        assert "Enabled auto-switch rules still apply" in text
        await begin_login(pilot, modal)
        controllers[0].status_result = {"ok": True, "status": "ready", "message": "Ready."}
        await poll_once(pilot, modal)
        assert "Enabled auto-switch rules still apply" in dashboard.query_one("#codex-status", Static).render().plain
