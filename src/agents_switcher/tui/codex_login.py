"""Save-only browser sign-in using the shared Codex enrollment controller."""

from __future__ import annotations

import asyncio
import threading

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from agents_switcher.codex.enrollment import CodexEnrollment, EnrollmentError
from agents_switcher.providers import safe_error


class CodexLoginModal(ModalScreen[dict | None]):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("left", "app.focus_previous", show=False),
        Binding("right", "app.focus_next", show=False),
    ]

    def __init__(self, source, *, number: str | None = None, label: str = "") -> None:
        super().__init__()
        self._source = source
        self._number = number
        self._label = label
        self._enrollment = CodexEnrollment(source.switcher, browser=True)
        self._session_id: str | None = None
        self._cancelled = threading.Event()
        self._busy = False
        self._next_action = "start"
        self._open_error: str | None = None
        self._poll_timer = None
        self._cleanup_task = None

    def compose(self) -> ComposeResult:
        purpose = (
            f"Sign in as {self._label} to replace its saved Codex login."
            if self._number else "Sign in to save another Codex account."
        )
        with VerticalScroll(classes="modal-box"):
            yield Label(
                "Sign in again" if self._number else "Sign in with browser",
                classes="modal-title",
            )
            yield Static(
                f"{purpose}\n\nContinuing authorizes automatically saving the login. "
                "This sign-in saves only; it does not switch your active login. "
                f"Choose Switch account separately to use it.{self._automation_notice()}\n\n"
                "The browser and this TUI must be on the same computer.",
                classes="modal-body", markup=False,
            )
            yield Static("", id="codex-login-message", classes="modal-body", markup=False)
            with Horizontal(classes="modal-buttons"):
                yield Button("Continue in browser", id="codex-login-continue")
                yield Button("Cancel", id="codex-login-cancel")
            yield Static("← → · enter  ·  esc cancel", classes="modal-hint")

    def on_mount(self) -> None:
        self.query_one("#codex-login-continue", Button).focus()

    async def on_unmount(self) -> None:
        result = await self.close_session()
        if self._source.is_mounted and self._source._login_modal is self:
            self._source._login_done(result if result and result.get("warning") else None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "codex-login-cancel":
            self.action_cancel()
        elif event.button.id == "codex-login-continue":
            self._start_operation(self._next_action)

    def _show(self, message: str, *, action: str = "", label: str = "Waiting…") -> None:
        self._next_action = action
        self.query_one("#codex-login-message", Static).update(message)
        button = self.query_one("#codex-login-continue", Button)
        button.label = label
        button.disabled = not action
        if action:
            button.remove_class("-active")
        button.refresh(layout=True)

    def _automation_notice(self) -> str:
        if self._source.actions.auto.status("codex")["mode"] == "live":
            return " Enabled auto-switch rules still apply."
        return ""

    def _stop_polling(self) -> None:
        if self._poll_timer is not None:
            self._poll_timer.stop()
            self._poll_timer = None

    def _ensure_polling(self) -> None:
        if self._poll_timer is None and not self._cancelled.is_set():
            self._poll_timer = self.set_interval(1, self._poll)

    def _poll(self) -> None:
        self._start_operation("status")

    def _start_operation(self, operation: str) -> None:
        if not operation or self._busy or self._cancelled.is_set():
            return
        self._busy = True
        if operation == "start":
            self._open_error = None
        if operation != "status":
            self._stop_polling()
            self._show("Saving the login…" if operation == "save" else "Opening browser sign-in…")
        self.run_worker(self._operate(operation), group="codex-login", exit_on_error=False)

    async def _operate(self, operation: str) -> None:
        try:
            if operation == "start":
                if self._session_id is not None:
                    result = await asyncio.to_thread(
                        self._enrollment.cancel, self._session_id, confirm=True,
                    )
                    if not result["ok"]:
                        raise EnrollmentError(result["message"])
                    if self._cancelled.is_set():
                        return
                    self._session_id = None
                result = await asyncio.to_thread(
                    self._enrollment.prepare, number=self._number, confirm=True,
                )
                if self._cancelled.is_set():
                    return
                self._session_id = result["sessionId"]
                operation = "open"
            if operation == "open":
                await asyncio.to_thread(self._enrollment.open_browser, self._session_id, confirm=True)
                if self._cancelled.is_set():
                    return
                self._open_error = None
                self._show("Waiting for sign-in in your browser. The login will be saved automatically.")
                self._ensure_polling()
                return
            if operation == "status":
                result = await asyncio.to_thread(self._enrollment.status, self._session_id, confirm=True)
                if self._cancelled.is_set():
                    return
                if result["status"] in {"waiting", "exchanging"}:
                    if result["status"] == "waiting" and self._open_error:
                        self._show(self._open_error, action="open", label="Retry opening browser")
                    else:
                        self._open_error = None
                        self._show(result["message"])
                    self._ensure_polling()
                    return
                self._stop_polling()
                self._open_error = None
                if result["status"] not in {"ready", "saved"}:
                    self._show(result["message"], action="start", label="Start again")
                    return
                operation = "save"
                self._show("Saving the login…")
            if operation == "save":
                result = await asyncio.to_thread(self._save)
                if self._cancelled.is_set():
                    return
                if not result["ok"]:
                    raise EnrollmentError(result["message"])
                message = (
                    "Codex login saved without switching. "
                    f"Choose Switch account to use the saved login.{self._automation_notice()}"
                )
                if result.get("warning"):
                    message += " " + result["cleanupWarning"]
                self.dismiss({**result, "message": message})
        except Exception as exc:
            if not self._cancelled.is_set():
                self._stop_polling()
                action, label = {
                    "open": ("open", "Retry opening browser"),
                    "save": ("save", "Retry save"),
                    "status": ("status", "Check again"),
                }.get(operation, ("start", "Start again"))
                if isinstance(exc, EnrollmentError) and exc.code == "wrong-account":
                    action, label = "start", "Start again"
                command = "agent-switch codex login"
                if self._number is not None:
                    command += f" --account {self._number}"
                message = f"{safe_error(exc)}\n\nIf browser sign-in is unavailable, cancel and run:\n{command}"
                if operation == "open":
                    self._open_error = message
                    self._ensure_polling()
                self._show(message, action=action, label=label)
        finally:
            self._busy = False

    def _save(self) -> dict | None:
        while not self._cancelled.is_set():
            if not self._source.actions.lock.acquire(timeout=0.1):
                continue
            try:
                if self._cancelled.is_set():
                    return None
                result = self._enrollment.complete(self._session_id, confirm=True)
                if result["ok"]:
                    self._source.actions.revision += 1
                return result
            finally:
                self._source.actions.lock.release()
        return None

    def action_cancel(self) -> None:
        if self._cancelled.is_set():
            return
        self._cancelled.set()
        self._stop_polling()
        self._show("Closing sign-in… Any login already saved will remain saved.")
        self.query_one("#codex-login-cancel", Button).disabled = True
        self.run_worker(self._cancel_and_dismiss(), group="codex-login-close", exit_on_error=False)

    async def _cancel_and_dismiss(self) -> None:
        result = await self.close_session()
        if self.is_mounted:
            self.dismiss(result if result and result.get("warning") else None)

    async def close_session(self) -> dict | None:
        self._cancelled.set()
        self._stop_polling()
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(asyncio.to_thread(self._close_blocking))
        return await asyncio.shield(self._cleanup_task)

    def _close_blocking(self) -> dict | None:
        result = None
        try:
            if self._session_id is not None:
                result = self._enrollment.cancel(self._session_id, confirm=True)
        except Exception as exc:
            result = {"ok": False, "warning": True, "message": safe_error(exc)}
        finally:
            try:
                self._enrollment.close()
            except Exception as exc:
                result = {"ok": False, "warning": True, "message": safe_error(exc)}
        return result
