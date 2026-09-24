"""Codex data and actions hosted by the shared provider TUI views."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from functools import partial

from rich.text import Text
from textual.reactive import reactive
from textual.widgets import RichLog, Static

from claude_swap.codex.switcher import CodexSwitcher
from claude_swap.codex.usage import CodexUsage
from claude_swap.models import AccountSnapshot, AccountsSnapshot
from claude_swap.providers import AUTO_SESSION_NOTICE, ProviderActions, safe_error
from claude_swap.settings import SETTING_SPECS
from claude_swap.tui.autoview import AutoView
from claude_swap.tui.dashboard import DashboardScreen
from claude_swap.tui.modals import ConfirmModal
from claude_swap.tui.theme import Palette
from claude_swap.usage_store import UsageEntry


def codex_snapshot(accounts, active, usage, fetched_at) -> AccountsSnapshot:
    """Project Codex quota into the common, read-only account-card model."""
    rows = []
    now = time.time()
    for account in accounts:
        result = usage.get((account.number, account.account_id))
        entry = UsageEntry()
        if isinstance(result, Exception):
            entry = UsageEntry(sentinel=safe_error(result, include_detail=True))
        elif isinstance(result, CodexUsage):
            windows = []
            for window in result.windows:
                row = {"label": window.label, "pct": window.used_percent}
                reset = window.reset_at
                if reset is None and window.reset_after_seconds is not None:
                    reset = fetched_at + window.reset_after_seconds
                if reset is not None:
                    row["resets_at"] = datetime.fromtimestamp(reset, timezone.utc).isoformat()
                windows.append(row)
            sentinel = "paid credits · manual switch only" if result.on_credits else None
            if not result.usable and not sentinel:
                sentinel = result.summary
            entry = UsageEntry(
                last_good={"windows": windows}, sentinel=sentinel,
                fetched_at=fetched_at, age_s=max(0, now - fetched_at),
            )
        rows.append(AccountSnapshot(
            number=account.number, email=account.email or account.account_id,
            org_name=account.plan, org_uuid="", is_active=account.number == active,
            kind="oauth", switchable=True, usage=entry,
            alias=account.alias, disabled=account.disabled,
        ))
    return AccountsSnapshot(active, tuple(rows), now)


class CodexScreen(DashboardScreen):
    provider = "codex"
    status_id = "codex-status"
    empty_message = (
        "No managed accounts yet.\nUse the menu below: Add account — from your "
        "current Codex login, or run 'agent-switch codex add'."
    )
    snapshot: reactive[AccountsSnapshot | None] = reactive(None)
    threshold_pct: reactive[float | None] = reactive(None)
    refresh_status: reactive[str] = reactive("")

    def __init__(self) -> None:
        super().__init__()
        self.switcher = CodexSwitcher()
        self.actions = ProviderActions(codex=self.switcher)
        self.threshold_pct = self.actions.auto.status("codex")["threshold"]
        self._busy = False
        self._refreshing = False
        self._refresh_again = False
        self._revision = self.actions.revision
        self._usage = {}
        self._fetched_at = time.time()
        self._message = ""

    @property
    def controller(self):
        return self

    def on_mount(self) -> None:
        self.app._claude_active = False
        self._rebuild()
        self.call_after_refresh(self.request_refresh)
        self.set_interval(30, self.request_refresh)
        self.set_interval(1, self._check_revision)

    async def on_unmount(self) -> None:
        await asyncio.to_thread(self.actions.close)

    def _rebuild(self) -> None:
        accounts = self.switcher.list_accounts()
        self.snapshot = codex_snapshot(
            accounts, self.switcher.store.active_number(), self._usage, self._fetched_at,
        )

    def request_refresh(self) -> None:
        if self.app._claude_active or not self.is_mounted:
            return
        if self._refreshing:
            self._refresh_again = True
            return
        self._refreshing = True
        self.refresh_status = "refreshing usage…"
        self.run_worker(self._refresh_blocking, thread=True, exit_on_error=False)

    def _refresh_blocking(self) -> None:
        try:
            with self.actions.lock:
                accounts = self.switcher.list_accounts()
                usage = self.switcher.usage_all() if accounts else {}
                usage = {(a.number, a.account_id): usage.get(a.number) for a in accounts}
                fetched_at = time.time()
                snapshot = codex_snapshot(
                    accounts, self.switcher.store.active_number(), usage, fetched_at,
                )
                revision = self.actions.revision
            if self.is_mounted:
                self.app.call_from_thread(self._apply_snapshot, snapshot, usage, fetched_at, revision)
        except Exception as exc:
            if self.is_mounted:
                self.app.call_from_thread(self._refresh_failed, safe_error(exc, include_detail=True))

    def _apply_snapshot(self, snapshot, usage, fetched_at, revision) -> None:
        self._refreshing = False
        if revision == self.actions.revision:
            self._usage = usage
            self._fetched_at = fetched_at
            self.snapshot = snapshot
            self._revision = revision
            self.refresh_status = self._message
        else:
            self._refresh_again = True
        if self._refresh_again:
            self._refresh_again = False
            self.request_refresh()

    def _refresh_failed(self, message: str) -> None:
        self._refreshing = False
        self._refresh_again = False
        self.refresh_status = f"Could not load quota: {message}"
        self._status(self.refresh_status)

    def _check_revision(self) -> None:
        if self.actions.revision != self._revision and not self._refreshing:
            self.request_refresh()

    def _status(self, message: str) -> None:
        self._message = message
        self.refresh_status = message
        self.query_one("#codex-status", Static).update(message)

    def _start_action(self, label: str, fn) -> None:
        if self._busy:
            self.app.notify("Another Codex action is still running", severity="warning")
            return
        self._busy = True
        self.run_worker(partial(self._run_action, label, fn), thread=True, exit_on_error=False)

    def _run_action(self, label: str, fn) -> None:
        try:
            result = fn()
        except Exception as exc:
            result = {"ok": False, "message": f"Could not {label}: {safe_error(exc)}"}
        if self.is_mounted:
            self.app.call_from_thread(self._action_done, result)

    def _action_done(self, result: dict) -> None:
        self._busy = False
        message = result.get("message") or result.get("reason") or "Done."
        self._status(message)
        self.app.notify(
            message, severity="warning" if result.get("restartRequired") else
            "information" if result.get("ok") else "error", timeout=8, markup=False,
        )
        self.threshold_pct = self.actions.auto.status("codex")["threshold"]
        self.request_refresh()

    def do_switch(self, number: str) -> None:
        self._start_action("switch", partial(self.actions.switch, "codex", number))

    def action_switch_best(self) -> None:
        self._start_action("switch best", partial(self.actions.switch_best, "codex"))

    def _confirm(self, title: str, message: str, fn) -> None:
        self.app.push_screen(
            ConfirmModal(message, title=title, yes_label="Confirm"),
            lambda yes: self._start_action(title.lower(), fn) if yes else None,
        )

    def action_add_current(self) -> None:
        self._confirm(
            "Add current login", "Save the current Codex CLI login as a managed account?\n\n"
            "If already managed, its saved credentials are refreshed in place.",
            partial(self.actions.add_current, "codex"),
        )

    def confirm_remove(self, number: str, email: str) -> None:
        self._confirm(
            "Remove account", f"Remove saved Codex account {number} ({email})?\n\n"
            "Its stored credentials are deleted. The live Codex login is unchanged.",
            partial(self.actions.remove, "codex", number, confirm=True),
        )

    def do_toggle_disabled(self, number: str) -> None:
        account = next((a for a in self.snapshot.accounts if a.number == number), None)
        if account is not None:
            self._start_action(
                "enable account" if account.disabled else "disable account",
                partial(self.actions.set_disabled, "codex", number, not account.disabled),
            )

    def action_refresh_full(self) -> None:
        self._status("")
        self.request_refresh()

    def action_open_auto(self) -> None:
        self.app.push_screen(CodexAutoScreen(self))


class CodexAutoScreen(AutoView):
    def __init__(self, source: CodexScreen) -> None:
        super().__init__()
        self._source = source
        self._adjusting = False
        self._threshold = source.threshold_pct
        self._configured_threshold = self._threshold
        self._seen_events = []

    @property
    def source(self):
        return self._source

    def on_mount(self) -> None:
        self.watch(self.source, "snapshot", lambda _: self._update())
        self.watch(self.app, "theme", lambda _: self._update())
        self.source._start_action("auto dry-run", partial(self.source.actions.auto.configure, "codex", "dry-run"))
        self.set_interval(0.5, self._update)
        self._update()

    async def on_unmount(self) -> None:
        result = await asyncio.to_thread(self.source.actions.auto.configure, "codex", "stopped")
        if self.source.is_mounted:
            self.source._status(result["message"])

    def _update(self) -> None:
        state = self.source.actions.auto.status("codex")
        if not self._adjusting:
            self._threshold = state["threshold"]
        palette = Palette.from_theme(self.app.current_theme)
        badge = self.query_one("#mode-badge", Static)
        badge.update(f" {state['mode'].upper()} ")
        badge.set_classes("live" if state["mode"] == "live" else "dry")
        summary = f"auto-switch · threshold {self._threshold:g}%"
        if self._threshold != self._configured_threshold:
            summary += " (session)"
        if self._adjusting:
            summary += "   ← → adjust · enter done"
        self.query_one("#auto-summary", Static).update(summary)
        text = Text("Next best", style=palette.muted)
        candidates = []
        for account in self.source.snapshot.accounts if self.source.snapshot else ():
            result = next((v for (number, _), v in self.source._usage.items() if number == account.number), None)
            if account.is_active or account.disabled:
                continue
            eligible = isinstance(result, CodexUsage) and result.auto_switch_eligible
            percent = result.binding_percent if eligible else None
            candidates.append((percent if percent is not None else 999, account, result))
        for percent, account, result in sorted(candidates, key=lambda row: (row[0], row[1].number)):
            text.append(f"\n  {account.number:>2}  {account.email}", style=palette.foreground)
            if percent != 999:
                text.append(f"  {percent:3.0f}% used", style=palette.severity(percent))
            else:
                note = "manual switch only" if isinstance(result, CodexUsage) and result.on_credits else "not eligible"
                text.append(f"  {note}", style=palette.muted)
        if not candidates:
            text.append("\n  no other switchable accounts", style=palette.muted)
        self.query_one("#candidates", Static).update(text)
        events = state["events"]
        if events != self._seen_events:
            log = self.query_one("#event-log", RichLog)
            log.clear()
            for event in events:
                style = palette.sev_warn if event["kind"] == "error" else palette.foreground
                stamp = datetime.fromisoformat(event["at"]).astimezone().strftime("%H:%M:%S")
                log.write(Text(f"{stamp}  {event['message']}", style=style))
            self._seen_events = events

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        if action in ("threshold_step", "adjust_done") and not self._adjusting:
            return False
        return True

    def action_adjust_threshold(self) -> None:
        if self._adjusting:
            self.action_adjust_done()
        else:
            self._adjusting = True
            self._update()
            self.refresh_bindings()

    def action_threshold_step(self, delta: float) -> None:
        if not self._adjusting:
            return
        spec = SETTING_SPECS["autoswitch.threshold"]
        self._threshold = min(spec.hi, max(spec.lo, self._threshold + delta))
        self._update()

    def action_adjust_done(self) -> None:
        if not self._adjusting:
            return
        state = self.source.actions.auto.status("codex")
        self.source._start_action("set threshold", partial(
            self.source.actions.auto.configure, "codex", state["mode"],
            threshold=self._threshold, confirm=state["mode"] == "live",
        ))
        self._adjusting = False
        self.refresh_bindings()

    def action_toggle_live(self) -> None:
        if self.source._busy:
            return
        state = self.source.actions.auto.status("codex")
        if state["mode"] == "live":
            self.source._start_action("auto dry-run", partial(self.source.actions.auto.configure, "codex", "dry-run"))
        else:
            self.source._confirm(
                "Start live auto-switching", "Automatically switch Codex accounts as included quota runs low?\n\n"
                "Paid credits and disabled accounts are never automatic targets; "
                "running Codex processes prevent an automatic switch.\n\n" + AUTO_SESSION_NOTICE,
                partial(self.source.actions.auto.configure, "codex", "live", confirm=True),
            )

    def action_back(self) -> None:
        if self.source._busy:
            self.app.notify("Wait for the Codex action to finish", severity="warning")
        elif self._adjusting:
            self.action_adjust_done()
        else:
            self.app.pop_screen()
