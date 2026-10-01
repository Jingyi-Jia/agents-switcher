"""Owned, loopback-only browser sign-in; persistence belongs to enrollment."""

from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import json
import os
import secrets
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

from agents_switcher.codex.identity import OPENAI_AUTH_CLAIM, decode_jwt_claims
from agents_switcher.codex.tokens import CLIENT_ID, ISSUER
from agents_switcher.exceptions import ClaudeSwitchError

CALLBACK_PORTS = (1455, 1457)
MAX_RESPONSE_BYTES = 1024 * 1024
EXCHANGE_TIMEOUT_SECONDS = 15


class BrowserLoginError(ClaudeSwitchError):
    """A safe error that never includes provider responses or login material."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _exchange_code(code: str, verifier: str, redirect_uri: str) -> dict:
    request = urllib.request.Request(
        f"{ISSUER}/oauth/token",
        data=urllib.parse.urlencode({
            "grant_type": "authorization_code", "client_id": CLIENT_ID,
            "code": code, "code_verifier": verifier, "redirect_uri": redirect_uri,
        }).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.build_opener(_NoRedirect()).open(
            request, timeout=EXCHANGE_TIMEOUT_SECONDS,
        ) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("oversized response")
        payload = json.loads(raw)
    except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError, RecursionError):
        raise BrowserLoginError(
            "Codex sign-in could not be completed. Check your connection and start sign-in again. "
            "No account was saved."
        ) from None
    if not isinstance(payload, dict) or any(
        not isinstance(payload.get(key), str) or not payload[key].strip()
        for key in ("id_token", "access_token", "refresh_token")
    ):
        raise BrowserLoginError("Codex returned an incomplete login. Start sign-in again; no account was saved.")
    try:
        claims = decode_jwt_claims(payload["id_token"])
    except RecursionError:
        claims = None
    identity = claims.get(OPENAI_AUTH_CLAIM) if isinstance(claims, dict) else None
    if (
        not isinstance(identity, dict)
        or not isinstance(identity.get("chatgpt_account_id"), str)
        or not identity["chatgpt_account_id"].strip()
        or ("email" in claims and not isinstance(claims["email"], str))
        or ("chatgpt_plan_type" in identity and not isinstance(identity["chatgpt_plan_type"], str))
    ):
        raise BrowserLoginError("Codex did not identify a valid account. Start sign-in again; no account was saved.")
    return {
        "auth_mode": "chatgpt", "OPENAI_API_KEY": None,
        "tokens": {
            **{key: payload[key] for key in ("id_token", "access_token", "refresh_token")},
            "account_id": identity["chatgpt_account_id"],
        },
        "last_refresh": datetime.now(UTC).isoformat(),
    }


def _open_default_browser(url: str) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(url)
            return
        executable = "/usr/bin/open" if sys.platform == "darwin" else shutil.which("xdg-open")
        if not executable:
            raise OSError("no default browser launcher")
        result = subprocess.run(
            [executable, url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=5, check=False,
            env={key: value for key, value in os.environ.items() if key.upper() != "BROWSER"},
        )
        if result.returncode != 0:
            raise OSError("browser launcher failed")
    except (OSError, subprocess.SubprocessError):
        raise BrowserLoginError(
            "The default browser could not be opened. Check your browser settings and retry. "
            "On a headless machine, use agent-switch codex login in a terminal instead."
        ) from None


class _CallbackServer(HTTPServer):
    allow_reuse_address = not hasattr(socket, "SO_EXCLUSIVEADDRUSE")

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        socketserver.TCPServer.server_bind(self)
        self.server_name = "127.0.0.1"
        self.server_port = self.server_address[1]

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(2)
        self.login._connection = connection
        return connection, address

    def handle_error(self, request, client_address) -> None:
        pass


class _CallbackHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args) -> None:
        pass

    def _reply(self, status: int, message: str) -> None:
        content = message.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:
        login = self.server.login
        if len(self.path) > 8192 or self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
            self._reply(400, "Invalid sign-in callback.")
            return
        try:
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.scheme or parsed.netloc or parsed.path != "/auth/callback":
                self._reply(404, "Not found.")
                return
            params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True, max_num_fields=20)
            if any(len(values) != 1 for values in params.values()):
                raise ValueError("duplicate callback parameters")
        except ValueError:
            self._reply(400, "Invalid sign-in callback.")
            return
        status, message = login._callback({key: values[0] for key, values in params.items()})
        self._reply(status, message)


class BrowserLogin:
    """One private OAuth session with a cancellable, bounded-lifetime listener."""

    def __init__(self, *, expires_at: float) -> None:
        self._expires_at = expires_at
        self._state = secrets.token_urlsafe(32)
        self._verifier = secrets.token_urlsafe(64)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._status = "waiting"
        self._message = "Finish signing in in your browser. Your current login is unchanged."
        self._credentials: dict | None = None
        self._connection: socket.socket | None = None
        self._server: _CallbackServer | None = None
        self._worker: threading.Thread | None = None
        self._redirect_uri = ""
        self._authorization_url = ""

    def start(self) -> None:
        for port in CALLBACK_PORTS:
            try:
                self._server = _CallbackServer(("127.0.0.1", port), _CallbackHandler)
                break
            except OSError:
                continue
        if self._server is None:
            raise BrowserLoginError(
                "The Codex sign-in ports are busy. Finish or cancel another Codex sign-in, then try again. "
                "No other application was stopped."
            )
        self._server.login = self
        self._server.timeout = 0.2
        self._redirect_uri = f"http://127.0.0.1:{self._server.server_port}/auth/callback"
        challenge = base64.urlsafe_b64encode(hashlib.sha256(self._verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
        self._authorization_url = f"{ISSUER}/oauth/authorize?" + urllib.parse.urlencode({
            "response_type": "code", "client_id": CLIENT_ID, "redirect_uri": self._redirect_uri,
            "scope": "openid profile email offline_access api.connectors.read api.connectors.invoke",
            "state": self._state, "code_challenge": challenge, "code_challenge_method": "S256",
            "id_token_add_organizations": "true", "codex_cli_simplified_flow": "true", "originator": "codex_cli_rs",
        })
        self._worker = threading.Thread(target=self._run, name="codex-browser-sign-in", daemon=True)
        self._worker.start()

    def _expire(self) -> None:
        if time.monotonic() >= self._expires_at and self._status not in {"cancelled", "expired"}:
            self._status = "expired"
            self._message = "Codex sign-in expired. Start sign-in again; no account was saved."
            self._credentials = None
            self._stop.set()

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                with self._lock:
                    self._expire()
                    if self._status in {"ready", "error", "expired", "cancelled"}:
                        break
                try:
                    self._server.handle_request()
                except (OSError, ValueError):
                    if not self._stop.is_set():
                        with self._lock:
                            self._status = "error"
                            self._message = "The local sign-in listener stopped. Start sign-in again; no account was saved."
                    break
        finally:
            self._server.server_close()

    def _callback(self, params: dict[str, str]) -> tuple[int, str]:
        with self._lock:
            self._expire()
            state = params.get("state", "")
            if not state.isascii() or not hmac.compare_digest(state, self._state):
                return 400, "This callback does not belong to the pending sign-in. Return to Agent Switch."
            if self._status != "waiting":
                return 409, "This sign-in is no longer waiting. Return to Agent Switch."
            if params.get("error"):
                self._status = "error"
                self._message = "Sign-in was cancelled or refused in the browser. Start again when you are ready. No account was saved."
                return 400, "Sign-in did not finish. Return to Agent Switch to try again."
            code = params.get("code", "")
            if not code or not code.strip():
                return 400, "The sign-in callback is incomplete. Return to Agent Switch."
            self._status = "exchanging"
            self._message = "Completing the browser sign-in…"
            verifier, redirect_uri = self._verifier, self._redirect_uri
        try:
            credentials = _exchange_code(code, verifier, redirect_uri)
        except Exception as error:
            with self._lock:
                self._expire()
                if self._status == "exchanging":
                    self._status = "error"
                    self._message = str(error) if isinstance(error, BrowserLoginError) else (
                        "Codex sign-in could not be completed. Start sign-in again; no account was saved."
                    )
            return 400, "Sign-in did not finish. Return to Agent Switch to try again."
        with self._lock:
            self._expire()
            if self._status != "exchanging":
                return 409, "This sign-in is no longer active. Return to Agent Switch."
            self._credentials = credentials
            self._status = "ready"
            self._message = "Signed in. Ready to save this account without changing the current login."
        return 200, "Sign-in received. Return to Agent Switch to check that your account was saved. You can close this tab."

    def open_browser(self) -> dict:
        with self._lock:
            self._expire()
            if self._status != "waiting" or not self._authorization_url:
                raise BrowserLoginError("This sign-in is no longer waiting for the browser. Check its status in Agent Switch.")
            url = self._authorization_url
        _open_default_browser(url)
        return self.status()

    def status(self) -> dict:
        with self._lock:
            self._expire()
            return {
                "ok": self._status in {"waiting", "exchanging", "ready"},
                "status": self._status, "message": self._message,
            }

    def credentials(self) -> dict:
        with self._lock:
            self._expire()
            if self._status != "ready" or self._credentials is None:
                raise BrowserLoginError(self._message)
            return {**self._credentials, "tokens": dict(self._credentials["tokens"])}

    def close(self) -> None:
        with self._lock:
            if self._status == "cancelled":
                return
            replying = self._status == "ready"
            self._status = "cancelled"
            self._message = "Codex sign-in was cancelled. No account was saved by this sign-in."
            self._credentials = None
            self._authorization_url = self._state = self._verifier = ""
            self._stop.set()
        if self._connection is not None and not replying:
            try:
                self._connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._connection.close()
        if self._server is not None:
            self._server.server_close()
        if self._worker is not None and self._worker is not threading.current_thread():
            self._worker.join(0.5)
