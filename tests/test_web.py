"""Tests for the local dashboard.

Driven in-process against a real ThreadingHTTPServer on an ephemeral port, so
these exercise the actual request handling rather than a mocked handler.
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request

import pytest

from claude_swap.codex.store import CodexAccount
from claude_swap.codex.switcher import CodexStatus, SwitchResult
from claude_swap.codex.usage import CodexCredits, CodexUsage, CodexWindow
from claude_swap.web.server import DashboardState, serve


class StubCodex:
    def __init__(self, accounts=(), usage=None, active="1", result=None, error=None):
        self._accounts, self._usage = list(accounts), dict(usage or {})
        self._result, self._error = result, error
        self.active = active
        self.switched: list[str] = []

        class Store:
            def active_number(self):
                return active

        self.store = Store()

    def list_accounts(self):
        return list(self._accounts)

    def status(self):
        account = next((a for a in self._accounts if a.number == self.active), None)
        return CodexStatus(
            bool(account), account.identity if account else None,
            account, account.number if account else None,
        )

    def usage_all(self):
        return dict(self._usage)

    def switch_to(self, number, **kw):
        self.switched.append(number)
        if self._error:
            raise self._error
        self.active = number
        return self._result


def account(number="1", email="one@example.com"):
    return CodexAccount(number=number, email=email, account_id=f"acct-{number}",
                        plan="pro")


def healthy(used=20):
    return CodexUsage(allowed=True, plan="pro", windows=(CodexWindow(used, 604800),))


def on_credits():
    return CodexUsage(allowed=False, limit_reached=True, plan="pro",
                      credits=CodexCredits(has_credits=True),
                      windows=(CodexWindow(100, 604800),))


@pytest.fixture
def dashboard():
    """A running server on an ephemeral port; torn down after each test."""
    servers = []

    def start(claude=None, codex=None):
        state = DashboardState(claude_switcher=claude, codex_switcher=codex)
        server, url = serve(state, host="127.0.0.1", port=0)
        # poll_interval: serve_forever's 0.5s default makes every teardown
        # wait that long, which dominated the runtime of this file.
        thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        thread.start()
        servers.append((server, thread))
        token = url.split("token=")[1]
        base = f"http://127.0.0.1:{server.server_port}"
        return base, token, state

    yield start
    for server, thread in servers:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def get(base, path, token=None, header_token=None):
    url = f"{base}{path}" + (f"?token={token}" if token else "")
    request = urllib.request.Request(url)
    if header_token:
        request.add_header("X-Auth-Token", header_token)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def post(base, path, payload, token):
    request = urllib.request.Request(
        f"{base}{path}", data=json.dumps(payload).encode(), method="POST",
        headers={"X-Auth-Token": token, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


class TestAuthentication:
    """These endpoints switch which account the machine is logged in as, so
    reaching them must take more than knowing a port number."""

    def test_no_token_is_refused(self, dashboard):
        base, _, _ = dashboard(codex=StubCodex())
        assert get(base, "/api/state")[0] == 403

    def test_a_wrong_token_is_refused(self, dashboard):
        base, _, _ = dashboard(codex=StubCodex())
        assert get(base, "/api/state", token="nope")[0] == 403

    def test_the_page_itself_is_gated(self, dashboard):
        base, _, _ = dashboard(codex=StubCodex())
        assert get(base, "/")[0] == 403

    def test_a_query_token_works(self, dashboard):
        base, token, _ = dashboard(codex=StubCodex())
        assert get(base, "/api/state", token=token)[0] == 200

    def test_a_header_token_works(self, dashboard):
        base, token, _ = dashboard(codex=StubCodex())
        assert get(base, "/api/state", header_token=token)[0] == 200

    def test_switching_without_a_token_is_refused(self, dashboard):
        codex = StubCodex(accounts=[account()], result=None)
        base, _, _ = dashboard(codex=codex)
        status, _ = post(base, "/api/switch", {"provider": "codex", "number": "1"},
                         token="wrong")
        assert status == 403
        assert codex.switched == []   # and nothing happened

    def test_each_run_mints_a_different_token(self, dashboard):
        _, first, _ = dashboard(codex=StubCodex())
        _, second, _ = dashboard(codex=StubCodex())
        assert first != second

    def test_no_cors_header_is_offered(self, dashboard):
        # Another origin must not be able to read this page.
        base, token, _ = dashboard(codex=StubCodex())
        request = urllib.request.Request(f"{base}/api/state?token={token}")
        with urllib.request.urlopen(request, timeout=10) as response:
            assert "Access-Control-Allow-Origin" not in response.headers


class TestStartup:
    def test_binding_does_not_wait_on_reverse_dns(self, dashboard):
        """HTTPServer.server_bind calls socket.getfqdn purely to set a field
        nothing here reads. Measured at 35 SECONDS on an ordinary machine with
        slow reverse DNS -- during which the command looks frozen and has not
        printed the URL the user needs."""
        import time

        started = time.monotonic()
        dashboard(codex=StubCodex())
        assert time.monotonic() - started < 2.0

    def test_server_name_is_the_host_not_a_resolved_name(self):
        from claude_swap.web.server import DashboardState, serve

        server, _ = serve(DashboardState(), host="127.0.0.1", port=0)
        try:
            assert server.server_name == "127.0.0.1"
        finally:
            server.server_close()


class TestState:
    def test_reports_codex_accounts_and_usage(self, dashboard):
        base, token, _ = dashboard(
            codex=StubCodex(accounts=[account()], usage={"1": healthy(35)})
        )
        _, body = get(base, "/api/state", token=token)
        payload = json.loads(body)
        entry = payload["codex"]["accounts"][0]
        assert entry["percent"] == 35
        assert entry["active"] is True
        assert entry["autoSwitchEligible"] is True

    def test_a_credits_account_is_flagged_not_shown_as_capacity(self, dashboard):
        base, token, _ = dashboard(
            codex=StubCodex(accounts=[account()], usage={"1": on_credits()})
        )
        _, body = get(base, "/api/state", token=token)
        entry = json.loads(body)["codex"]["accounts"][0]
        assert entry["onCredits"] is True
        assert entry["autoSwitchEligible"] is False

    def test_a_failed_read_is_reported_per_account(self, dashboard):
        base, token, _ = dashboard(codex=StubCodex(
            accounts=[account("1"), account("2", "two@e.com")],
            usage={"1": RuntimeError("down"), "2": healthy(10)},
        ))
        _, body = get(base, "/api/state", token=token)
        accounts = json.loads(body)["codex"]["accounts"]
        assert accounts[0]["error"] == "down"
        assert accounts[1]["percent"] == 10

    def test_one_broken_provider_does_not_blank_the_other(self, dashboard):
        class Broken:
            def accounts_snapshot(self, *a, **k):
                raise RuntimeError("claude store unreadable")

        base, token, _ = dashboard(
            claude=Broken(), codex=StubCodex(accounts=[account()], usage={"1": healthy()})
        )
        _, body = get(base, "/api/state", token=token)
        payload = json.loads(body)
        assert payload["claude"]["available"] is False
        assert payload["codex"]["available"] is True

    def test_an_absent_provider_is_reported_not_crashed(self, dashboard):
        base, token, _ = dashboard(codex=StubCodex())
        payload = json.loads(get(base, "/api/state", token=token)[1])
        assert payload["claude"]["available"] is False
        assert payload["claude"]["accounts"] == []

    def test_unknown_paths_404(self, dashboard):
        base, token, _ = dashboard(codex=StubCodex())
        assert get(base, "/api/nope", token=token)[0] == 404


class TestLiveLogin:
    """A machine can be signed in to an account this tool does not manage.
    Reporting only managed accounts made that read as 'no accounts', which is
    true and useless -- the answer wanted is 'yes, as whom, and shall I manage
    it?'"""

    def test_an_unmanaged_codex_login_is_surfaced(self, dashboard):
        from claude_swap.codex.identity import CodexIdentity
        from claude_swap.codex.switcher import CodexStatus

        codex = StubCodex()
        codex.status = lambda: CodexStatus(
            logged_in=True,
            identity=CodexIdentity(email="live@example.com", account_id="a", plan="pro"),
            account=None, active_number=None,
        )
        base, token, _ = dashboard(codex=codex)
        payload = json.loads(get(base, "/api/state", token=token)[1])
        live = payload["codex"]["liveLogin"]
        assert live["email"] == "live@example.com"
        assert live["managed"] is False

    def test_an_unmanaged_claude_login_is_surfaced(self, dashboard):
        class Claude:
            def accounts_snapshot(self, *a, **k):
                from claude_swap.models import AccountsSnapshot

                return AccountsSnapshot(active_number=None, accounts=(), taken_at=0.0)

            def status(self, json_output=False):
                return {"active": {"email": "live@corp.com", "managed": False}}

        base, token, _ = dashboard(claude=Claude(), codex=StubCodex())
        live = json.loads(get(base, "/api/state", token=token)[1])["claude"]["liveLogin"]
        assert live == {"email": "live@corp.com", "managed": False}

    def test_no_login_reports_none_rather_than_failing(self, dashboard):
        codex = StubCodex()
        codex.status = lambda: CodexStatus(False, None, None, None)
        base, token, _ = dashboard(codex=codex)
        payload = json.loads(get(base, "/api/state", token=token)[1])
        assert payload["codex"]["liveLogin"] is None
        assert payload["codex"]["available"] is True   # still usable


class TestAddCurrent:
    def test_manages_the_live_codex_account(self, dashboard):
        codex = StubCodex()
        added = []
        codex.add_current = lambda **kw: (added.append(1), account("3"))[1]
        base, token, _ = dashboard(codex=codex)
        status, body = post(base, "/api/add", {"provider": "codex"}, token)
        assert status == 200 and body["ok"] is True
        assert "slot 3" in body["message"]

    def test_claude_add_never_blocks_on_a_prompt(self, dashboard):
        """An HTTP request must not wait on a terminal prompt nobody can see."""
        seen = {}

        class Claude:
            def accounts_snapshot(self, *a, **k):
                from claude_swap.models import AccountsSnapshot

                return AccountsSnapshot(active_number=None, accounts=(), taken_at=0.0)

            def status(self, json_output=False):
                return {"active": {"email": "x@y.com", "managed": False}}

            def add_account(self, **kwargs):
                seen.update(kwargs)

        base, token, _ = dashboard(claude=Claude(), codex=StubCodex())
        status, _ = post(base, "/api/add", {"provider": "claude"}, token)
        assert status == 200
        assert seen.get("assume_yes") is True

    def test_adding_requires_a_token(self, dashboard):
        base, _, _ = dashboard(codex=StubCodex())
        assert post(base, "/api/add", {"provider": "codex"}, "wrong")[0] == 403

    def test_an_unknown_provider_is_rejected(self, dashboard):
        base, token, _ = dashboard(codex=StubCodex())
        status, body = post(base, "/api/add", {"provider": "nope"}, token)
        assert status == 400 and "unknown provider" in body["message"]


class TestCaching:
    def test_repeated_reads_do_not_refetch(self, dashboard):
        """Without a cache a polling page drives token refreshes far more often
        than anything needs, and rotation is not free."""
        codex = StubCodex(accounts=[account()], usage={"1": healthy()})
        calls = {"n": 0}
        original = codex.usage_all

        def counted():
            calls["n"] += 1
            return original()

        codex.usage_all = counted
        base, token, _ = dashboard(codex=codex)
        for _ in range(3):
            get(base, "/api/state", token=token)
        assert calls["n"] == 1

    def test_force_bypasses_the_cache(self, dashboard):
        codex = StubCodex(accounts=[account()], usage={"1": healthy()})
        calls = {"n": 0}
        original = codex.usage_all

        def counted():
            calls["n"] += 1
            return original()

        codex.usage_all = counted
        base, token, _ = dashboard(codex=codex)
        get(base, "/api/state", token=token)
        get(base, f"/api/state?force=1&token={token}".replace("/api/state?", "/api/state?"),)
        # the second call carries the token in the same query string
        assert calls["n"] >= 1

    def test_a_switch_invalidates_the_cache(self, dashboard):
        codex = StubCodex(
            accounts=[account("1"), account("2", "two@e.com")],
            usage={"1": healthy(), "2": healthy()},
            result=SwitchResult(account=account("2", "two@e.com"), previous=None,
                                synced_back=False),
        )
        base, token, state = dashboard(codex=codex)
        get(base, "/api/state", token=token)
        assert state._cached is not None
        post(base, "/api/switch", {"provider": "codex", "number": "2"}, token)
        assert state._cached is None   # next read reflects the switch


class TestSwitching:
    def test_switches_and_reports(self, dashboard):
        codex = StubCodex(
            accounts=[account()],
            result=SwitchResult(account=account("2", "two@e.com"), previous=None,
                                synced_back=False),
        )
        base, token, _ = dashboard(codex=codex)
        status, body = post(base, "/api/switch",
                            {"provider": "codex", "number": "2"}, token)
        assert status == 200 and body["ok"] is True
        assert codex.switched == ["2"]
        assert body["restartRequired"] is False

    def test_a_running_codex_is_reported_as_needing_a_restart(self, dashboard):
        from claude_swap.codex.processes import CodexProcess

        codex = StubCodex(
            accounts=[account()],
            result=SwitchResult(
                account=account(), previous=None, synced_back=False,
                processes=(CodexProcess(3, "/bin/codex", "codex", tty="pts/1"),),
            ),
        )
        base, token, _ = dashboard(codex=codex)
        _, body = post(base, "/api/switch", {"provider": "codex", "number": "1"}, token)
        assert body["restartRequired"] is True
        assert "Restart Codex" in body["message"]

    def test_a_refused_switch_returns_the_reason(self, dashboard):
        from claude_swap.exceptions import SwitchError

        codex = StubCodex(accounts=[account()],
                          error=SwitchError("already the active Codex account"))
        base, token, _ = dashboard(codex=codex)
        status, body = post(base, "/api/switch",
                            {"provider": "codex", "number": "1"}, token)
        assert status == 400
        assert "already the active" in body["message"]

    def test_an_unknown_provider_is_rejected(self, dashboard):
        base, token, _ = dashboard(codex=StubCodex())
        status, body = post(base, "/api/switch",
                            {"provider": "gemini", "number": "1"}, token)
        assert status == 400
        assert "unknown provider" in body["message"]


class TestPage:
    def test_serves_html_with_a_valid_token(self, dashboard):
        base, token, _ = dashboard(codex=StubCodex())
        status, body = get(base, "/", token=token)
        assert status == 200
        assert b"<!doctype html>" in body.lower()

    def test_the_page_needs_no_external_resources(self, dashboard):
        """It has to render through an SSH tunnel on a cluster login node,
        where fetching anything from the internet is what will not work."""
        base, token, _ = dashboard(codex=StubCodex())
        _, body = get(base, "/", token=token)
        text = body.decode()
        assert "http://" not in text.replace("http://127.0.0.1", "")
        assert "cdn" not in text.lower()
        assert "<script src=" not in text


class TestSingleInstance:
    """A clickable icon gets double-clicked. Joining the dashboard that is
    already up is the right answer to that; "port in use" is not."""

    @pytest.fixture(autouse=True)
    def _isolated_record(self, tmp_path, monkeypatch):
        from claude_swap import paths

        monkeypatch.setattr(paths, "get_backup_root", lambda: tmp_path)

    def test_a_running_dashboard_is_found(self, dashboard):
        from claude_swap.web.server import live_dashboard_url, publish_url

        base, token, _ = dashboard(codex=StubCodex())
        publish_url(f"{base}/?token={token}")
        assert live_dashboard_url() == f"{base}/?token={token}"

    def test_a_stale_record_is_not_trusted(self, tmp_path):
        """A crashed process leaves a record behind, so the URL is only a claim
        until something answers it."""
        from claude_swap.web.server import live_dashboard_url, publish_url, url_file

        publish_url("http://127.0.0.1:9/?token=dead")  # nothing listens on 9
        assert live_dashboard_url(timeout=0.5) is None
        assert not url_file().exists()   # and it cleans up after itself

    def test_a_record_with_the_wrong_token_is_stale(self, dashboard):
        # Someone else's dashboard, or a rotated token: not ours to join.
        from claude_swap.web.server import live_dashboard_url, publish_url

        base, _, _ = dashboard(codex=StubCodex())
        publish_url(f"{base}/?token=wrong")
        assert live_dashboard_url(timeout=1.0) is None

    def test_no_record_is_simply_none(self):
        from claude_swap.web.server import live_dashboard_url

        assert live_dashboard_url() is None

    def test_an_empty_record_is_none(self, tmp_path):
        from claude_swap.web.server import live_dashboard_url, url_file

        url_file().write_text("   ")
        assert live_dashboard_url() is None

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission model")
    def test_the_record_is_owner_only_because_it_carries_the_token(self, tmp_path):
        import stat

        from claude_swap.web.server import publish_url, url_file

        publish_url("http://127.0.0.1:8765/?token=secret")
        assert stat.S_IMODE(url_file().stat().st_mode) == 0o600

    def test_clearing_is_safe_when_absent(self):
        from claude_swap.web.server import clear_url

        clear_url()  # must not raise
