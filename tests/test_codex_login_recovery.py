from __future__ import annotations

import base64
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace

import pytest

from claude_swap import providers
from claude_swap.codex import switcher as switcher_mod
from claude_swap.codex.auth_file import read_auth, write_auth
from claude_swap.codex.identity import OPENAI_AUTH_CLAIM
from claude_swap.codex.processes import CodexProcess
from claude_swap.codex.store import CodexAccountStore
from claude_swap.codex.switcher import CodexSwitcher
from claude_swap.codex.tokens import TokenRefreshError
from claude_swap.codex.usage import CodexUsage, UsageAuthError
from claude_swap.exceptions import SwitchError
from claude_swap.providers import ProviderActionError, ProviderActions, safe_error


def jwt(claims):
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


def login(account="one", refresh="original", *, expired=False):
    return {
        "auth_mode": "chatgpt",
        "tokens": {
            "id_token": jwt({
                "email": f"{account}@example.com",
                OPENAI_AUTH_CLAIM: {"chatgpt_account_id": account},
            }),
            "account_id": account,
            "access_token": jwt({"exp": time.time() + (-60 if expired else 3600)}),
            "refresh_token": refresh,
        },
        "unmodelled": {"preserved": True},
    }


@pytest.fixture
def account_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setattr(switcher_mod, "running_codex_processes", list)
    store = CodexAccountStore(tmp_path / "backups")
    switcher = CodexSwitcher(store)

    def unexpected_refresh(tokens):
        pytest.fail("Unexpected token rotation")

    monkeypatch.setattr(switcher_mod, "refresh_tokens", unexpected_refresh)
    return SimpleNamespace(store=store, switcher=switcher)


@pytest.mark.parametrize("running", [False, True])
def test_usage_adopts_live_rotation_without_reusing_saved_refresh(account_env, monkeypatch, running):
    write_auth(login(expired=True))
    account_env.switcher.add_current()
    latest = login(refresh="rotated-by-codex")
    write_auth(latest)
    if running:
        monkeypatch.setattr(switcher_mod, "running_codex_processes", lambda: [object()])

    def fetch(tokens):
        assert tokens == latest["tokens"]
        return CodexUsage(plan="pro")

    monkeypatch.setattr(switcher_mod, "fetch_usage", fetch)
    assert account_env.switcher.usage_for("1").plan == "pro"
    assert account_env.store.read_credentials("1") == latest


def test_rejected_request_rereads_a_live_rotation_before_refresh(account_env, monkeypatch):
    write_auth(login())
    account_env.switcher.add_current()
    latest = login(refresh="codex-rotated-during-request")
    attempts = []
    monkeypatch.setattr(switcher_mod, "running_codex_processes", lambda: [object()])

    def fetch(tokens):
        attempts.append(tokens)
        if len(attempts) == 1:
            write_auth(latest)
            raise UsageAuthError("HTTP 401")
        assert tokens == latest["tokens"]
        return CodexUsage(plan="pro")

    monkeypatch.setattr(switcher_mod, "fetch_usage", fetch)
    assert account_env.switcher.usage_for("1").plan == "pro"
    assert len(attempts) == 2
    assert account_env.store.read_credentials("1") == latest


def test_live_credentials_never_get_filed_under_another_account(account_env, monkeypatch):
    original = login()
    write_auth(original)
    account_env.switcher.add_current()
    write_auth(login("two", "other-account"))
    account_env.store.set_active("1")

    def fetch(tokens):
        assert tokens == original["tokens"]
        return CodexUsage()

    monkeypatch.setattr(switcher_mod, "fetch_usage", fetch)
    account_env.switcher.usage_for("1")
    assert account_env.store.read_credentials("1") == original


def test_a_reused_slot_is_not_polled_as_its_previous_owner(account_env):
    write_auth(login())
    account = account_env.switcher.add_current()
    previous = replace(account, account_id="previous-owner")
    with pytest.raises(SwitchError, match="account changed"):
        account_env.switcher._call_with_tokens(previous, lambda tokens: pytest.fail("Wrong account"), allow_refresh=True)


def test_concurrent_readers_rotate_once_and_reuse_the_persisted_login(account_env, monkeypatch):
    write_auth(login(expired=True))
    account_env.switcher.add_current()
    rotated = login(refresh="new-refresh")["tokens"]
    calls = []
    start = threading.Barrier(2)

    def refresh(tokens):
        calls.append(tokens["refresh_token"])
        time.sleep(0.1)
        return rotated

    def fetch(tokens):
        assert tokens == rotated
        return CodexUsage(plan="pro")

    def read(_):
        start.wait(timeout=5)
        return CodexSwitcher(account_env.store).usage_for("1")

    monkeypatch.setattr(switcher_mod, "refresh_tokens", refresh)
    monkeypatch.setattr(switcher_mod, "fetch_usage", fetch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(read, range(2)))
    assert calls == ["original"]
    assert all(result.plan == "pro" for result in results)


def test_a_proactive_refresh_is_not_rotated_again_after_rejection(account_env, monkeypatch):
    write_auth(login(expired=True))
    account_env.switcher.add_current()
    calls = []

    def refresh(tokens):
        calls.append(tokens)
        return login(refresh="rotated-once")["tokens"]

    def fetch(tokens):
        raise UsageAuthError("HTTP 401")

    monkeypatch.setattr(switcher_mod, "refresh_tokens", refresh)
    monkeypatch.setattr(switcher_mod, "fetch_usage", fetch)
    with pytest.raises(UsageAuthError):
        account_env.switcher.usage_for("1")
    assert len(calls) == 1


def test_switch_refreshes_an_expired_target_before_replacing_live_login(account_env, monkeypatch):
    write_auth(login(expired=True))
    account_env.switcher.add_current()
    current = login("two")
    write_auth(current)
    account_env.switcher.add_current()
    rotated = login(refresh="refreshed-before-switch")["tokens"]

    def refresh(tokens):
        assert read_auth() == current
        assert tokens["account_id"] == "one"
        return rotated

    monkeypatch.setattr(switcher_mod, "refresh_tokens", refresh)
    result = account_env.switcher.switch_to("1")
    assert result.account.account_id == "one"
    assert read_auth()["tokens"] == rotated
    assert account_env.store.read_credentials("1")["tokens"] == rotated
    assert read_auth()["unmodelled"] == {"preserved": True}
    assert datetime.fromisoformat(read_auth()["last_refresh"]).tzinfo is not None


def test_switch_preserves_current_login_when_target_needs_reauthentication(account_env, monkeypatch):
    write_auth(login(expired=True))
    account_env.switcher.add_current()
    current = login("two")
    write_auth(current)
    account_env.switcher.add_current()

    def refresh(tokens):
        raise TokenRefreshError("Sign in with codex login and add the existing login again")

    monkeypatch.setattr(switcher_mod, "refresh_tokens", refresh)
    with pytest.raises(SwitchError) as error:
        account_env.switcher.switch_to("1")
    assert "codex login" in safe_error(error.value)
    assert read_auth() == current
    assert account_env.store.active_number() == "2"


def test_switch_refuses_credentials_saved_under_the_wrong_account(account_env):
    write_auth(login())
    account_env.switcher.add_current()
    current = login("two")
    write_auth(current)
    account_env.switcher.add_current()
    account_env.store.write_credentials("1", current)
    with pytest.raises(SwitchError, match="mismatched"):
        account_env.switcher.switch_to("1")
    assert read_auth() == current
    assert account_env.store.active_number() == "2"


def test_refresh_does_not_overwrite_a_newer_external_login(account_env, monkeypatch):
    write_auth(login(expired=True))
    account_env.switcher.add_current()
    latest = login(refresh="external-newest")

    def refresh(tokens):
        write_auth(latest)
        return login(refresh="our-rotation")["tokens"]

    monkeypatch.setattr(switcher_mod, "refresh_tokens", refresh)
    monkeypatch.setattr(switcher_mod, "fetch_usage", lambda tokens: CodexUsage())
    account_env.switcher.usage_for("1")
    assert read_auth() == latest


def test_a_failed_live_write_does_not_erase_the_saved_rotation(account_env, monkeypatch):
    original = {**login(expired=True), "last_refresh": "2020-01-01T00:00:00Z"}
    write_auth(original)
    account_env.switcher.add_current()
    rotated = login(refresh="preserved-rotation")["tokens"]
    monkeypatch.setattr(switcher_mod, "refresh_tokens", lambda tokens: rotated)

    def fail_write(auth):
        raise OSError("Live credential file is temporarily unavailable")

    monkeypatch.setattr(switcher_mod, "write_auth", fail_write)
    with pytest.raises(OSError):
        account_env.switcher.usage_for("1")
    assert account_env.store.read_credentials("1")["tokens"] == rotated
    assert read_auth() == original

    def fetch(tokens):
        assert tokens == rotated
        return CodexUsage()

    monkeypatch.setattr(switcher_mod, "fetch_usage", fetch)
    account_env.switcher.usage_for("1")
    assert account_env.store.read_credentials("1")["tokens"] == rotated


def test_switching_away_keeps_a_newer_saved_rotation(account_env):
    original = {**login(), "last_refresh": "2020-01-01T00:00:00Z"}
    write_auth(original)
    account_env.switcher.add_current()
    write_auth(login("two"))
    account_env.switcher.add_current()
    rotated = {**login(refresh="newest"), "last_refresh": "2021-01-01T00:00:00Z"}
    account_env.store.write_credentials("1", rotated)
    write_auth(original)
    result = account_env.switcher.switch_to("2")
    assert not result.synced_back
    assert account_env.store.read_credentials("1") == rotated
    account_env.switcher.switch_to("1")
    assert read_auth() == rotated


def test_ui_switch_refuses_running_codex_without_altering_login(account_env, monkeypatch):
    write_auth(login())
    account_env.switcher.add_current()
    current = login("two")
    write_auth(current)
    account_env.switcher.add_current()
    monkeypatch.setattr(providers, "running_codex_processes", lambda: [
        CodexProcess(123, "/Applications/Codex.app/Contents/Resources/codex", "codex app-server"),
    ], raising=False)
    actions = ProviderActions(codex=account_env.switcher)
    try:
        with pytest.raises(ProviderActionError, match="Quit Codex"):
            actions.switch("codex", "1")
    finally:
        actions.close()
    assert read_auth() == current
    assert account_env.store.active_number() == "2"
