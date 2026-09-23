"""A menu-bar / system-tray shim for both providers.

WHAT IT IS. A small always-visible readout of how much quota is left, and a
click-through to the dashboard. It runs the same ``DashboardState`` the web UI
does and never re-implements account or usage logic.

WHY TWO BACKENDS RATHER THAN ONE LIBRARY. pystray works everywhere, but its
macOS backend renders ``title`` through ``setToolTip_`` -- a tooltip, not
visible menu-bar text. On macOS the whole value of this is a number you can read
without clicking anything, so a tooltip-only readout is not the feature. ``rumps``
does render menu-bar text and is already an optional extra here, so macOS uses
it and everything else uses pystray, whose tray+tooltip idiom is what Windows
and most Linux desktops actually offer anyway.

THE DISPLAY LOGIC IS PURE AND SHARED. ``build_model`` turns collected state into
exactly what should be shown, with no toolkit involved, so both backends render
the same thing and the interesting behaviour is tested on every platform --
including the ones whose toolkit cannot be installed in CI.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field

#: Below this much headroom the readout is worth noticing.
WARN_BELOW_PCT = 20.0


@dataclass(frozen=True)
class TrayEntry:
    """One line in the menu: an account, and what clicking it does."""

    label: str
    provider: str = ""
    number: str = ""
    enabled: bool = True
    checked: bool = False

    @property
    def is_action(self) -> bool:
        return bool(self.provider and self.number)


@dataclass(frozen=True)
class TrayModel:
    """Everything a backend needs to draw, with no toolkit types in it."""

    title: str
    tooltip: str
    entries: tuple[TrayEntry, ...] = ()
    attention: bool = False


def _headroom(account: dict) -> float | None:
    """Percentage points left on the account's worst window."""
    windows = account.get("windows") or []
    values = [
        100.0 - float(w["usedPercent"])
        for w in windows
        if isinstance(w.get("usedPercent"), (int, float))
    ]
    if values:
        return max(0.0, min(values))
    percent = account.get("percent")
    return None if percent is None else max(0.0, 100.0 - float(percent))


def _active(provider_state: dict) -> dict | None:
    for account in provider_state.get("accounts") or []:
        if account.get("active"):
            return account
    return None


def _short(text: str, limit: int = 18) -> str:
    """Trim an email for a menu line without losing which account it is."""
    if len(text) <= limit:
        return text
    local, _, domain = text.partition("@")
    if domain and len(local) + 1 < limit:
        return f"{local}@{domain[: max(1, limit - len(local) - 2)]}…"
    return text[: limit - 1] + "…"


def _provider_summary(name: str, state: dict) -> tuple[str, bool]:
    """``('C 66%', False)`` — the compact readout for one provider.

    Returns ``attention`` separately because a provider can be fine, low, or
    not-set-up, and the caller decides how loudly to say so.
    """
    if not state.get("available"):
        return f"{name} —", False
    account = _active(state)
    if account is None:
        return f"{name} —", False
    if account.get("onCredits"):
        # It works, but it is not quota. Saying "0%" alone would read as broken
        # and saying "100%" would read as healthy; neither is the truth.
        return f"{name} $", True
    left = _headroom(account)
    if left is None:
        return f"{name} ?", False
    return f"{name} {left:.0f}%", left <= WARN_BELOW_PCT


def build_model(state: dict) -> TrayModel:
    """Turn collected dashboard state into what the tray should display.

    Pure: no toolkit, no I/O, no clock. Both backends render this, so what is
    tested here is what is shown everywhere.
    """
    claude = state.get("claude") or {}
    codex = state.get("codex") or {}

    claude_text, claude_warn = _provider_summary("C", claude)
    codex_text, codex_warn = _provider_summary("X", codex)
    title = f"{claude_text}  {codex_text}"

    entries: list[TrayEntry] = []
    for label, key, provider_state in (
        ("Claude Code", "claude", claude), ("Codex", "codex", codex)
    ):
        entries.append(TrayEntry(label=label, enabled=False))
        accounts = provider_state.get("accounts") or []
        if not provider_state.get("available"):
            entries.append(TrayEntry(label="   unavailable", enabled=False))
            continue
        if not accounts:
            live = provider_state.get("liveLogin")
            note = (
                f"   {_short(live['email'])} — not managed"
                if live else "   no accounts"
            )
            entries.append(TrayEntry(label=note, enabled=False))
            continue
        for account in accounts:
            entries.append(_account_entry(key, account))
    return TrayModel(
        title=title,
        tooltip="agent-switch — click for the dashboard",
        entries=tuple(entries),
        attention=claude_warn or codex_warn,
    )


def _account_entry(provider: str, account: dict) -> TrayEntry:
    name = _short(account.get("email") or f"account {account.get('number')}")
    if account.get("error"):
        detail = "error"
    elif account.get("onCredits"):
        detail = "on credits"
    else:
        left = _headroom(account)
        detail = "?" if left is None else f"{left:.0f}% left"
    return TrayEntry(
        label=f"   {name} · {detail}",
        provider=provider,
        number=str(account.get("number") or ""),
        # Clicking the account you are already on should do nothing, and a
        # disabled account is still a valid explicit target.
        enabled=not account.get("active"),
        checked=bool(account.get("active")),
    )


def available_backend(platform: str | None = None) -> str | None:
    """Which backend can actually run here: ``'rumps'``, ``'pystray'`` or None."""
    platform = platform or sys.platform
    if platform == "darwin":
        try:
            import rumps  # noqa: F401
        except ImportError:
            return _pystray_or_none()
        return "rumps"
    return _pystray_or_none()


def _pystray_or_none() -> str | None:
    try:
        import pystray  # noqa: F401
    except ImportError:
        return None
    return "pystray"


def install_hint(platform: str | None = None) -> str:
    """What to install when no backend is available."""
    platform = platform or sys.platform
    extra = "tray-macos" if platform == "darwin" else "tray"
    return f"pip install 'agents-switcher[{extra}]'"


# -- backends -----------------------------------------------------------
#
# Both are thin: they translate a TrayModel into toolkit objects and route
# clicks back. Anything worth testing lives in build_model above.

def _icon_image(attention: bool):
    """A small dot for the tray. Generated rather than shipped as an asset so
    there is no binary in the package and no path to resolve at runtime."""
    from PIL import Image, ImageDraw

    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    fill = (214, 139, 32, 255) if attention else (60, 140, 92, 255)
    draw.ellipse((8, 8, size - 8, size - 8), fill=fill)
    return image


def _run_pystray(refresh, on_select, on_open, interval: float) -> None:
    """Tray icon for Windows and Linux (and macOS without rumps)."""
    import threading

    import pystray

    state = {"model": refresh()}

    def build_menu():
        items = [
            pystray.MenuItem("Open dashboard", lambda *_: on_open(), default=True),
            pystray.Menu.SEPARATOR,
        ]
        for entry in state["model"].entries:
            if entry.is_action and entry.enabled:
                items.append(pystray.MenuItem(
                    entry.label,
                    (lambda e: lambda *_: on_select(e.provider, e.number))(entry),
                ))
            else:
                items.append(pystray.MenuItem(entry.label, None, enabled=False))
        items += [pystray.Menu.SEPARATOR,
                  pystray.MenuItem("Quit", lambda icon, *_: icon.stop())]
        return pystray.Menu(*items)

    icon = pystray.Icon(
        "agent-switch",
        _icon_image(state["model"].attention),
        state["model"].title,
        menu=build_menu(),
    )
    stopped = threading.Event()

    def poll():
        while not stopped.wait(interval):
            try:
                state["model"] = refresh()
                icon.icon = _icon_image(state["model"].attention)
                icon.title = state["model"].title
                icon.menu = build_menu()
                icon.update_menu()
            except Exception:  # noqa: BLE001 - a refresh failure must not kill the tray
                continue

    worker = threading.Thread(target=poll, daemon=True)
    worker.start()
    try:
        icon.run()
    finally:
        stopped.set()
        worker.join()


def _run_rumps(refresh, on_select, on_open, interval: float, on_quit) -> None:
    """macOS menu bar, where the title is VISIBLE text rather than a tooltip."""
    import rumps

    class TrayApp(rumps.App):
        def __init__(self):
            model = refresh()
            super().__init__(model.title, quit_button=None)
            self._render(model)
            self._timer = rumps.Timer(self._tick, interval)
            self._timer.start()

        def _render(self, model):
            self.title = model.title
            self.menu.clear()
            entries = [rumps.MenuItem("Open dashboard", callback=lambda _: on_open()),
                       rumps.separator]
            for entry in model.entries:
                if entry.is_action and entry.enabled:
                    item = rumps.MenuItem(
                        entry.label,
                        callback=(lambda e: lambda _: on_select(e.provider, e.number))(entry),
                    )
                else:
                    item = rumps.MenuItem(entry.label)  # no callback = greyed out
                item.state = 1 if entry.checked else 0
                entries.append(item)
            entries += [rumps.separator,
                        rumps.MenuItem("Quit", callback=self._quit)]
            self.menu = entries

        def _quit(self, _):
            self._timer.stop()
            on_quit()
            rumps.quit_application()

        def _tick(self, _):
            try:
                self._render(refresh())
            except Exception:  # noqa: BLE001 - a refresh failure must not kill the tray
                pass

    app = TrayApp()
    try:
        app.run()
    finally:
        app._timer.stop()


def run(refresh, on_select, on_open, *, interval: float = 30.0,
        backend: str | None = None, on_quit=None) -> None:
    """Run the tray until quit.

    Args:
        refresh: Returns a fresh :class:`TrayModel`.
        on_select: ``(provider, number)`` when an account is chosen.
        on_open: Called to open the dashboard.
        on_quit: Stop owned resources before the tray process exits.

    Raises:
        RuntimeError: No usable backend is installed.
    """
    backend = backend or available_backend()
    closed = False

    def cleanup():
        nonlocal closed
        if not closed:
            closed = True
            if on_quit is not None:
                on_quit()

    try:
        if backend == "rumps":
            _run_rumps(refresh, on_select, on_open, interval, cleanup)
        elif backend == "pystray":
            _run_pystray(refresh, on_select, on_open, interval)
        else:
            raise RuntimeError(
                f"no tray backend available — install one with: {install_hint()}"
            )
    finally:
        cleanup()
