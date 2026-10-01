from __future__ import annotations

import pytest

from agents_switcher.codex import switcher as switcher_mod
from agents_switcher.codex.auth_file import read_auth, write_auth
from agents_switcher.codex.tokens import LoginRequiredError, TokenRefreshError
from agents_switcher.codex.usage import CodexUsage, UsageAuthError, UsageLoginRequiredError
from tests.test_codex_login_recovery import account_env, login


def test_adding_another_account_does_not_rotate_a_healthy_opaque_login(account_env, monkeypatch):
    original = login("one")
    original["tokens"]["access_token"] = "synthetic-opaque-access"
    write_auth(original)
    account_env.switcher.add_current()
    current = login("two")
    write_auth(current)
    account_env.switcher.add_current()
    requests = []

    def fetch(tokens):
        requests.append(tokens)
        return CodexUsage(plan="pro")

    monkeypatch.setattr(switcher_mod, "fetch_usage", fetch)
    assert account_env.switcher.usage_for("1").plan == "pro"
    assert requests == [original["tokens"]]
    assert account_env.store.read_credentials("1") == original
    assert read_auth() == current


def test_opaque_login_refreshes_only_after_rejection_and_keeps_the_replacement(account_env, monkeypatch):
    original = login()
    original["tokens"]["access_token"] = "synthetic-old-opaque-access"
    write_auth(original)
    account_env.switcher.add_current()
    rotated = {**original["tokens"], "access_token": "synthetic-new-opaque-access", "refresh_token": "synthetic-rotated-refresh"}
    requests = []
    refreshes = []

    def fetch(tokens):
        requests.append(tokens)
        if tokens == original["tokens"]:
            raise UsageAuthError("HTTP 401")
        return CodexUsage(plan="pro")

    def refresh(tokens):
        refreshes.append(tokens)
        assert requests == [original["tokens"]]
        return rotated

    monkeypatch.setattr(switcher_mod, "fetch_usage", fetch)
    monkeypatch.setattr(switcher_mod, "refresh_tokens", refresh)
    assert account_env.switcher.usage_for("1").plan == "pro"
    assert account_env.switcher.usage_for("1").plan == "pro"
    assert refreshes == [original["tokens"]]
    assert requests == [original["tokens"], rotated, rotated]
    assert account_env.store.read_credentials("1")["tokens"] == rotated
    assert read_auth()["tokens"] == rotated


@pytest.mark.parametrize("permanent", [True, False])
def test_refresh_recovery_classifies_revoked_login_without_reclassifying_network_failure(account_env, monkeypatch, permanent):
    original = login(expired=True)
    write_auth(original)
    account_env.switcher.add_current()
    attempts = []

    def refresh(tokens):
        attempts.append(tokens)
        if permanent:
            raise LoginRequiredError("Sign in again to repair this login.")
        raise TokenRefreshError("Connection failed; retry later.")

    monkeypatch.setattr(switcher_mod, "refresh_tokens", refresh)
    error = account_env.switcher.usage_all()["1"]
    assert isinstance(error, UsageLoginRequiredError) is permanent
    assert isinstance(error, Exception)
    assert attempts == [original["tokens"]]
    assert account_env.store.read_credentials("1") == original
    assert read_auth() == original
