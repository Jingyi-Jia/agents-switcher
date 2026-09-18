"""The local dashboard's HTTP server.

SECURITY, BECAUSE THIS SERVES CREDENTIALS-ADJACENT CONTROLS. The endpoints here
can switch which account a machine is logged in as, so reaching them must be
harder than knowing a port number:

* Loopback by default. Binding a wider interface takes an explicit ``--host``
  and prints a warning, because on a shared login node a wildcard bind offers
  account switching to every other user on the box. The reference implementation
  this borrows its shape from binds 0.0.0.0 with no auth at all; that is the one
  thing here that is deliberately different.
* A fresh token per run, minted by ``secrets`` and never written to disk. It
  travels in the URL that gets opened, so a browser on the machine has it and
  nothing else does. Compared with ``compare_digest`` so a wrong guess cannot be
  narrowed by timing.
* No CORS headers. A page on another origin must not be able to read this one.

Reading usage is slow -- a network round trip per account, sometimes a token
refresh -- so the server is threaded and the state is cached briefly. Without
the cache a page that polls would refresh tokens far more often than anything
needs, and rotation is not free.
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from claude_swap.web.page import PAGE_HTML

_logger = logging.getLogger("claude-swap")

#: How long a collected view is reused. Long enough that a polling page does not
#: drive token refreshes, short enough that a switch is visible almost at once.
STATE_TTL_S = 20.0
DEFAULT_PORT = 8765
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


class DashboardState:
    """Collects both providers' accounts, with a short cache.

    Failures are captured per provider rather than raised: a broken Codex login
    must not blank the Claude half of the page, and vice versa.
    """

    def __init__(self, claude_switcher=None, codex_switcher=None) -> None:
        self._claude = claude_switcher
        self._codex = codex_switcher
        self._lock = threading.Lock()
        self._cached: dict | None = None
        self._cached_at = 0.0

    def invalidate(self) -> None:
        """Drop the cache so the next read reflects a change we just made."""
        with self._lock:
            self._cached = None

    def get(self, *, force: bool = False) -> dict:
        with self._lock:
            fresh = (
                self._cached is not None
                and not force
                and time.time() - self._cached_at < STATE_TTL_S
            )
            if fresh:
                return self._cached
        collected = {
            "claude": self._collect_claude(),
            "codex": self._collect_codex(),
            "generatedAt": time.time(),
        }
        with self._lock:
            self._cached = collected
            self._cached_at = time.time()
        return collected

    # -- providers -------------------------------------------------------

    def _collect_claude(self) -> dict:
        if self._claude is None:
            return {"available": False, "error": "not configured", "accounts": []}
        try:
            from claude_swap.poll_policy import binding_pct

            snapshot = self._claude.accounts_snapshot()
            accounts = []
            for account in snapshot.accounts:
                usage = account.usage
                percent = binding_pct(usage.last_good) if usage else None
                accounts.append({
                    "number": account.number,
                    "email": account.email,
                    "alias": account.alias,
                    "org": account.display_tag,
                    "active": account.is_active,
                    "disabled": account.disabled,
                    "kind": account.kind,
                    "percent": None if percent is None else round(percent),
                    "sentinel": usage.sentinel if usage else None,
                    "switchable": account.switchable,
                })
            return {
                "available": True,
                "activeNumber": snapshot.active_number,
                "accounts": accounts,
            }
        except Exception as e:  # noqa: BLE001 - reported, never fatal
            _logger.warning("Dashboard could not read Claude accounts: %s", e)
            return {"available": False, "error": str(e), "accounts": []}

    def _collect_codex(self) -> dict:
        if self._codex is None:
            return {"available": False, "error": "not configured", "accounts": []}
        try:
            managed = self._codex.list_accounts()
            active = self._codex.store.active_number()
            usage = self._codex.usage_all() if managed else {}
            accounts = []
            for account in managed:
                result = usage.get(account.number)
                entry = {
                    "number": account.number,
                    "email": account.email,
                    "alias": account.alias,
                    "plan": account.plan,
                    "active": account.number == active,
                    "disabled": account.disabled,
                }
                if isinstance(result, Exception):
                    entry["error"] = str(result)
                elif result is not None:
                    entry.update({
                        "percent": result.binding_percent,
                        "window": (
                            result.binding_window.label if result.binding_window else ""
                        ),
                        "summary": result.summary,
                        "usable": result.usable,
                        "onCredits": result.on_credits,
                        # The page greys these out: usable by hand, never by the
                        # auto-switcher, because they cost money per request.
                        "autoSwitchEligible": result.auto_switch_eligible,
                        "resetAfterSeconds": (
                            result.binding_window.reset_after_seconds
                            if result.binding_window else None
                        ),
                    })
                accounts.append(entry)
            return {"available": True, "activeNumber": active, "accounts": accounts}
        except Exception as e:  # noqa: BLE001 - reported, never fatal
            _logger.warning("Dashboard could not read Codex accounts: %s", e)
            return {"available": False, "error": str(e), "accounts": []}

    # -- actions ---------------------------------------------------------

    def switch(self, provider: str, number: str) -> dict:
        """Switch one provider, returning what the user still has to do."""
        if provider == "codex":
            if self._codex is None:
                raise ValueError("Codex is not configured")
            result = self._codex.switch_to(number)
            message = f"Switched to {result.account.display_label}."
            if result.restart_required:
                message += (
                    f" Restart Codex — {len(result.processes)} process(es) still "
                    "hold the previous account."
                )
            self.invalidate()
            return {"ok": True, "message": message,
                    "restartRequired": result.restart_required}
        if provider == "claude":
            if self._claude is None:
                raise ValueError("Claude is not configured")
            self._claude.switch_to(number)
            self.invalidate()
            return {"ok": True, "message": f"Switched to account {number}.",
                    "restartRequired": False}
        raise ValueError(f"unknown provider '{provider}'")


class _Server(ThreadingHTTPServer):
    """A ThreadingHTTPServer that does not do a reverse-DNS lookup to start.

    ``HTTPServer.server_bind`` calls ``socket.getfqdn()`` purely to populate
    ``server_name``, which nothing here reads. On a network whose reverse DNS is
    slow or absent -- a corporate VPN, a cluster login node -- that lookup blocks
    until it times out: measured at 35 SECONDS on one ordinary machine, during
    which the command appears frozen and has not yet printed the URL the user
    needs. Skipping it makes startup instant everywhere.
    """

    def server_bind(self):
        from socketserver import TCPServer

        TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


def _make_handler(state: DashboardState, token: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "agent-switch"

        def log_message(self, fmt, *args):  # noqa: A003 - stdlib hook
            _logger.debug("dashboard %s", fmt % args)

        # -- helpers ----------------------------------------------------

        def _authorized(self, query: dict) -> bool:
            supplied = (
                self.headers.get("X-Auth-Token")
                or (query.get("token") or [""])[0]
            )
            # compare_digest so a wrong token cannot be narrowed by timing.
            return bool(supplied) and secrets.compare_digest(supplied, token)

        def _send(self, status, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            # No CORS: another origin must not be able to read this page.
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status, payload: dict) -> None:
            self._send(status, json.dumps(payload).encode("utf-8"), "application/json")

        # -- routes -----------------------------------------------------

        def do_GET(self) -> None:  # noqa: N802 - stdlib hook
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            if not self._authorized(query):
                self._json(HTTPStatus.FORBIDDEN, {"error": "bad or missing token"})
                return
            if parsed.path in ("/", "/index.html"):
                self._send(HTTPStatus.OK, PAGE_HTML.encode("utf-8"), "text/html; charset=utf-8")
            elif parsed.path == "/api/state":
                force = (query.get("force") or ["0"])[0] == "1"
                self._json(HTTPStatus.OK, state.get(force=force))
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802 - stdlib hook
            parsed = urlparse(self.path)
            if not self._authorized(parse_qs(parsed.query)):
                self._json(HTTPStatus.FORBIDDEN, {"error": "bad or missing token"})
                return
            if parsed.path != "/api/switch":
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length) or b"{}")
                result = state.switch(
                    str(payload.get("provider", "")), str(payload.get("number", ""))
                )
            except Exception as e:  # noqa: BLE001 - surfaced to the page
                self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "message": str(e)})
                return
            self._json(HTTPStatus.OK, result)

    return Handler


def serve(
    state: DashboardState,
    *,
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
    token: str | None = None,
) -> tuple[ThreadingHTTPServer, str]:
    """Start the dashboard. Returns the server and the URL to open.

    Threaded because a single usage collection can take seconds; on a
    single-threaded server that would stall the page it is being rendered for.
    """
    token = token or secrets.token_urlsafe(24)
    server = _Server((host, port), _make_handler(state, token))
    server.daemon_threads = True
    shown_host = "127.0.0.1" if host in ("", "0.0.0.0", "::") else host
    url = f"http://{shown_host}:{server.server_port}/?token={token}"
    return server, url
