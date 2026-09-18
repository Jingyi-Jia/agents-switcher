"""``agent-switch web`` — start the local dashboard."""

from __future__ import annotations

import argparse
import sys
import webbrowser

from claude_swap.printer import accent, dimmed, error, muted, yellowed
from claude_swap.web.server import DEFAULT_PORT, LOOPBACK_HOSTS, DashboardState, serve


def _build_state() -> DashboardState:
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
    return DashboardState(claude_switcher=claude, codex_switcher=codex)


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
    args = parser.parse_args(argv)

    state = _build_state()
    try:
        server, url = serve(state, host=args.host, port=args.port)
    except OSError as e:
        error(f"Could not start the dashboard on {args.host}:{args.port} — {e}")
        error("Another instance may already be running; try --port.")
        sys.exit(1)

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
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 - headless boxes have no browser
            print(muted("  (could not open a browser — copy the URL above)"))

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"\n{dimmed('Dashboard stopped')}")
    finally:
        server.shutdown()
        server.server_close()
