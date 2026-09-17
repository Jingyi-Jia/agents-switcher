"""Tests for reading quota through the switcher.

The fetch is a harmless GET. The REFRESH is the hazard: it rotates the refresh
token, so a result that is not persisted strands the slot, and a refresh racing
Codex's own AuthManager invalidates one party's token.
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import pytest

from claude_swap.codex import switcher as switcher_mod
from claude_swap.codex.auth_file import read_auth, write_auth
from claude_swap.codex.identity import OPENAI_AUTH_CLAIM
from claude_swap.codex.processes import CodexProcess
from claude_swap.codex.store import CodexAccountStore
from claude_swap.codex.switcher import CodexSwitcher
from claude_swap.codex.usage import CodexUsage, UsageAuthError, UsageError


def jwt(claims: dict) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"hdr.{payload}.sig"


def auth_for(account_id="acct-1", email="one@example.com", refresh="rt-v1", exp=None):
    claims = {
        "email": email,
        OPENAI_AUTH_CLAIM: {"chatgpt_account_id": account_id, "chatgpt_plan_type": "pro"},
    }
    return {
        "auth_mode": "chatgpt",
        "tokens": {
            "id_token": jwt(claims),
            "access_token": jwt({"exp": exp if exp is not None else time.time() + 86400}),
            "refresh_token": refresh,
            "account_id": account_id,
        },
    }


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    home = tmp_path / "codexhome"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setattr(switcher_mod, "running_codex_processes", lambda: [])

    class Env:
        auth_path = home / "auth.json"

        def __init__(self):
            self.store = CodexAccountStore(root=tmp_path / "backup")
            self.switcher = CodexSwitcher(self.store)
            self.fetched: list[dict] = []

        def login_as(self, **kw):
            write_auth(auth_for(**kw), self.auth_path)

        def stub_fetch(self, mp, result=None, error=None):
            def fake(tokens, **kw):
                self.fetched.append(dict(tokens))
                if error is not None:
                    raise error
                return result if result is not None else CodexUsage(allowed=True)

            mp.setattr(switcher_mod, "fetch_usage", fake)

        def stub_refresh(self, mp, new_refresh="rt-v2"):
            calls = []

            def fake(tokens, **kw):
                calls.append(dict(tokens))
                return {**tokens, "refresh_token": new_refresh,
                        "access_token": jwt({"exp": time.time() + 86400})}

            mp.setattr(switcher_mod, "refresh_tokens", fake)
            return calls

    return Env()


class TestFetchWithoutRefresh:
    def test_a_valid_token_is_used_as_is(self, env, monkeypatch):
        env.login_as()
        env.switcher.add_current()
        refreshes = env.stub_refresh(monkeypatch)
        env.stub_fetch(monkeypatch, CodexUsage(plan="pro", allowed=True))

        result = env.switcher.usage_for("1")
        assert result.plan == "pro"
        assert refreshes == []  # refreshing rotates; don't do it needlessly

    def test_a_slot_without_a_credential_is_an_error(self, env, monkeypatch):
        env.login_as()
        env.switcher.add_current()
        env.store.delete_credentials("1")
        with pytest.raises(Exception, match="no saved credential"):
            env.switcher.usage_for("1")


class TestRefreshOnExpiry:
    def test_an_expiring_token_is_refreshed(self, env, monkeypatch):
        env.login_as(exp=time.time() - 1)
        env.switcher.add_current()
        refreshes = env.stub_refresh(monkeypatch)
        env.stub_fetch(monkeypatch)

        env.switcher.usage_for("1")
        assert len(refreshes) == 1

    def test_rotated_tokens_are_persisted_before_the_fetch(self, env, monkeypatch):
        """The old refresh token dies the moment the new one is issued, so
        anything between refreshing and saving costs the account."""
        env.login_as(exp=time.time() - 1)
        env.switcher.add_current()
        env.stub_refresh(monkeypatch, new_refresh="rt-rotated")

        saved: list[str] = []

        def fake_fetch(tokens, **kw):
            # Observed AT FETCH TIME: the store must already hold the new token.
            saved.append(env.store.read_credentials("1")["tokens"]["refresh_token"])
            return CodexUsage(allowed=True)

        monkeypatch.setattr(switcher_mod, "fetch_usage", fake_fetch)
        env.switcher.usage_for("1")
        assert saved == ["rt-rotated"]

    def test_the_live_auth_file_is_updated_for_the_active_slot(self, env, monkeypatch):
        # Codex reads the FILE, not our backup; leaving it on the superseded
        # token would make its next request fail.
        env.login_as(exp=time.time() - 1)
        env.switcher.add_current()
        env.stub_refresh(monkeypatch, new_refresh="rt-rotated")
        env.stub_fetch(monkeypatch)

        env.switcher.usage_for("1")
        assert read_auth(env.auth_path)["tokens"]["refresh_token"] == "rt-rotated"

    def test_a_failed_refresh_surfaces_as_a_usage_error(self, env, monkeypatch):
        from claude_swap.codex.tokens import TokenRefreshError

        env.login_as(exp=time.time() - 1)
        env.switcher.add_current()
        monkeypatch.setattr(
            switcher_mod, "refresh_tokens",
            lambda *a, **k: (_ for _ in ()).throw(TokenRefreshError("needs codex login")),
        )
        with pytest.raises(UsageError, match="codex login"):
            env.switcher.usage_for("1")


class TestRefreshSafety:
    def test_never_refreshes_the_account_a_running_codex_is_using(self, env, monkeypatch):
        """Codex's AuthManager refreshes on 401. Racing it means one party
        rotates a token the other just invalidated, and there is no lock."""
        env.login_as(account_id="acct-1", exp=time.time() - 1)
        env.switcher.add_current()
        monkeypatch.setattr(
            switcher_mod, "running_codex_processes",
            lambda: [CodexProcess(1, "/bin/codex", "codex", tty="pts/0")],
        )
        refreshes = env.stub_refresh(monkeypatch)
        env.stub_fetch(monkeypatch)

        env.switcher.usage_for("1")
        assert refreshes == []  # stale token used as-is rather than raced

    def test_an_idle_slot_is_refreshed_even_with_codex_running(self, env, monkeypatch):
        # The running process is on acct-1; slot 2 is nobody's live account.
        env.login_as(account_id="acct-1", email="one@e.com")
        env.switcher.add_current()
        env.login_as(account_id="acct-2", email="two@e.com", exp=time.time() - 1)
        env.switcher.add_current()
        env.login_as(account_id="acct-1", email="one@e.com")  # acct-1 is live again

        monkeypatch.setattr(
            switcher_mod, "running_codex_processes",
            lambda: [CodexProcess(1, "/bin/codex", "codex", tty="pts/0")],
        )
        refreshes = env.stub_refresh(monkeypatch)
        env.stub_fetch(monkeypatch)

        env.switcher.usage_for("2")
        assert len(refreshes) == 1

    def test_a_rejected_token_is_retried_exactly_once(self, env, monkeypatch):
        """Each attempt rotates the token, so this must never become a loop."""
        env.login_as()
        env.switcher.add_current()
        refreshes = env.stub_refresh(monkeypatch)

        attempts = {"n": 0}

        def flaky(tokens, **kw):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise UsageAuthError("HTTP 401")
            return CodexUsage(plan="pro")

        monkeypatch.setattr(switcher_mod, "fetch_usage", flaky)
        assert env.switcher.usage_for("1").plan == "pro"
        assert attempts["n"] == 2
        assert len(refreshes) == 1

    def test_a_rejection_that_cannot_be_refreshed_propagates(self, env, monkeypatch):
        env.login_as(account_id="acct-1")
        env.switcher.add_current()
        monkeypatch.setattr(
            switcher_mod, "running_codex_processes",
            lambda: [CodexProcess(1, "/bin/codex", "codex", tty="pts/0")],
        )
        env.stub_fetch(monkeypatch, error=UsageAuthError("HTTP 401"))
        with pytest.raises(UsageAuthError):
            env.switcher.usage_for("1")


class TestUsageAll:
    def test_one_broken_account_does_not_hide_the_others(self, env, monkeypatch):
        env.login_as(account_id="acct-1", email="one@e.com")
        env.switcher.add_current()
        env.login_as(account_id="acct-2", email="two@e.com")
        env.switcher.add_current()

        def per_account(tokens, **kw):
            if tokens.get("account_id") == "acct-1":
                raise UsageError("boom")
            return CodexUsage(plan="pro")

        monkeypatch.setattr(switcher_mod, "fetch_usage", per_account)
        results = env.switcher.usage_all()
        assert isinstance(results["1"], UsageError)
        assert results["2"].plan == "pro"
