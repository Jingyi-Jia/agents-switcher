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

from typing import TYPE_CHECKING

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from rich.text import Text
from textual.widgets import Footer, ListItem, ListView, Static

from claude_swap.codex.store import CodexAccount
from claude_swap.codex.switcher import CodexSwitcher
from claude_swap.codex.usage import CodexUsage
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
            text.append(self.account.email or self.account.account_id, style=pal.foreground)
        if self.account.plan:
            text.append(f"  [{self.account.plan}]", style=pal.muted)
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
                text.append(usage_bar(
                    w.label, float(w.used_percent), suffix, _BAR_WIDTH, palette=pal
                ))
        self._body.update(text)


class CodexScreen(Screen):
    """Managed Codex accounts, their quota, and switching between them."""

    BINDINGS = [
        Binding("escape,q", "back", "Back"),
        Binding("r", "reload", "Refresh"),
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
        self._numbers: list[str] = []
        # A pinned message survives the usage reload that follows a switch.
        # Without this the restart notice -- the one line the user MUST read
        # -- is wiped a second later by 'Loading quota…' completing.
        self._pinned = False

    def compose(self) -> ComposeResult:
        yield Static("Codex accounts", id="list-title")
        yield ListView(id="codex-accounts")
        yield Static("", id="codex-status")
        yield Footer()

    def on_mount(self) -> None:
        self._rebuild()
        self.query_one("#codex-accounts", ListView).focus()
        self._load_usage()

    # -- rendering -------------------------------------------------------

    def _rebuild(self) -> None:
        """Draw the roster from the store. No network, so this is instant."""
        listview = self.query_one("#codex-accounts", ListView)
        accounts = self.switcher.list_accounts()
        active = self.switcher.store.active_number()
        palette = Palette.from_theme(self.app.current_theme)
        listview.clear()
        for account in accounts:
            listview.append(
                CodexAccountItem(account, account.number == active, palette)
            )
        self._numbers = [a.number for a in accounts]
        listview.index = 0 if accounts else None
        if not accounts:
            self._status(
                "No Codex accounts managed yet — run 'agent-switch codex add'."
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

    @work(thread=True, exclusive=True, group="codex-usage")
    def _load_usage(self) -> None:
        """Fetch every account's quota off the event loop.

        Failures arrive as values in the result map (``usage_all`` reports per
        account), so one unreachable account shows its error on its own row
        instead of blanking the screen.
        """
        if not self._numbers:
            return
        self.app.call_from_thread(self._status, "Loading quota…")
        results = self.switcher.usage_all()
        self.app.call_from_thread(self._apply_usage, results)

    @work(thread=True, exclusive=True, group="codex-switch")
    def _do_switch(self, number: str) -> None:
        """Switch, then report what the user must still do.

        A Codex switch is only real for processes started afterwards, so a
        running Codex means the message has to say so rather than claim success.
        """
        try:
            result = self.switcher.switch_to(number)
        except Exception as e:  # noqa: BLE001 - shown to the user verbatim
            self.app.call_from_thread(lambda: self._status(f"Could not switch: {e}", pin=True))
            return
        message = f"Switched to {result.account.display_label}."
        if result.restart_required:
            message += (
                f"  Restart Codex — {len(result.processes)} process(es) are still "
                "signed in as the previous account."
            )
        self.app.call_from_thread(lambda: self._status(message, pin=True))
        self.app.call_from_thread(self._rebuild)
        self._load_usage()

    # -- actions ---------------------------------------------------------

    def action_reload(self) -> None:
        self._pinned = False
        self._rebuild()
        self._load_usage()

    def action_select_highlighted(self) -> None:
        listview = self.query_one("#codex-accounts", ListView)
        if listview.display:
            listview.action_select_cursor()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, CodexAccountItem):
            self._status(f"Switching to {item.account.display_label}…")
            self._do_switch(item.account.number)

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_cursor_down(self) -> None:
        self.query_one("#codex-accounts", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#codex-accounts", ListView).action_cursor_up()
