"""``agent-switch web`` — start the local dashboard."""

from __future__ import annotations

import argparse
import signal
import sys
import webbrowser

from claude_swap.printer import accent, dimmed, error, muted, yellowed
from claude_swap.web.server import (
    DEFAULT_PORT,
    LOOPBACK_HOSTS,
    DashboardState,
    clear_url,
    live_dashboard_url,
    publish_url,
    serve,
)


def _open(url: str) -> None:
    """Open a browser, tolerating machines that have none."""
    try:
        if not webbrowser.open(url):
            raise RuntimeError("no browser")
    except Exception:  # noqa: BLE001 - headless boxes have no browser
        print(muted("  (could not open a browser — copy the URL above)"), flush=True)


def _build_state(*, state_class=DashboardState) -> DashboardState:
    """Compose both providers, tolerating either being unusable.

    Each is constructed independently so a machine with only one of the two set
    up still gets a working dashboard for the half it has.
    """
    claude = codex = None
    try:
        from claude_swap.switcher import ClaudeAccountSwitcher

        claude = ClaudeAccountSwitcher()
    except Exception:  # noqa: BLE001 - reported in the page, not fatal here
        claude = None
    try:
        from claude_swap.codex.switcher import CodexSwitcher

        codex = CodexSwitcher()
    except Exception:  # noqa: BLE001
        codex = None
    return state_class(claude_switcher=claude, codex_switcher=codex)


def web_command(argv: list[str]) -> None:
    """Handle ``<prog> web``."""
    from claude_swap.cli import _prog_name

    parser = argparse.ArgumentParser(
        prog=f"{_prog_name()} web",
        description="Open a local dashboard for Claude Code and Codex accounts.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
The dashboard binds loopback and mints a fresh token each run, so only a browser
on this machine can reach it. To use it from a cluster login node, forward the
port rather than widening the bind:

  ssh -L 8765:127.0.0.1:8765 mercury
  # then on mercury:
  {_prog_name()} web --no-open
        """,
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"Port to listen on (default {DEFAULT_PORT})")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Interface to bind (default 127.0.0.1 — see below)")
    parser.add_argument("--no-open", action="store_true",
                        help="Print the URL instead of opening a browser")
    parser.add_argument("--no-reuse", action="store_true",
                        help="Always start a new server, even if one is running")
    args = parser.parse_args(argv)

    # A clickable icon gets double-clicked. Joining the dashboard that is
    # already up is the right answer to that; "port in use" is not.
    existing = live_dashboard_url()
    if existing and not args.no_reuse:
        print(f"{accent('Dashboard already running')} {existing}", flush=True)
        if not args.no_open:
            _open(existing)
        return

    state = _build_state()
    try:
        server, url = serve(state, host=args.host, port=args.port)
    except OSError as e:
        error(f"Could not start the dashboard on {args.host}:{args.port} — {e}")
        error("Another instance may already be running; try --port.")
        sys.exit(1)
    publish_url(url)

    if args.host not in LOOPBACK_HOSTS:
        # Worth shouting about: these endpoints switch which account the machine
        # is logged in as, so a wider bind offers that to everyone who can reach
        # the port. The token is the only thing left between them and it.
        print(yellowed(
            f"  Warning: listening on {args.host}, not just this machine."
        ), flush=True)
        print(dimmed(
            "  Anyone who can reach this port and guess the token can switch "
            "your accounts.\n"
            "  Prefer an SSH tunnel: ssh -L "
            f"{args.port}:127.0.0.1:{args.port} <host>"
        ))

    # flush: the URL carries the token, and it is the ONE thing the user needs.
    # Python buffers stdout when it is not a terminal, so under nohup, a pipe, or
    # a captured log it would not appear until the process ended.
    print(f"{accent('Dashboard')} {url}", flush=True)
    print(dimmed("  Ctrl-C to stop."), flush=True)
    if not args.no_open:
        _open(url)

    # SIGTERM does not run `finally`, so quitting the app from the Dock or a
    # plain `kill` would leave the URL record behind. A stale record is already
    # self-healing (the liveness probe clears one that does not answer), but
    # tidying up on the ordinary path costs four lines.
    def _stop(signum, frame):  # noqa: ARG001 - signal handler signature
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGTERM, _stop)
    except (ValueError, AttributeError):
        pass  # not the main thread, or no SIGTERM on this platform

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"\n{dimmed('Dashboard stopped')}")
    finally:
        clear_url()
        server.shutdown()
        server.server_close()


def app_command(argv: list[str]) -> None:
    """Handle ``<prog> app`` — install or remove the desktop launcher."""
    from claude_swap.cli import _prog_name
    from claude_swap.web import launcher

    prog = _prog_name()
    parser = argparse.ArgumentParser(
        prog=f"{prog} app",
        description="Create a clickable launcher for the dashboard.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
The launcher only runs `{prog} web`; it is not a separate application. The
terminal commands are unaffected, so a machine with no desktop -- a cluster
login node, a container -- keeps using `{prog} web --no-open` over an SSH
tunnel exactly as before, and simply never installs one.
        """,
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("install", help="Create the launcher (replaces any existing one)")
    sub.add_parser("uninstall", help="Remove the launcher")
    sub.add_parser("status", help="Show whether a launcher is installed (the default)")
    args = parser.parse_args(argv)
    command = args.command or "status"

    target = launcher.plan()
    if not target.supported:
        error(f"Cannot install a launcher: {target.reason}")
        print(dimmed(f"  Use '{prog} web --no-open' and open the URL yourself."))
        sys.exit(1)

    location = target.paths[0]
    if command == "status":
        if launcher.is_installed():
            print(f"{accent('Installed')} {location}")
        else:
            print(dimmed(f"Not installed. '{prog} app install' creates "
                         f"{target.description}."))
        return

    if command == "install":
        try:
            launcher.install()
        except OSError as e:
            error(f"Could not create the launcher: {e}")
            sys.exit(1)
        print(f"{accent('Installed')} {location}")
        print(dimmed(f"  {target.description[:1].upper()}{target.description[1:]}."))
        print(dimmed(f"  It runs '{launcher.executable_path()} web'."))
        return

    removed = launcher.uninstall()
    if removed:
        for path in removed:
            print(f"{accent('Removed')} {path}")
    else:
        print(dimmed("No launcher was installed."))


def tray_command(argv: list[str]) -> None:
    """Handle ``<prog> tray`` — a menu-bar / system-tray readout."""
    import threading

    from claude_swap.cli import _prog_name
    from claude_swap.web import tray

    prog = _prog_name()
    parser = argparse.ArgumentParser(
        prog=f"{prog} tray",
        description="Show quota in the menu bar / system tray.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Runs until you quit it from the menu. It also hosts the dashboard, so "Open
dashboard" is instant. Needs a desktop session -- on a headless machine use
`{prog} web --no-open` over an SSH tunnel instead.
        """,
    )
    parser.add_argument("--interval", type=float, default=30.0, metavar="SECONDS",
                        help="How often to refresh quota (default 30)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"Port for the dashboard it hosts (default {DEFAULT_PORT})")
    args = parser.parse_args(argv)

    backend = tray.available_backend()
    if backend is None:
        error("No tray backend is installed.")
        print(dimmed(f"  {tray.install_hint()}"))
        sys.exit(1)
    if sys.platform == "darwin" and backend != "rumps":
        # Not fatal, but the number would only appear on hover, which is the
        # opposite of the point.
        print(yellowed("  rumps is not installed — the readout will be a tooltip "
                       "rather than visible menu-bar text."))
        print(dimmed(f"  {tray.install_hint()}"), flush=True)

    state = _build_state()

    # The tray hosts the dashboard itself rather than shelling out, so opening
    # it is instant and there is one process to quit rather than two.
    url_holder: dict[str, str] = {}
    server = None
    server_thread = None
    existing = live_dashboard_url()
    if existing:
        url_holder["url"] = existing
    else:
        for port in (args.port, 0):
            # Port 0 as a fallback: reuse depends on the URL record, so a server
            # whose record was lost (killed with SIGKILL, or the file removed)
            # holds the port invisibly. Giving up there would cost the dashboard
            # for no reason when any free port would do.
            try:
                server, url = serve(state, host="127.0.0.1", port=port)
            except OSError as e:
                if port == 0:
                    print(yellowed(f"  Dashboard could not start ({e}); the tray "
                                   "will still show quota."), flush=True)
                continue
            publish_url(url)
            url_holder["url"] = url
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            break

    def refresh():
        return tray.build_model(state.get())

    def on_select(provider: str, number: str) -> None:
        try:
            state.switch(provider, number)
        except Exception as e:  # noqa: BLE001 - a bad switch must not kill the tray
            print(yellowed(f"  switch failed: {e}"), flush=True)

    def on_open() -> None:
        if url_holder.get("url"):
            _open(url_holder["url"])

    closed = False

    def on_quit() -> None:
        nonlocal closed
        if closed:
            return
        closed = True
        try:
            if server is not None:
                server.shutdown()
                server.server_close()
                server_thread.join()
            else:
                state.close()
        finally:
            if server is not None:
                clear_url()

    print(f"{accent('Tray running')} ({backend}). Quit from the menu.", flush=True)
    try:
        tray.run(
            refresh, on_select, on_open, interval=args.interval,
            backend=backend, on_quit=on_quit,
        )
    except RuntimeError as e:
        error(str(e))
        sys.exit(1)
    finally:
        on_quit()
