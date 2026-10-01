from __future__ import annotations

import base64
import hashlib
import http.client
import io
import json
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agents_switcher.codex import browser_login as browser_mod
from agents_switcher.codex.browser_login import BrowserLogin, BrowserLoginError
from agents_switcher.codex.enrollment import CodexEnrollment, EnrollmentError
from agents_switcher.codex.tokens import CLIENT_ID, ISSUER
from tests.test_codex_enrollment import env, login, save_current, snapshot

_actual_open_default_browser = browser_mod._open_default_browser


@pytest.fixture(autouse=True)
def isolated_transport(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("No real provider or default browser may be used by this test")

    monkeypatch.setattr(browser_mod, "CALLBACK_PORTS", (0,))
    monkeypatch.setattr(browser_mod, "_open_default_browser", forbidden)
    monkeypatch.setattr(browser_mod.urllib.request, "build_opener", forbidden)


@pytest.fixture
def browser(monkeypatch):
    monkeypatch.setattr(browser_mod, "_exchange_code", Mock(return_value=login("two", "browser")))
    session = BrowserLogin(expires_at=time.monotonic() + 60)
    session.start()
    yield session
    session.close()


def callback(browser, *, params=None, path=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", browser._server.server_port, timeout=3)
    try:
        if path is None:
            path = "/auth/callback?" + urllib.parse.urlencode(
                params if params is not None else {"state": browser._state, "code": "synthetic-code"},
            )
        connection.request("GET", path, headers=headers or {})
        response = connection.getresponse()
        return response.status, response.read().decode("utf-8"), dict(response.getheaders())
    finally:
        connection.close()


def test_fixed_authorization_parameters_and_pkce_never_reach_public_status(browser, monkeypatch):
    opened = []
    monkeypatch.setenv("CODEX_APP_SERVER_LOGIN_CLIENT_ID", "untrusted-client")
    monkeypatch.setenv("CODEX_AUTH_ISSUER", "https://untrusted.invalid")
    monkeypatch.setattr(browser_mod, "_open_default_browser", opened.append)
    result = browser.open_browser()
    assert result["status"] == "waiting"
    parsed = urllib.parse.urlsplit(opened[0])
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == f"{ISSUER}/oauth/authorize"
    query = urllib.parse.parse_qs(parsed.query)
    assert query["client_id"] == [CLIENT_ID]
    assert query["redirect_uri"] == [f"http://127.0.0.1:{browser._server.server_port}/auth/callback"]
    assert query["response_type"] == ["code"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"] == [base64.urlsafe_b64encode(
        hashlib.sha256(browser._verifier.encode("ascii")).digest(),
    ).decode().rstrip("=")]
    assert len(browser._state) >= 40 and len(browser._verifier) >= 80
    public = json.dumps(browser.status())
    for secret in (browser._state, browser._verifier, opened[0]):
        assert secret not in public


@pytest.mark.parametrize("params", [
    {"state": "wrong", "code": "synthetic-code"},
    {"state": "wrong", "error": "access_denied", "error_description": "synthetic-sensitive-body"},
    {"state": "非ascii", "code": "synthetic-code"},
    {"code": "synthetic-code"},
])
def test_wrong_state_never_consumes_the_pending_login(browser, params):
    code, message, _ = callback(browser, params=params)
    assert code == 400
    assert "synthetic-sensitive" not in message
    assert browser.status()["status"] == "waiting"
    browser_mod._exchange_code.assert_not_called()
    assert callback(browser)[0] == 200
    browser_mod._exchange_code.assert_called_once()


@pytest.mark.parametrize("path,headers", [
    ("/auth/callback?state=a&state=b&code=c", {}),
    ("/auth/callback?" + "x" * 8192, {}),
    ("/auth/callback?" + "&".join(f"p{i}=v" for i in range(21)), {}),
    ("/auth/callback?state=a&code=c", {"Host": "attacker.invalid"}),
])
def test_callback_rejects_ambiguous_or_oversized_requests(browser, path, headers):
    assert callback(browser, path=path, headers=headers)[0] == 400
    assert browser.status()["status"] == "waiting"
    browser_mod._exchange_code.assert_not_called()


def test_other_routes_never_cancel_or_expose_the_session(browser):
    for path in ("/cancel", "/", "http://attacker.invalid/auth/callback"):
        assert callback(browser, path=path, headers={"Host": f"127.0.0.1:{browser._server.server_port}"})[0] == 404
    assert browser.status()["status"] == "waiting"


def test_success_is_ready_without_saving_and_replay_cannot_exchange_twice(browser, capsys):
    state = browser._state
    code, message, headers = callback(browser)
    assert code == 200
    assert "check that your account was saved" in message
    assert "no-store" in headers["Cache-Control"]
    assert headers["Referrer-Policy"] == "no-referrer"
    assert browser.status()["status"] == "ready"
    assert browser.credentials() == login("two", "browser")
    assert browser._callback({"state": state, "code": "replayed-code"})[0] == 409
    browser_mod._exchange_code.assert_called_once_with("synthetic-code", browser._verifier, browser._redirect_uri)
    assert "secret-refresh" not in json.dumps(browser.status())
    assert capsys.readouterr() == ("", "")


def test_saving_a_ready_login_does_not_truncate_the_browser_callback_response(browser, monkeypatch):
    ready, release = threading.Event(), threading.Event()
    actual = browser._callback

    def paused(params):
        result = actual(params)
        ready.set()
        assert release.wait(3)
        return result

    monkeypatch.setattr(browser, "_callback", paused)
    with ThreadPoolExecutor(max_workers=1) as pool:
        response = pool.submit(callback, browser)
        assert ready.wait(2)
        assert browser.credentials() == login("two", "browser")
        browser.close()
        browser.close()
        release.set()
        assert response.result()[0] == 200
    assert browser._credentials is None


def test_provider_error_is_validated_and_sanitized_before_consuming_session(browser):
    code, message, _ = callback(browser, params={
        "state": browser._state, "error": "access_denied", "error_description": "synthetic-secret-error",
    })
    assert code == 400 and "synthetic-secret" not in message
    assert browser.status()["status"] == "error"
    assert "synthetic-secret" not in json.dumps(browser.status())
    browser_mod._exchange_code.assert_not_called()


def test_unexpected_exchange_exception_never_exposes_its_body(browser, monkeypatch):
    monkeypatch.setattr(browser_mod, "_exchange_code", Mock(side_effect=RuntimeError("synthetic-secret-error")))
    assert callback(browser)[0] == 400
    assert browser.status()["status"] == "error"
    assert "synthetic-secret" not in json.dumps(browser.status())
    with pytest.raises(BrowserLoginError, match="Start sign-in again"):
        browser.credentials()


def test_open_failure_can_retry_without_replacing_session(browser, monkeypatch):
    original = browser._state
    opener = Mock(side_effect=[BrowserLoginError("Default browser unavailable."), None])
    monkeypatch.setattr(browser_mod, "_open_default_browser", opener)
    with pytest.raises(BrowserLoginError):
        browser.open_browser()
    assert browser.status()["status"] == "waiting"
    assert browser.open_browser()["ok"]
    assert browser._state == original
    assert opener.call_count == 2


@pytest.mark.parametrize("ending", ["cancel", "expire"])
def test_late_exchange_cannot_publish_credentials_after_cancel_or_expiry(browser, monkeypatch, ending):
    entered, release = threading.Event(), threading.Event()

    def blocked(*args):
        entered.set()
        assert release.wait(3)
        return login("two", "late")

    monkeypatch.setattr(browser_mod, "_exchange_code", blocked)
    state = browser._state
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(browser._callback, {"state": state, "code": "synthetic-code"})
        assert entered.wait(2)
        assert browser.status()["status"] == "exchanging"
        if ending == "cancel":
            before = time.monotonic()
            browser.close()
            assert time.monotonic() - before < 1
        else:
            browser._expires_at = time.monotonic() - 1
            assert browser.status()["status"] == "expired"
        release.set()
        assert result.result()[0] == 409
    assert browser._credentials is None
    with pytest.raises(BrowserLoginError):
        browser.credentials()


def test_cancel_releases_the_callback_listener_and_clears_private_state(browser):
    port = browser._server.server_port
    browser.close()
    assert browser._authorization_url == browser._state == browser._verifier == ""
    assert browser._worker.is_alive() is False
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))


def test_sequential_signins_can_reuse_a_completed_callback_port(browser, monkeypatch):
    port = browser._server.server_port
    assert callback(browser)[0] == 200
    browser.close()
    monkeypatch.setattr(browser_mod, "CALLBACK_PORTS", (port,))
    for _ in range(3):
        session = BrowserLogin(expires_at=time.monotonic() + 60)
        try:
            session.start()
            assert callback(session)[0] == 200
        finally:
            session.close()


def test_an_active_callback_listener_cannot_be_rebound(browser, monkeypatch):
    monkeypatch.setattr(browser_mod, "CALLBACK_PORTS", (browser._server.server_port,))
    another = BrowserLogin(expires_at=time.monotonic() + 60)
    try:
        with pytest.raises(BrowserLoginError, match="ports are busy"):
            another.start()
        assert browser.status()["status"] == "waiting"
    finally:
        another.close()


def test_only_fixed_production_ports_are_tried_and_collision_never_contacts_listener(monkeypatch):
    assert BrowserLogin.start.__globals__["CALLBACK_PORTS"] == (0,)
    attempts = []
    actual = browser_mod._CallbackServer

    def occupied(address, handler):
        attempts.append(address)
        raise OSError("address already in use")

    monkeypatch.setattr(browser_mod, "CALLBACK_PORTS", (1455, 1457))
    monkeypatch.setattr(browser_mod, "_CallbackServer", occupied)
    session = BrowserLogin(expires_at=time.monotonic() + 60)
    with pytest.raises(BrowserLoginError, match="ports are busy"):
        session.start()
    session.close()
    assert attempts == [("127.0.0.1", 1455), ("127.0.0.1", 1457)]
    attempts.clear()

    def fallback(address, handler):
        attempts.append(address)
        if len(attempts) == 1:
            raise OSError("address already in use")
        return actual(("127.0.0.1", 0), handler)

    monkeypatch.setattr(browser_mod, "_CallbackServer", fallback)
    try:
        session.start()
        assert attempts == [("127.0.0.1", 1455), ("127.0.0.1", 1457)]
    finally:
        session.close()


def exchange_response(monkeypatch, raw):
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = raw
    opener = Mock()
    opener.open.return_value = response
    factory = Mock(return_value=opener)
    monkeypatch.setattr(browser_mod.urllib.request, "build_opener", factory)
    return factory, opener, response


def test_exchange_accepts_opaque_tokens_and_fixes_the_one_time_exchange_parameters(monkeypatch):
    tokens = {**login()["tokens"], "access_token": "opaque-access", "refresh_token": "synthetic-refresh"}
    factory, opener, response = exchange_response(monkeypatch, json.dumps(tokens).encode())
    credentials = browser_mod._exchange_code("synthetic-code", "synthetic-verifier", "http://127.0.0.1:1455/auth/callback")
    assert credentials["tokens"] == tokens
    assert credentials["auth_mode"] == "chatgpt" and credentials["OPENAI_API_KEY"] is None
    request = opener.open.call_args.args[0]
    assert request.full_url == f"{ISSUER}/oauth/token"
    assert urllib.parse.parse_qs(request.data.decode()) == {
        "grant_type": ["authorization_code"], "client_id": [CLIENT_ID], "code": ["synthetic-code"],
        "code_verifier": ["synthetic-verifier"], "redirect_uri": ["http://127.0.0.1:1455/auth/callback"],
    }
    assert opener.open.call_args.kwargs == {"timeout": browser_mod.EXCHANGE_TIMEOUT_SECONDS}
    assert isinstance(factory.call_args.args[0], browser_mod._NoRedirect)
    response.read.assert_called_once_with(browser_mod.MAX_RESPONSE_BYTES + 1)
    opener.open.assert_called_once()


@pytest.mark.parametrize("raw", [
    b"synthetic-secret-not-json", b"null", b"[]", b"{}",
    b"x" * (browser_mod.MAX_RESPONSE_BYTES + 1),
    json.dumps({"id_token": "invalid", "access_token": "opaque", "refresh_token": "synthetic"}).encode(),
])
def test_exchange_rejects_malformed_or_oversized_response_without_leaking_it(monkeypatch, raw):
    _, opener, _ = exchange_response(monkeypatch, raw)
    with pytest.raises(BrowserLoginError) as caught:
        browser_mod._exchange_code("synthetic-code", "synthetic-verifier", "http://127.0.0.1:1455/auth/callback")
    assert "synthetic" not in str(caught.value)
    opener.open.assert_called_once()


@pytest.mark.parametrize("failure", [
    urllib.error.HTTPError("https://synthetic.invalid", 400, "synthetic-secret", {}, io.BytesIO(b"synthetic-secret")),
    urllib.error.HTTPError("https://synthetic.invalid", 302, "redirect", {}, io.BytesIO(b"synthetic-secret")),
    urllib.error.URLError("synthetic-secret"), TimeoutError("synthetic-secret"),
])
def test_exchange_never_retries_an_uncertain_code_or_discloses_provider_errors(monkeypatch, failure):
    _, opener, _ = exchange_response(monkeypatch, b"{}")
    opener.open.side_effect = failure
    with pytest.raises(BrowserLoginError) as caught:
        browser_mod._exchange_code("synthetic-code", "synthetic-verifier", "http://127.0.0.1:1455/auth/callback")
    assert "synthetic" not in str(caught.value)
    opener.open.assert_called_once()
    assert browser_mod._NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.invalid") is None


@pytest.mark.parametrize("platform,executable", [("linux", "/usr/bin/xdg-open"), ("darwin", "/usr/bin/open")])
def test_default_browser_launcher_is_bounded_and_cannot_corrupt_backend_stdio(monkeypatch, platform, executable):
    monkeypatch.setattr(browser_mod.sys, "platform", platform)
    monkeypatch.setenv("BROWSER", "untrusted-command")
    monkeypatch.setattr(browser_mod.shutil, "which", lambda name: "/usr/bin/xdg-open")
    launcher = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(browser_mod.subprocess, "run", launcher)
    _actual_open_default_browser("https://auth.openai.com/synthetic")
    args, kwargs = launcher.call_args
    assert args[0] == [executable, "https://auth.openai.com/synthetic"]
    assert kwargs["stdin"] == kwargs["stdout"] == kwargs["stderr"] == subprocess.DEVNULL
    assert kwargs["timeout"] == 5 and "BROWSER" not in kwargs["env"]
    assert kwargs.get("shell", False) is False


def test_windows_opens_default_browser_without_shell_interpolation(monkeypatch):
    monkeypatch.setattr(browser_mod.sys, "platform", "win32")
    launcher = Mock()
    monkeypatch.setattr(browser_mod.os, "startfile", launcher, raising=False)
    _actual_open_default_browser("https://auth.openai.com/synthetic")
    launcher.assert_called_once_with("https://auth.openai.com/synthetic")


@pytest.mark.parametrize("failure", [OSError("synthetic-secret"), subprocess.TimeoutExpired("synthetic-secret", 5)])
def test_browser_launch_errors_are_safe_and_offer_terminal_fallback(monkeypatch, failure):
    monkeypatch.setattr(browser_mod.sys, "platform", "linux")
    monkeypatch.setattr(browser_mod.shutil, "which", lambda name: "/usr/bin/xdg-open")
    monkeypatch.setattr(browser_mod.subprocess, "run", Mock(side_effect=failure))
    with pytest.raises(BrowserLoginError, match="agent-switch codex login") as caught:
        _actual_open_default_browser("https://auth.openai.com/synthetic")
    assert "synthetic" not in str(caught.value)


@pytest.fixture
def enrollment(env, monkeypatch):
    env.controller.close()
    env.controller = CodexEnrollment(env.switcher, browser=True)
    monkeypatch.setattr(browser_mod, "_exchange_code", Mock(return_value=login("two", "browser")))
    yield env
    env.controller.close()


def test_browser_enrollment_reuses_saving_guards_and_never_activates(enrollment):
    first, original = save_current(enrollment)
    before = snapshot(enrollment)
    prepared = enrollment.controller.prepare(confirm=True)
    session_id = prepared["sessionId"]
    assert prepared["method"] == "browser" and "command" not in prepared
    assert enrollment.controller.prepare(confirm=True) == prepared
    assert callback(enrollment.controller._session.browser)[0] == 200
    assert enrollment.controller.status(session_id, confirm=True)["status"] == "ready"
    assert snapshot(enrollment) == before
    saved = enrollment.controller.complete(session_id, confirm=True)
    assert saved["activationRequired"] and saved["account"]["number"] == "2"
    assert snapshot(enrollment)[0] == original
    assert enrollment.store.active_number() == first.number
    assert enrollment.controller.complete(session_id, confirm=True) == saved
    assert enrollment.controller.status(session_id, confirm=True)["status"] == "saved"
    enrollment.controller.cancel(session_id, confirm=True)
    assert enrollment.store.read_credentials("2") == login("two", "browser")
    assert enrollment.switcher.activation_required(enrollment.store.get("2"))


def test_browser_repair_rejects_wrong_identity_and_retains_metadata_for_correct_signin(enrollment, monkeypatch):
    first, original = save_current(enrollment, alias="personal")
    first = replace(first, disabled=True)
    enrollment.store.update(first)
    before = snapshot(enrollment)
    prepared = enrollment.controller.prepare(number="1", confirm=True)
    session_id = prepared["sessionId"]
    assert callback(enrollment.controller._session.browser)[0] == 200
    with pytest.raises(EnrollmentError, match="different Codex account") as caught:
        enrollment.controller.complete(session_id, confirm=True)
    assert caught.value.code == "wrong-account"
    assert snapshot(enrollment) == before
    enrollment.controller.cancel(session_id, confirm=True)
    monkeypatch.setattr(browser_mod, "_exchange_code", Mock(return_value=login("one", "repair")))
    session_id = enrollment.controller.prepare(number="1", confirm=True)["sessionId"]
    assert callback(enrollment.controller._session.browser)[0] == 200
    saved = enrollment.controller.complete(session_id, confirm=True)
    assert saved["account"]["alias"] == "personal" and saved["account"]["disabled"] is True
    assert saved["account"]["added"] == first.added
    assert snapshot(enrollment)[0] == original
    assert enrollment.switcher.activation_required(first)
    enrollment.controller.cancel(session_id, confirm=True)
    enrollment.switcher.status()
    assert enrollment.store.read_credentials("1") == login("one", "repair")


def test_browser_save_failure_retries_same_login_without_exchanging_again(enrollment, monkeypatch):
    session_id = enrollment.controller.prepare(confirm=True)["sessionId"]
    assert callback(enrollment.controller._session.browser)[0] == 200
    actual = enrollment.switcher.import_login
    attempts = []

    def import_login(*args, **kwargs):
        attempts.append(True)
        if len(attempts) == 1:
            raise OSError("synthetic-secret")
        return actual(*args, **kwargs)

    monkeypatch.setattr(enrollment.switcher, "import_login", import_login)
    with pytest.raises(EnrollmentError, match="Could not save"):
        enrollment.controller.complete(session_id, confirm=True)
    assert not enrollment.switcher.list_accounts()
    assert enrollment.controller.status(session_id, confirm=True)["status"] == "ready"
    assert enrollment.controller.complete(session_id, confirm=True)["ok"]
    browser_mod._exchange_code.assert_called_once()


@pytest.mark.parametrize("method", ["open_browser", "status", "complete", "cancel"])
@pytest.mark.parametrize("confirm", [False, None, "true", 1])
def test_browser_controller_requires_exact_confirmation(enrollment, method, confirm):
    session_id = enrollment.controller.prepare(confirm=True)["sessionId"]
    with pytest.raises(EnrollmentError, match="Confirm"):
        getattr(enrollment.controller, method)(session_id, confirm=confirm)


def test_browser_session_has_no_terminal_environment(enrollment):
    session_id = enrollment.controller.prepare(confirm=True)["sessionId"]
    with pytest.raises(EnrollmentError, match="does not use a terminal"):
        enrollment.controller.cli_environment(session_id)


def test_expired_controller_cannot_save_ready_credentials(enrollment):
    session_id = enrollment.controller.prepare(confirm=True)["sessionId"]
    assert callback(enrollment.controller._session.browser)[0] == 200
    enrollment.controller._session.expires_at = time.monotonic() - 1
    assert enrollment.controller.status(session_id, confirm=True)["status"] == "expired"
    with pytest.raises(EnrollmentError):
        enrollment.controller.complete(session_id, confirm=True)
    assert not enrollment.switcher.list_accounts()


@pytest.mark.parametrize("ending", ["cancel", "close"])
def test_real_callback_cannot_save_after_controller_cancellation(enrollment, monkeypatch, ending):
    entered, release = threading.Event(), threading.Event()

    def blocked(*args):
        entered.set()
        assert release.wait(3)
        return login("two", "late")

    monkeypatch.setattr(browser_mod, "_exchange_code", blocked)
    session_id = enrollment.controller.prepare(confirm=True)["sessionId"]
    transport = enrollment.controller._session.browser
    with ThreadPoolExecutor(max_workers=1) as pool:
        request = pool.submit(callback, transport)
        assert entered.wait(2)
        if ending == "cancel":
            enrollment.controller.cancel(session_id, confirm=True)
        else:
            enrollment.controller.close()
        release.set()
        with pytest.raises((OSError, http.client.HTTPException)):
            request.result()
    transport._worker.join(2)
    assert not transport._worker.is_alive()
    assert transport._credentials is None
    with pytest.raises(EnrollmentError):
        enrollment.controller.complete(session_id, confirm=True)
    assert not enrollment.switcher.list_accounts()


def test_idle_timeout_closes_the_owned_listener_without_a_status_poll():
    session = BrowserLogin(expires_at=time.monotonic() + 0.02)
    session.start()
    try:
        session._worker.join(2)
        assert not session._worker.is_alive()
        assert session.status()["status"] == "expired"
        assert session._server.socket.fileno() == -1
    finally:
        session.close()
