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

import base64
import hashlib
import json
import logging
import re
import secrets
import select
import socket
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from claude_swap.claude_desktop import ClaudeDesktopProfiles
from claude_swap.providers import ActionRequired, ProviderActionError, ProviderActions, account_number, safe_error
from claude_swap.web.page import PAGE_HTML
from claude_swap.web.preferences import UiPreferences

_logger = logging.getLogger("claude-swap")

#: How long a collected view is reused. Long enough that a polling page does not
#: drive token refreshes, short enough that a switch is visible almost at once.
STATE_TTL_S = 20.0
DEFAULT_PORT = 8765
#: Where a running dashboard records its URL so a second launch can join it
#: rather than fail on a busy port. Carries the token, so it is written 0600 and
#: removed on exit -- a clickable icon gets double-clicked, and "port in use" is
#: the wrong answer to that.
URL_FILENAME = "web-url"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
MAX_BODY_BYTES = 32768
REJECTION_DRAIN_TIMEOUT_S = 0.25


def _quota_window(label, percent, window_seconds, reset_at, reset_after, observed_at) -> dict:
    def timestamp(value):
        return value if type(value) in (int, float) and 0 < value < 8_640_000_000_000 else None

    observed = timestamp(observed_at)
    seconds = timestamp(window_seconds)
    reset = timestamp(reset_at)
    remaining = reset_after if type(reset_after) in (int, float) and 0 <= reset_after < 8_640_000_000_000 else None
    if reset_at is None and observed is not None and remaining is not None:
        reset = timestamp(observed + remaining)
    if reset is not None and observed is not None and seconds is not None and reset - observed > seconds:
        reset = remaining = None
    if reset is not None:
        remaining = max(0, int(reset - time.time()))
    return {
        "label": label, "usedPercent": percent,
        "windowSeconds": seconds, "resetAt": reset,
        "resetAfterSeconds": remaining, "observedAt": observed,
    }


def _claude_windows(last_good: dict | None, observed_at: float | None = None) -> list[dict]:
    """Claude's windows in the same shape the Codex ones use.

    One shape for both providers so the page has a single way to draw a limit;
    the alternative is two renderers that drift. Claude names its windows in the
    payload (``five_hour``/``seven_day``) while Codex only gives a duration, so
    the label is normalised here rather than in the page.
    """
    from datetime import datetime

    from claude_swap.tui.data import window_pct

    if not isinstance(last_good, dict):
        return []
    windows = []
    for key, label, seconds in (("five_hour", "5h", 18000), ("seven_day", "7d", 604800)):
        percent = window_pct(last_good, key)
        if percent is None:
            continue
        stamp = None
        block = last_good.get(key)
        resets_at = block.get("resets_at") if isinstance(block, dict) else None
        if isinstance(resets_at, str) and resets_at:
            try:
                parsed = datetime.fromisoformat(resets_at.replace("Z", "+00:00"))
                if parsed.tzinfo is not None and parsed.utcoffset() is not None:
                    stamp = parsed.timestamp()
            except (ValueError, OSError, OverflowError):
                pass
        windows.append(_quota_window(label, round(percent), seconds, stamp, None, observed_at))
    return windows


class DashboardState:
    """Collects both providers' accounts, with a short cache.

    Failures are captured per provider rather than raised: a broken Codex login
    must not blank the Claude half of the page, and vice versa.
    """

    def __init__(self, claude_switcher=None, codex_switcher=None) -> None:
        from claude_swap.analytics import UsageAnalytics
        from claude_swap.codex.desktop import CodexDesktop

        self._claude = claude_switcher
        self._codex = codex_switcher
        self.claude_desktop = ClaudeDesktopProfiles()
        self.codex_desktop = CodexDesktop()
        self.analytics = UsageAnalytics(codex_switcher)
        self.preferences = UiPreferences()
        self.actions = ProviderActions(
            claude=claude_switcher, codex=codex_switcher, codex_preflight=self._require_codex_quit,
        )
        self._lock = self.actions.lock
        self._cached: dict | None = None
        self._cached_at = 0.0
        self._cached_revision = -1

    def invalidate(self) -> None:
        """Drop the cache so the next read reflects a change we just made."""
        with self._lock:
            self._cached = None

    def get(self, *, force: bool = False) -> dict:
        with self._lock:
            fresh = (
                self._cached is not None
                and not force
                and self._cached_revision == self.actions.revision
                and time.time() - self._cached_at < STATE_TTL_S
            )
            if not fresh:
                self._cached = {
                    "claude": self._collect_claude(),
                    "codex": self._collect_codex(),
                    "generatedAt": time.time(),
                }
                self._cached_at = time.time()
                self._cached_revision = self.actions.revision
            return {
                **self._cached,
                "claudeDesktop": self.claude_desktop.status(),
                "preferences": self.preferences.get(),
                **{
                    provider: {
                        **self._cached[provider],
                        "capabilities": self.actions.capabilities(provider),
                        "switchNotice": self.actions.switch_notice(provider),
                        "auto": self.actions.auto.status(provider),
                    }
                    for provider in ("claude", "codex")
                },
            }

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
                    "windows": _claude_windows(usage.last_good if usage else None, usage.fetched_at if usage else None),
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
                # The machine can be LOGGED IN to an account this tool does not
                # manage. Reporting only managed accounts made a machine with a
                # live login read as "no accounts", which is true and useless --
                # the answer the user wants is "yes, as whom, and shall I manage
                # it?"
                "liveLogin": self._claude_live_login(),
            }
        except Exception as e:  # noqa: BLE001 - reported, never fatal
            _logger.warning("Dashboard could not read Claude accounts")
            return {"available": False, "error": safe_error(e, include_detail=True), "accounts": []}

    def _claude_live_login(self) -> dict | None:
        """Who Claude Code is currently signed in as, managed or not."""
        try:
            payload = self._claude.status(json_output=True)
        except Exception:  # noqa: BLE001 - a label, never fatal
            return None
        active = (payload or {}).get("active") if isinstance(payload, dict) else None
        if not isinstance(active, dict) or not active.get("email"):
            return None
        return {"email": active["email"], "managed": bool(active.get("managed"))}

    def _collect_codex(self) -> dict:
        if self._codex is None:
            return {"available": False, "error": "not configured", "accounts": []}
        try:
            managed = self._codex.list_accounts()
            status = self._codex.status()
            active = status.active_number
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
                    entry["error"] = safe_error(result, include_detail=True)
                elif result is not None:
                    entry.update({
                        "windows": [
                            _quota_window(w.label, w.used_percent, w.window_seconds,
                                          w.reset_at, w.reset_after_seconds, result.fetched_at)
                            for w in result.windows
                        ],
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
            live = None
            if status.logged_in and status.identity:
                live = {
                    "email": status.identity.email,
                    "plan": status.identity.plan,
                    "managed": status.is_managed,
                }
            return {"available": True, "activeNumber": active,
                    "accounts": accounts, "liveLogin": live}
        except Exception as e:  # noqa: BLE001 - reported, never fatal
            _logger.warning("Dashboard could not read Codex accounts")
            return {"available": False, "error": safe_error(e, include_detail=True), "accounts": []}

    # -- actions ---------------------------------------------------------

    def add_current(self, provider: str) -> dict:
        """Manage whichever account the machine is currently signed in as.

        ``assume_yes`` on the Claude side, and an auto-assigned slot on both:
        an HTTP request must never block on a terminal prompt nobody can see.
        """
        try:
            return self.actions.add_current(provider)
        finally:
            self.invalidate()

    def switch(self, provider: str, number: str) -> dict:
        """Switch one provider, returning what the user still has to do."""
        try:
            with self._lock:
                if provider == "codex":
                    number = account_number(number)
                    self._require_codex_quit()
                return self.actions.switch(provider, number)
        finally:
            self.invalidate()

    def _require_codex_quit(self) -> None:
        status = self.codex_desktop.status()
        if status.get("available") is not True or type(status.get("running")) is not bool:
            raise ActionRequired(
                "Could not confirm that Codex is closed. Check its status again; your login has not changed.",
                code="codex-status-unknown",
            )
        if status.get("running") is not False:
            raise ActionRequired(
                "Close Codex before switching, including its app and terminal sessions. Your login has not changed.",
                code="codex-running",
            )

    def quit_codex(self, *, confirm=False) -> dict:
        with self._lock:
            return self.codex_desktop.quit(confirm=confirm)

    def open_codex(self, *, confirm=False) -> dict:
        with self._lock:
            return self.codex_desktop.open(confirm=confirm)

    def set_disabled(self, provider: str, number, disabled: bool) -> dict:
        try:
            return self.actions.set_disabled(provider, number, disabled)
        finally:
            self.invalidate()

    def remove(self, provider: str, number, *, confirm=False) -> dict:
        try:
            return self.actions.remove(provider, number, confirm=confirm)
        finally:
            self.invalidate()

    def switch_best(self, provider: str) -> dict:
        try:
            with self._lock:
                if provider == "codex":
                    self._require_codex_quit()
                return self.actions.switch_best(provider)
        finally:
            self.invalidate()

    def add_token(self, provider: str, token: str, email=None, slot=None, *, confirm=False) -> dict:
        try:
            return self.actions.add_token(provider, token, email, slot, confirm=confirm)
        finally:
            self.invalidate()

    def configure_auto(self, provider: str, mode: str, *, threshold=None, confirm=False) -> dict:
        try:
            return self.actions.auto.configure(provider, mode, threshold=threshold, confirm=confirm)
        finally:
            self.invalidate()

    def close(self) -> None:
        self.actions.close()


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

    def server_close(self):
        try:
            state = getattr(self, "dashboard_state", None)
            if state is not None:
                state.close()
        finally:
            super().server_close()


def _make_handler(state: DashboardState, token: str):
    script_hashes = " ".join(
        "'sha256-" + base64.b64encode(hashlib.sha256(script.encode("utf-8")).digest()).decode("ascii") + "'"
        for script in re.findall(r"<script>(.*?)</script>", PAGE_HTML, re.DOTALL)
    )
    policy = (
        f"default-src 'none'; script-src {script_hashes}; style-src 'unsafe-inline'; "
        "connect-src 'self'; img-src 'self' data:; base-uri 'none'; "
        "frame-ancestors 'none'; form-action 'none'"
    )

    class Handler(BaseHTTPRequestHandler):
        server_version = "agent-switch"

        def log_message(self, fmt, *args):  # noqa: A003 - stdlib hook
            _logger.debug("dashboard request completed")

        # -- helpers ----------------------------------------------------

        def _authorized(self, query: dict) -> bool:
            supplied = (
                self.headers.get("X-Auth-Token")
                or (query.get("token") or [""])[0]
            )
            # compare_digest so a wrong token cannot be narrowed by timing.
            return bool(supplied) and secrets.compare_digest(
                supplied.encode("utf-8"), token.encode("utf-8")
            )

        def _send(self, status, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            # No CORS: another origin must not be able to read this page.
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", policy)
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status, payload: dict) -> None:
            self._send(status, json.dumps(payload).encode("utf-8"), "application/json")

        def _content_length(self) -> int:
            lengths = self.headers.get_all("Content-Length", [])
            if (
                len(lengths) != 1 or not lengths[0].isascii()
                or not lengths[0].isdigit() or len(lengths[0]) > 10
                or self.headers.get("Transfer-Encoding") is not None
            ):
                raise ProviderActionError("Provide one valid Content-Length; transfer encoding is not supported")
            length = int(lengths[0])
            if not 0 < length <= MAX_BODY_BYTES:
                raise ProviderActionError(f"JSON body must contain 1 to {MAX_BODY_BYTES} bytes")
            return length

        def _reject(self, status, payload: dict) -> None:
            self.close_connection = True
            self._json(status, payload)
            self.wfile.flush()
            try:
                self.connection.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            self._reject_transport_deadline = time.monotonic() + REJECTION_DRAIN_TIMEOUT_S
            self._reject_transport_remaining = max(
                0, MAX_BODY_BYTES - getattr(self, "_rejected_body_bytes", 0),
            )
            if self._body_started:
                return
            try:
                remaining = self._content_length()
            except ProviderActionError:
                return
            self._body_started = True
            try:
                while remaining:
                    timeout = self._reject_transport_deadline - time.monotonic()
                    if timeout <= 0:
                        break
                    self.connection.settimeout(timeout)
                    chunk = self.rfile.read1(remaining)
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    self._record_rejected_body_bytes(len(chunk))
            except OSError:
                pass

        def _record_rejected_body_bytes(self, count: int) -> None:
            self._rejected_body_bytes = getattr(self, "_rejected_body_bytes", 0) + count
            if hasattr(self, "_reject_transport_remaining"):
                self._reject_transport_remaining = max(0, self._reject_transport_remaining - count)

        def _finish_rejected_transport(self) -> None:
            deadline = getattr(self, "_reject_transport_deadline", None)
            remaining = getattr(self, "_reject_transport_remaining", 0)
            if deadline is None or remaining <= 0:
                return
            try:
                self.connection.setblocking(False)
                while remaining:
                    timeout = deadline - time.monotonic()
                    if timeout <= 0:
                        break
                    readable, _, _ = select.select([self.connection], [], [], timeout)
                    if not readable:
                        break
                    try:
                        chunk = self.connection.recv(min(remaining, 8192))
                    except BlockingIOError:
                        continue
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    self._reject_transport_remaining = remaining
            except (OSError, ValueError):
                pass

        def finish(self) -> None:
            try:
                super().finish()
            finally:
                self._finish_rejected_transport()

        def _body(self) -> dict:
            length = self._content_length()
            if self.headers.get_content_type() != "application/json":
                raise ProviderActionError("Content-Type must be application/json")
            self.connection.settimeout(10)
            self._body_started = True
            raw = self.rfile.read(length)
            self._record_rejected_body_bytes(len(raw))
            if len(raw) != length:
                raise ProviderActionError("Incomplete JSON body")

            def object_pairs(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ProviderActionError("Duplicate JSON fields are not supported")
                    result[key] = value
                return result

            def invalid_constant(value):
                raise ProviderActionError("JSON numbers must be finite")

            try:
                payload = json.loads(
                    raw.decode("utf-8"), object_pairs_hook=object_pairs,
                    parse_constant=invalid_constant,
                )
            except (ValueError, UnicodeError, RecursionError):
                raise ProviderActionError("Provide a valid JSON object") from None
            if not isinstance(payload, dict):
                raise ProviderActionError("JSON body must be an object")
            return payload

        # -- routes -----------------------------------------------------

        def do_GET(self) -> None:  # noqa: N802 - stdlib hook
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query, keep_blank_values=True)
            private_read = parsed.path in {"/api/analytics", "/api/codex/status", "/api/preferences"}
            if not self._authorized({} if private_read else query):
                self._json(HTTPStatus.FORBIDDEN, {"error": "bad or missing token"})
                return
            if parsed.path in ("/", "/index.html"):
                self._send(HTTPStatus.OK, PAGE_HTML.encode("utf-8"), "text/html; charset=utf-8")
            elif parsed.path == "/api/state":
                force = (query.get("force") or ["0"])[0] == "1"
                self._json(HTTPStatus.OK, state.get(force=force))
            elif private_read:
                try:
                    if parsed.path == "/api/analytics":
                        if (
                            set(query) - {"provider", "force"}
                            or any(len(values) != 1 for values in query.values())
                            or (query.get("provider") or [None])[0] not in {"claude", "codex"}
                            or (query.get("force") or ["0"])[0] not in {"0", "1"}
                        ):
                            raise ProviderActionError("Choose a valid usage provider and refresh option.")
                        result = state.analytics.get(query["provider"][0], force=query.get("force") == ["1"])
                    elif parsed.query:
                        raise ProviderActionError("This request does not accept query parameters.")
                    elif parsed.path == "/api/preferences":
                        result = state.preferences.get()
                    else:
                        result = state.codex_desktop.status()
                    self._json(HTTPStatus.OK, result)
                except Exception as error:
                    self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "message": safe_error(error)})
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802 - stdlib hook
            self._body_started = False
            self._rejected_body_bytes = 0
            parsed = urlparse(self.path)
            if not self._authorized({}):
                self._reject(HTTPStatus.FORBIDDEN, {"error": "bad or missing token"})
                return
            routes = {
                "/api/preferences": (state.preferences.update, set(), {"theme", "profileNoticeVersion", "confirm"}),
                "/api/codex/quit": (state.quit_codex, {"confirm"}, set()),
                "/api/codex/open": (state.open_codex, {"confirm"}, set()),
                "/api/claude-desktop/create": (state.claude_desktop.create, {"name", "confirm"}, {"emailLabel"}),
                "/api/claude-desktop/open": (state.claude_desktop.open, {"profileId", "confirm"}, set()),
                "/api/claude-desktop/update": (state.claude_desktop.update, {"profileId", "name", "emailLabel", "confirm"}, set()),
                "/api/claude-desktop/delete": (state.claude_desktop.delete, {"profileId", "confirm"}, set()),
                "/api/switch": (state.switch, {"provider", "number"}, set()),
                "/api/add": (state.add_current, {"provider"}, set()),
                "/api/remove": (state.remove, {"provider", "number", "confirm"}, set()),
                "/api/disabled": (state.set_disabled, {"provider", "number", "disabled"}, set()),
                "/api/switch-best": (state.switch_best, {"provider"}, set()),
                "/api/token": (state.add_token, {"provider", "token"}, {"email", "slot", "confirm"}),
                "/api/auto": (state.configure_auto, {"provider", "mode"}, {"threshold", "confirm"}),
            }
            if parsed.path not in routes:
                self._reject(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            try:
                payload = self._body()
                action, required, optional = routes[parsed.path]
                if not required <= payload.keys() or payload.keys() - required - optional:
                    raise ProviderActionError("Missing required or unsupported request fields")
                if "threshold" in payload and payload["threshold"] is None:
                    raise ProviderActionError("threshold must be a number")
                result = action(**payload)
            except Exception as e:  # noqa: BLE001 - surfaced to the page
                result = {"ok": False, "message": safe_error(e)}
                if isinstance(e, ActionRequired):
                    result.update({"kind": "action-required", "code": e.code})
                self._reject(HTTPStatus.BAD_REQUEST, result)
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
    server.dashboard_state = state
    server.daemon_threads = True
    shown_host = "127.0.0.1" if host in ("", "0.0.0.0", "::") else host
    url = f"http://{shown_host}:{server.server_port}/?token={token}"
    return server, url


def url_file() -> "Path":  # noqa: F821 - Path imported lazily
    """Path of the running dashboard's URL record."""
    from pathlib import Path

    from claude_swap import paths

    return Path(paths.get_backup_root()) / URL_FILENAME


def publish_url(url: str) -> None:
    """Record this dashboard's URL for other launches to find."""
    import os
    import sys as _sys

    target = url_file()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(url, encoding="utf-8")
        if _sys.platform != "win32":
            os.chmod(target, 0o600)  # it carries the token
    except OSError as e:
        _logger.debug("Could not record dashboard URL: %s", e)


def clear_url() -> None:
    """Remove the URL record. Best effort; a stale one is detected on read."""
    try:
        url_file().unlink()
    except OSError:
        pass


def live_dashboard_url(*, timeout: float = 2.0) -> str | None:
    """A URL for an ALREADY-RUNNING dashboard, or None.

    The recorded URL is only a claim -- a crashed process leaves one behind --
    so it is verified with a real request before being trusted. An unreachable
    or unauthorized record is treated as stale and cleared.
    """
    import urllib.error
    import urllib.parse
    import urllib.request

    try:
        url = url_file().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not url:
        return None
    parsed = urllib.parse.urlparse(url)
    token = urllib.parse.parse_qs(parsed.query).get("token", [""])[0]
    probe = f"{parsed.scheme}://{parsed.netloc}/api/state"
    request = urllib.request.Request(probe, headers={"X-Auth-Token": token})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status == 200:
                return url
    except Exception:  # noqa: BLE001 - any failure means "not usable"
        pass
    clear_url()
    return None
