"""Shared render widgets: usage bars, account cards, and the accounts panel.

``bar_cells``/``usage_bar`` are custom renderers rather than Textual's
``ProgressBar`` because the design needs three things the stock widget
doesn't do: a severity color ramp, an optional threshold tick mark (the
auto-switch trigger line), and stale-measurement dimming.
"""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING

from rich.text import Text
from textual.widgets import ListItem, Static

from agents_switcher import oauth, pace, printer
from agents_switcher.json_output import USAGE_API_KEY
from agents_switcher.models import AccountSnapshot
from agents_switcher.switcher import ERROR_NOTES
from agents_switcher.usage_store import STALE_OK_S
from agents_switcher.tui import data
from agents_switcher.tui.theme import Palette

if TYPE_CHECKING:
    from agents_switcher.tui.app import CswapApp

_BAR_FILLED = "━"
_BAR_HALF = "╸"
_BAR_EMPTY = "─"
_BAR_TICK = "┃"


def remaining_pct(pct: float | None) -> float | None:
    """Display-only available quota from a reported utilization percentage."""
    if pct is None or not math.isfinite(pct):
        return None
    return min(100.0, max(0.0, 100.0 - pct))


class AppHeader(Static):
    def __init__(self, provider: str | None = None) -> None:
        super().__init__(classes="app-header")
        self.provider = provider

    def on_mount(self) -> None:
        self.watch(self.app, "theme", lambda _: self.refresh())

    def render(self) -> Text:
        palette = Palette.from_theme(self.app.current_theme, self.provider)
        text = Text("▦  ", style=palette.active)
        text.append("Agent Switch", style=f"bold {palette.foreground}")
        if self.provider:
            text.append("   /   ", style=palette.muted)
            text.append(
                "Claude Code" if self.provider == "claude" else "Codex",
                style=palette.accent,
            )
        else:
            text.append("   /   Accounts", style=palette.muted)
        return text


def bar_cells(
    pct: float | None,
    width: int,
    *,
    stale: bool = False,
    threshold: float | None = None,
    palette: Palette = Palette.DARK,
) -> Text:
    """Available-quota fill and tick, with severity based on utilization."""
    text = Text()
    left = remaining_pct(pct)
    if left is None:
        text.append(_BAR_EMPTY * width, style=palette.track)
        return text
    frac = left / 100.0
    cells = frac * width
    full = int(cells)
    half = (cells - full) >= 0.5 and full < width
    tick_at: int | None = None
    if threshold is not None:
        tick_at = min(width - 1, max(0, round((100.0 - threshold) / 100.0 * width)))
    color = palette.severity(pct)
    fill_style = f"{color} dim" if stale else color
    for i in range(width):
        if tick_at is not None and i == tick_at:
            text.append(_BAR_TICK, style=palette.sev_warn)
        elif i < full:
            text.append(_BAR_FILLED, style=fill_style)
        elif i == full and half:
            text.append(_BAR_HALF, style=fill_style)
        else:
            text.append(_BAR_EMPTY, style=palette.track)
    return text


def usage_bar(
    label: str,
    pct: float | None,
    suffix: str | None,
    width: int,
    *,
    stale: bool = False,
    threshold: float | None = None,
    palette: Palette = Palette.DARK,
) -> Text:
    """One remaining-quota line; inputs retain utilization semantics."""
    text = Text()
    text.append(f"{label} ", style=palette.muted)
    text.append(bar_cells(pct, width, stale=stale, threshold=threshold, palette=palette))
    left = remaining_pct(pct)
    if left is None:
        text.append("  quota unknown", style=palette.muted)
    else:
        color = palette.severity(pct)
        text.append(f" {left:3.0f}% left", style=f"{color} dim" if stale else color)
    if suffix:
        text.append(f"  {suffix}", style=palette.muted)
    return text


def _reset_parts(window: dict, now: float) -> tuple[str | None, str | None]:
    """Countdown suffix and its clock-extended variant for one window.

    ``("resets 2h 13m", "resets 2h 13m · 20:39")`` — the second form is what
    a row shows when it has the width for it. Equal when no clock is known.
    """
    reset = data.reset_text(window, now)
    if not reset:
        return None, None
    clock = data.reset_clock(window, now)
    return reset, f"{reset} · {clock}" if clock else reset


def _pace_suffix(window: dict, fetched_at: float | None) -> str:
    """"(ahead of pace)" when a weekly window is meaningfully ahead, else ""."""
    result = pace.compute_pace(window, fetched_at=fetched_at)
    return "(ahead of pace)" if result and result.ahead else ""


def usage_rows(
    last_good: dict | None, now: float, fetched_at: float | None = None
) -> list[tuple[str, float, str, str]]:
    """(label, used pct, suffix, suffix_full) rows for quota renderers.

    ``suffix_full`` extends the reset countdown with the absolute clock time
    (``resets 2h 13m · 20:39``) for rows that have room; otherwise it equals
    ``suffix``. Only windows the account actually has produce a row — an
    annual plan without a 7-day window simply has no 7d line. Order matches
    the CLI: spend, 5h, 7d, then per-model scoped windows (e.g. "Fable"),
    the latter marked ``(!)`` at/over their limit. The weekly (7d) and scoped
    rows also carry a "(ahead of pace)" marker when meaningfully ahead of the
    week's expected usage (issue #125) — never the 5h row.
    """
    if not isinstance(last_good, dict):
        return []
    rows: list[tuple[str, float, str, str]] = []
    if "windows" in last_good:
        for window in last_good["windows"]:
            reset, reset_full = _reset_parts(window, now)
            rows.append((window["label"], float(window["pct"]), reset or "", reset_full or ""))
        return rows
    spend = last_good.get("spend")
    if spend:
        amounts = f"${max(0.0, spend['limit'] - spend['used']):,.2f} / ${spend['limit']:,.2f} left"
        reset, reset_full = _reset_parts(spend, now)
        suffix = f"{reset}  {amounts}" if reset else amounts
        suffix_full = f"{reset_full}  {amounts}" if reset_full else amounts
        rows.append(("$$", float(spend["pct"]), suffix, suffix_full))
    for key, label in (("five_hour", "5h"), ("seven_day", "7d")):
        window = last_good.get(key)
        if window:
            reset, reset_full = _reset_parts(window, now)
            suffix, suffix_full = reset or "", reset_full or ""
            if key == "seven_day":
                marker = _pace_suffix(window, fetched_at)
                if marker:
                    suffix = f"{suffix}  {marker}" if suffix else marker
                    suffix_full = f"{suffix_full}  {marker}" if suffix_full else marker
            rows.append((label, float(window["pct"]), suffix, suffix_full))
    for window in last_good.get("scoped") or []:
        pct = float(window["pct"])
        suffix, suffix_full = _reset_parts(window, now)
        suffix, suffix_full = suffix or "", suffix_full or ""
        if pct >= 100:
            suffix = f"{suffix}  (!)" if suffix else "(!)"
            suffix_full = f"{suffix_full}  (!)" if suffix_full else "(!)"
        else:
            marker = _pace_suffix(window, fetched_at)
            if marker:
                suffix = f"{suffix}  {marker}" if suffix else marker
                suffix_full = f"{suffix_full}  {marker}" if suffix_full else marker
        rows.append((window["name"], pct, suffix, suffix_full))
    return rows


def account_card_text(
    acc: AccountSnapshot,
    width: int,
    *,
    threshold: float | None = None,
    now: float | None = None,
    palette: Palette = Palette.DARK,
) -> Text:
    """The full account card: header line + per-window bar rows."""
    now = now if now is not None else time.time()

    text = Text()
    text.append(f"{acc.number:>2}  ", style=f"bold {palette.foreground}")
    if acc.alias:
        text.append(acc.alias, style=f"bold {palette.accent}")
        text.append(f" ({acc.email})", style=palette.foreground)
    else:
        text.append(acc.email, style=palette.foreground)
    metadata = Text(f"[{acc.display_tag}]", style=palette.muted)
    if acc.is_active:
        metadata.append("   ● active", style=f"bold {palette.active}")
    if acc.disabled:
        metadata.append("   (disabled)", style=palette.muted)
    age = data.format_age(acc.usage.age_s)
    if age:
        metadata.append(f"   {age}", style=palette.muted)
    text.append("  " if text.cell_len + metadata.cell_len + 2 <= width else "\n    ")
    text.append(metadata)

    sentinel = acc.usage.sentinel
    if sentinel is not None:
        text.append("\n    ")
        style = palette.muted if sentinel == USAGE_API_KEY else palette.sev_warn
        marker = "·" if sentinel == USAGE_API_KEY else "⚠"
        text.append(f"{marker} {data.sentinel_label(sentinel)}", style=style)
        if sentinel != USAGE_API_KEY:
            headroom = oauth.account_headroom(acc.usage.last_good)
            left = remaining_pct(100.0 - headroom) if headroom is not None else None
            if left is not None and acc.usage.fetched_at is not None:
                text.append("\n    ")
                age = printer.format_age(int(acc.usage.fetched_at * 1000))
                text.append(f"└ last seen {left:.0f}% left · {age}", style=palette.muted)
        return text

    rows = usage_rows(acc.usage.last_good, now, acc.usage.fetched_at)
    if not rows:
        text.append("\n    ")
        text.append("usage unavailable", style=palette.muted)
        if acc.usage.last_error:
            # Same wording as the CLI detail line: error KINDS with a
            # friendly note render it, so both surfaces describe the state
            # identically.
            note = ERROR_NOTES.get(acc.usage.last_error, acc.usage.last_error)
            text.append(f" · {note}", style=palette.muted)
        return text

    stale = acc.usage.age_s is not None and acc.usage.age_s > STALE_OK_S
    label_width = max(len(label) for label, _pct, _suffix, _full in rows)
    bar_width = (
        max(4, min(24, width - 17 - label_width))
        if width < 60 else max(12, min(30, width - 47 - label_width))
    )
    row_overhead = 4 + label_width + 1 + bar_width + 10 + 2
    for label, pct, suffix, suffix_full in rows:
        # per-row: show the absolute clock only where it fits, so a long
        # spend row degrading doesn't cost the 5h/7d rows their clocks
        if (
            width >= 60 and suffix_full != suffix
            and row_overhead + len(suffix_full) <= width
        ):
            suffix = suffix_full
        text.append("\n    ")
        text.append(
            usage_bar(
                f"{label:<{label_width}}",
                pct,
                suffix if row_overhead + len(suffix) <= width else None,
                bar_width,
                stale=stale,
                threshold=threshold,
                palette=palette,
            )
        )
        if suffix and row_overhead + len(suffix) > width:
            text.append(f"\n    {suffix}", style=palette.muted)
    return text


def _wrap_mini_details(text: Text, detail_start: int, width: int | None) -> Text:
    if width is None or text.cell_len <= width:
        return text
    return text[:detail_start] + Text("\n    ") + text[detail_start + 3:]


def mini_account_text(
    acc: AccountSnapshot, now: float, *, palette: Palette = Palette.DARK,
    width: int | None = None,
) -> Text:
    """Compact inactive account summary, wrapping details in narrow terminals.

    ``2  work@acme.dev [personal]   5h 8% left · 7d 37% left`` — severity
    colored; a window at/over 100% brings its reset countdown along, and a
    maxed per-model window shows as ``Fable (!)``. Sentinel states show
    their label instead.
    """
    text = Text()
    text.append(f"{acc.number:>2}  ", style=f"bold {palette.muted}")
    if acc.alias:
        text.append(acc.alias, style=f"bold {palette.accent}")
        text.append(f" ({acc.email})", style=palette.foreground)
    else:
        text.append(acc.email, style=palette.foreground)
    text.append(f"  [{acc.display_tag}]", style=palette.muted)
    if acc.disabled:
        text.append("  (disabled)", style=palette.muted)
    detail_start = len(text)
    text.append("   ")

    sentinel = acc.usage.sentinel
    if sentinel is not None:
        style = palette.muted if sentinel == USAGE_API_KEY else palette.sev_warn
        text.append(data.sentinel_label(sentinel), style=style)
        return _wrap_mini_details(text, detail_start, width)

    last_good = acc.usage.last_good
    fetched_at = acc.usage.fetched_at
    stale = acc.usage.age_s is not None and acc.usage.age_s > STALE_OK_S
    if isinstance(last_good, dict) and "windows" in last_good:
        rows = usage_rows(last_good, now, fetched_at)
        for i, (label, pct, suffix, _full) in enumerate(rows):
            if i:
                text.append(" · ", style=palette.track)
            text.append(f"{label} ", style=palette.muted)
            color = palette.severity(pct)
            left = remaining_pct(pct)
            if left is None:
                text.append("quota unknown", style=palette.muted)
            else:
                text.append(f"{left:3.0f}% left", style=f"{color} dim" if stale else color)
            if pct >= 100 and suffix:
                text.append(f" ({suffix})", style=palette.muted)
        if not rows:
            text.append("usage unknown", style=palette.muted)
        return _wrap_mini_details(text, detail_start, width)
    parts = 0
    for key, label in (("five_hour", "5h"), ("seven_day", "7d")):
        window = last_good.get(key) if isinstance(last_good, dict) else None
        if not window:
            continue
        pct = float(window["pct"])
        if parts:
            text.append(" · ", style=palette.track)
        color = palette.severity(pct)
        text.append(f"{label} ", style=palette.muted)
        left = remaining_pct(pct)
        if left is None:
            text.append("quota unknown", style=palette.muted)
        else:
            text.append(f"{left:.0f}% left", style=f"{color} dim" if stale else color)
        if pct >= 100:
            reset = data.reset_text(window, now)
            if reset:
                text.append(f" ({reset})", style=palette.muted)
        elif key == "seven_day":
            result = pace.compute_pace(window, fetched_at=fetched_at)
            if result and result.ahead:
                text.append(" (ahead)", style=palette.sev_warn)
        parts += 1
    maxed = [
        w["name"]
        for w in (last_good.get("scoped") or [] if isinstance(last_good, dict) else [])
        if float(w["pct"]) >= 100
    ]
    for name in maxed:
        if parts:
            text.append(" · ", style=palette.track)
        text.append(f"{name} (!)", style=palette.sev_crit)
        parts += 1
    if not parts:
        text.append("usage unknown", style=palette.muted)
    return _wrap_mini_details(text, detail_start, width)


class AccountsPanel(Static):
    """Static account overview: the active account full-size, others as
    compact minis (in slot order, expanded in place). The dashboard's — and
    with ``show_minis=False`` the auto screen's — always-visible monitor."""

    def __init__(self, *, source=None, show_minis: bool = True, id: str | None = None) -> None:
        super().__init__(id=id)
        self._source = source
        self._show_minis = show_minis

    @property
    def source(self):
        return self._source if self._source is not None else self.app

    def on_mount(self) -> None:
        self.watch(self.source, "snapshot", lambda _snap: self.refresh(layout=True))
        self.watch(self.source, "threshold_pct", lambda _value: self.refresh())
        self.watch(self.app, "theme", lambda _t: self.refresh(layout=True))

    def render(self) -> Text:
        app: "CswapApp" = self.app  # type: ignore[assignment]
        palette = Palette.from_theme(
            app.current_theme, getattr(self.source, "provider", "claude"),
        )
        snap = self.source.snapshot
        if snap is None:
            status = self.source.refresh_status
            if status.startswith("Could not"):
                return Text(status, style=palette.sev_crit)
            return Text("Connecting to saved accounts…", style=palette.muted)
        if not snap.accounts:
            return Text(
                getattr(self.source, "empty_message", "No managed accounts yet.\n"
                "Use the menu below: Add account — from your current "
                "Claude Code login, or from a setup-token / API key."),
                style=palette.muted,
            )
        now = time.time()
        width = self.content_size.width or 80
        blocks: list[Text] = []
        for acc in snap.accounts:
            if acc.is_active:
                blocks.append(
                    account_card_text(
                        acc, width, threshold=self.source.threshold_pct, now=now,
                        palette=palette,
                    )
                )
            elif self._show_minis:
                blocks.append(mini_account_text(acc, now, palette=palette, width=width))
        if not blocks:
            return Text("no active managed login", style=palette.muted)
        text = Text()
        previous_multiline = False
        for i, block in enumerate(blocks):
            multiline = "\n" in block.plain
            if i:
                # breathe around the expanded active card
                text.append("\n\n" if (multiline or previous_multiline) else "\n")
            text.append(block)
            previous_multiline = multiline
        return text


class AccountCard(Static):
    """One account rendered full-size (used by the switch screen's list)."""

    def __init__(
        self, acc: AccountSnapshot, *, threshold: float | None = None,
        provider: str = "claude",
    ) -> None:
        super().__init__()
        self._acc = acc
        self._threshold = threshold
        self.provider = provider

    def on_mount(self) -> None:
        self.watch(self.app, "theme", lambda _: self.refresh(layout=True))

    def set_account(self, acc: AccountSnapshot) -> None:
        self._acc = acc
        self.refresh(layout=True)

    def render(self) -> Text:
        return account_card_text(
            self._acc, self.size.width or 80, threshold=self._threshold,
            palette=Palette.from_theme(self.app.current_theme, self.provider),
        )


class AccountItem(ListItem):
    """ListView row wrapping an :class:`AccountCard`; remembers its slot."""

    def __init__(self, acc: AccountSnapshot, *, provider: str = "claude") -> None:
        super().__init__(AccountCard(acc, provider=provider))
        self.number = acc.number
        self.email = acc.email
        self.set_class(acc.is_active, "active-account")
        self.set_class(acc.disabled, "disabled-account")

    def set_account(self, acc: AccountSnapshot) -> None:
        self.number = acc.number
        self.email = acc.email
        self.set_class(acc.is_active, "active-account")
        self.set_class(acc.disabled, "disabled-account")
        self.query_one(AccountCard).set_account(acc)


class MenuItem(ListItem):
    """One menu row: a label plus an action id the screen dispatches on."""

    def __init__(
        self, label: str, action_id: str, *, muted: bool = False,
        description: str = "",
    ) -> None:
        item = Static(label, markup=False)
        if muted:
            item.add_class("menu-item-muted")
        children = [item]
        if description:
            children.append(Static(description, classes="menu-description", markup=False))
        super().__init__(*children)
        self.action_id = action_id
