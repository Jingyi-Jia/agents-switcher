"""The Codex accounts screen.

A SEPARATE screen rather than Codex rows merged into the Claude account list,
for two reasons. Codex and Claude accounts are not interchangeable -- "switch to
account 2" has to mean one provider or the other, and a merged list makes that
ambiguous exactly where a mistake is expensive. And keeping it here means the
shared dashboard takes one menu entry instead of a rewrite, which matters for a
fork tracking a fast-moving upstream.

Quota is fetched on a THREAD worker. Each account costs a network round trip and
possibly a token refresh, which would freeze the UI for seconds on the event
loop. The store read is instant, so the list appears immediately and fills in
with usage as it arrives -- the screen is never blank waiting on the network.
"""

from __future__ import annotations

import asyncio
from functools import partial
from typing import TYPE_CHECKING

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import Screen
from rich.text import Text
from textual.widgets import Button, Footer, Input, ListItem, ListView, Static

from claude_swap.codex.store import CodexAccount
from claude_swap.codex.switcher import CodexSwitcher
from claude_swap.codex.usage import CodexUsage
from claude_swap.providers import AUTO_SESSION_NOTICE, ProviderActions, safe_error
from claude_swap.tui.modals import ConfirmModal
from claude_swap.tui.theme import Palette
from claude_swap.tui.widgets import usage_bar

if TYPE_CHECKING:  # pragma: no cover - typing only
    from claude_swap.tui.app import CswapApp


#: Bar width in cells, matching the Claude minis that share this TUI.
_BAR_WIDTH = 10


class CodexAccountItem(ListItem):
    """One Codex account row: identity, then a bar per window once loaded.

    The bar is drawn by the Claude card's own ``usage_bar``, not a second
    renderer: the two lists sit in one app and must read as one object, and
    the first draft's own glyphs rendered as heavy solid blocks beside the
    Claude rows' thin lines. Reusing it also means this screen shows
    UTILISATION like its neighbours rather than the web dashboard's headroom --
    that framing is a deliberate choice there, but inside one TUI the adjacent
    rows win. Red still appears only in the CRIT band, via the shared palette.
    """

    def __init__(self, account: CodexAccount, active: bool, palette: Palette) -> None:
        self._body = Static("", markup=False)
        super().__init__(self._body)
        self.account = account
        self._active = active
        self._palette = palette
        self._paint(None, None)

    def set_usage(self, usage: CodexUsage | None, error: str | None) -> None:
        self._paint(usage, error)

    def _paint(self, usage: CodexUsage | None, error: str | None) -> None:
        """Redraw the row. NOT named _render: Textual's Widget owns that name
        and calls it with no arguments, so shadowing it breaks the widget."""
        pal = self._palette
        text = Text(no_wrap=True, overflow="ellipsis")
        text.append(" ● " if self._active else "   ", style=pal.foreground)
        text.append(f"{self.account.number:>2}  ", style=f"bold {pal.muted}")
        if self.account.alias:
            text.append(self.account.alias, style=f"bold {pal.foreground}")
            text.append(f" ({self.account.email})", style=pal.muted)
        else:
            text.append(
                self.account.email or self.account.account_id, style=pal.foreground
            )
        if self.account.plan:
            text.append(f"  [{self.account.plan}]", style=pal.muted)
        if self.account.disabled:
            text.append("  (disabled)", style=pal.muted)
        text.append("   ")

        if error:
            text.append("● ", style=pal.sev_crit)
            text.append(error, style=pal.sev_crit)
        elif usage is None:
            text.append("…", style=pal.muted)
        elif usage.on_credits:
            # The solid-pill analogue in a terminal: reverse video. It is not
            # red, because paying is a warning about money, not an error.
            text.append(" paid credits ", style=f"bold reverse {pal.foreground}")
            text.append("  manual switch only", style=pal.muted)
        elif not usage.windows:
            text.append("usage unknown", style=pal.muted)
        else:
            for i, w in enumerate(usage.windows):
                if i:
                    text.append("  ")
                suffix = None
                # Countdown only once a window is spent, as the Claude rows do.
                if w.used_percent >= 100 and w.reset_after_seconds:
                    d, h = divmod(w.reset_after_seconds // 3600, 24)
                    suffix = f"resets {d}d {h}h" if d else f"resets {h}h"
                text.append(
                    usage_bar(
                        w.label, float(w.used_percent), suffix, _BAR_WIDTH, palette=pal
                    )
                )
        self._body.update(text)


class CodexScreen(Screen):
    """Managed Codex accounts, their quota, and switching between them."""

    BINDINGS = [
        Binding("escape,q", "back", "Back"),
        Binding("r", "reload", "Refresh"),
        Binding("a", "add_current", "Add current"),
        Binding("d", "toggle_disabled", "Enable/disable"),
        Binding("delete", "remove", "Remove", show=False),
        Binding("b", "switch_best", "Best", show=False),
        Binding("g", "auto_dry_run", "Auto", show=False),
        Binding("p", "app.open_providers", "Providers"),
        # priority: the focused ListView has its own hidden enter binding, so
        # without this the key never reaches the screen and Enter does nothing.
        # The action delegates back to the list cursor, which emits Selected.
        Binding("enter", "select_highlighted", "Switch", priority=True),
        Binding("down,j", "cursor_down", "Down", show=False),
        Binding("up,k", "cursor_up", "Up", show=False),
    ]

    app: "CswapApp"

    def __init__(self) -> None:
        super().__init__()
        self.switcher = CodexSwitcher()
        self.actions = ProviderActions(codex=self.switcher)
        self._busy = False
        self._revision = self.actions.revision
        self._numbers: list[str] = []
        # A pinned message survives the usage reload that follows a switch.
        # Without this the restart notice -- the one line the user MUST read
        # -- is wiped a second later by 'Loading quota…' completing.
        self._pinned = False

    def compose(self) -> ComposeResult:
        yield Static("Codex accounts", id="list-title")
        yield Static(
            self.actions.switch_notice("codex"), id="codex-notice", markup=False
        )
        yield ListView(id="codex-accounts")
        with Horizontal(classes="codex-controls"):
            yield Button("Add current login", id="codex-add")
            yield Button("Remove…", id="codex-remove")
            yield Button("Enable / disable", id="codex-disable")
            yield Button("Switch best", id="codex-best")
        with Horizontal(classes="codex-controls"):
            yield Button("Auto dry-run", id="codex-dry-run")
            yield Button("Go live…", id="codex-live")
            yield Button("Stop auto", id="codex-stop")
            yield Input(
                str(self.actions.auto.status("codex")["threshold"]),
                type="number",
                id="codex-threshold",
                tooltip="Session-only auto-switch threshold (%)",
            )
            yield Button("Set threshold", id="codex-set-threshold")
        yield Static("", id="codex-auto-status", markup=False)
        yield Static("", id="codex-status")
        yield Footer()

    def on_mount(self) -> None:
        self._rebuild()
        self.query_one("#codex-accounts", ListView).focus()
        self._load_usage()
        self._update_auto_status()
        self.set_interval(1.0, self._update_auto_status)

    async def on_unmount(self) -> None:
        await asyncio.to_thread(self.actions.close)

    def on_screen_resume(self) -> None:
        self.app._claude_active = False

    # -- rendering -------------------------------------------------------

    def _rebuild(self) -> None:
        """Draw the roster from the store. No network, so this is instant."""
        listview = self.query_one("#codex-accounts", ListView)
        selected = self._selected_account()
        accounts = self.switcher.list_accounts()
        active = self.switcher.store.active_number()
        palette = Palette.from_theme(self.app.current_theme)
        listview.clear()
        for account in accounts:
            listview.append(
                CodexAccountItem(account, account.number == active, palette)
            )
        self._numbers = [a.number for a in accounts]
        listview.index = (
            self._numbers.index(selected.number)
            if selected is not None and selected.number in self._numbers
            else 0
            if accounts
            else None
        )
        if not accounts:
            self._status(
                "No Codex accounts managed yet — choose Add current login "
                "or run 'agent-switch codex add'."
            )

    def _status(self, message: str, *, pin: bool = False) -> None:
        """Show a message. ``pin`` keeps it until the next explicit status."""
        if self._pinned and not pin and message:
            return  # a transient update must not bury a pinned notice
        self._pinned = pin
        self.query_one("#codex-status", Static).update(message)

    def _apply_usage(self, results: dict[str, object]) -> None:
        for item in self.query(CodexAccountItem):
            result = results.get(item.account.number)
            if isinstance(result, Exception):
                item.set_usage(None, str(result))
            elif isinstance(result, CodexUsage):
                item.set_usage(result, None)
        if not self._pinned:
            self._status("")

    # -- workers ---------------------------------------------------------

    @work(thread=True, exclusive=True, group="codex-usage", exit_on_error=False)
    def _load_usage(self) -> None:
        """Fetch every account's quota off the event loop.

        Failures arrive as values in the result map (``usage_all`` reports per
        account), so one unreachable account shows its error on its own row
        instead of blanking the screen.
        """
        if not self._numbers:
            return
        self.app.call_from_thread(self._status, "Loading quota…")
        try:
            with self.actions.lock:
                results = self.switcher.usage_all()
        except Exception as exc:
            if self.is_mounted:
                self.app.call_from_thread(
                    self._status, f"Could not load quota: {safe_error(exc)}"
                )
            return
        if self.is_mounted:
            self.app.call_from_thread(self._apply_usage, results)

    def _do_switch(self, number: str) -> None:
        self._start_action("switch", partial(self.actions.switch, "codex", number))

    def _start_action(self, label: str, fn) -> None:
        if self._busy:
            self.app.notify("Another Codex action is still running", severity="warning")
            return
        self._busy = True
        self._status(f"Running {label}…", pin=True)
        self._run_action(label, fn)

    @work(thread=True, group="codex-action")
    def _run_action(self, label: str, fn) -> None:
        try:
            result = fn()
            message = result.get("message") or result.get("reason") or "Done."
        except Exception as exc:
            message = f"Could not {label}: {safe_error(exc)}"
        if self.is_mounted:
            self.app.call_from_thread(self._action_done, message)

    def _action_done(self, message: str) -> None:
        self._busy = False
        self._status(message, pin=True)
        self._revision = self.actions.revision
        self._rebuild()
        self._load_usage()
        self._update_auto_status()

    def _update_auto_status(self) -> None:
        state = self.actions.auto.status("codex")
        events = state["events"]
        latest = events[-1]["message"] if events else ""
        self.query_one("#codex-auto-status", Static).update(
            f"Auto: {state['mode']} · threshold {state['threshold']:g}%\n"
            + (latest or AUTO_SESSION_NOTICE)
        )
        if self.actions.revision != self._revision:
            self._revision = self.actions.revision
            self._rebuild()
            self._load_usage()

    def _selected_account(self) -> CodexAccount | None:
        item = self.query_one("#codex-accounts", ListView).highlighted_child
        return item.account if isinstance(item, CodexAccountItem) else None

    def _confirm(self, title: str, message: str, fn) -> None:
        self.app.push_screen(
            ConfirmModal(message, title=title, yes_label="Confirm"),
            lambda yes: self._start_action(title.lower(), fn) if yes else None,
        )

    def action_add_current(self) -> None:
        self._confirm(
            "Add current login",
            "Save the current Codex CLI login as a managed account?",
            partial(self.actions.add_current, "codex"),
        )

    def action_remove(self) -> None:
        account = self._selected_account()
        if account is not None:
            self._confirm(
                "Remove account",
                f"Remove saved Codex account {account.number} ({account.display_label})?\n\n"
                "Its stored credentials are deleted. The live Codex login is unchanged.",
                partial(self.actions.remove, "codex", account.number, confirm=True),
            )

    def action_toggle_disabled(self) -> None:
        account = self._selected_account()
        if account is not None:
            self._start_action(
                "enable account" if account.disabled else "disable account",
                partial(
                    self.actions.set_disabled,
                    "codex",
                    account.number,
                    not account.disabled,
                ),
            )

    def action_switch_best(self) -> None:
        self._start_action("switch best", partial(self.actions.switch_best, "codex"))

    def _configure_auto(self, mode: str, *, update_threshold: bool = False) -> None:
        threshold = None
        if mode != "stopped" or update_threshold:
            try:
                threshold = float(self.query_one("#codex-threshold", Input).value)
            except ValueError:
                self._status("Enter an auto-switch threshold percentage.", pin=True)
                return
        fn = partial(
            self.actions.auto.configure,
            "codex",
            mode,
            threshold=threshold,
            confirm=mode == "live",
        )
        if mode == "live":
            self._confirm(
                "Start live auto-switching",
                "Automatically switch Codex accounts as included quota runs low?\n\n"
                "Paid credits and disabled accounts are never automatic targets; "
                "running Codex processes prevent an automatic switch.\n\n"
                + AUTO_SESSION_NOTICE,
                fn,
            )
        else:
            self._start_action(f"auto {mode}", fn)

    def action_auto_dry_run(self) -> None:
        self._configure_auto("dry-run")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        handlers = {
            "codex-add": self.action_add_current,
            "codex-remove": self.action_remove,
            "codex-disable": self.action_toggle_disabled,
            "codex-best": self.action_switch_best,
            "codex-dry-run": self.action_auto_dry_run,
            "codex-live": partial(self._configure_auto, "live"),
            "codex-stop": partial(self._configure_auto, "stopped"),
            "codex-set-threshold": partial(
                self._configure_auto,
                self.actions.auto.status("codex")["mode"],
                update_threshold=True,
            ),
        }
        handler = handlers.get(event.button.id)
        if handler is not None:
            event.stop()
            handler()

    # -- actions ---------------------------------------------------------

    def action_reload(self) -> None:
        self._pinned = False
        self._rebuild()
        self._load_usage()

    def action_select_highlighted(self) -> None:
        listview = self.query_one("#codex-accounts", ListView)
        if listview.display and listview.has_focus:
            listview.action_select_cursor()
        elif isinstance(self.focused, Button):
            self.focused.press()
        elif isinstance(self.focused, Input):
            self._configure_auto(
                self.actions.auto.status("codex")["mode"], update_threshold=True
            )

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, CodexAccountItem):
            self._status(f"Switching to {item.account.display_label}…")
            self._do_switch(item.account.number)

    def action_back(self) -> None:
        if self._busy:
            self.app.notify("Wait for the Codex action to finish", severity="warning")
            return
        self.app.pop_screen()

    def action_cursor_down(self) -> None:
        self.query_one("#codex-accounts", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#codex-accounts", ListView).action_cursor_up()
