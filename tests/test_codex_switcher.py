"""Tests for switching the active Codex account.

Most of these guard a way of losing a credential rather than a feature:
rotation going unsaved, tokens being misfiled against the wrong slot, and an
unmanaged login being silently discarded.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from claude_swap.codex import switcher as switcher_mod
from claude_swap.codex.auth_file import read_auth, write_auth
from claude_swap.codex.identity import OPENAI_AUTH_CLAIM
from claude_swap.codex.processes import CodexProcess
from claude_swap.codex.store import CodexAccountStore
from claude_swap.codex.switcher import CodexSwitcher
from claude_swap.exceptions import AccountNotFoundError, SwitchError, ValidationError


def id_token(account_id: str, email: str, plan: str = "pro") -> str:
    claims = {
        "email": email,
        OPENAI_AUTH_CLAIM: {"chatgpt_account_id": account_id, "chatgpt_plan_type": plan},
    }
    payload = base64.urlsafe_b64encode(
        json.dumps(claims).encode()
    ).decode().rstrip("=")
    return f"hdr.{payload}.sig"


def auth_for(account_id="acct-1", email="one@example.com", refresh="rt-v1") -> dict:
    return {
        "OPENAI_API_KEY": None,
        "auth_mode": "chatgpt",
        "tokens": {
            "id_token": id_token(account_id, email),
            "access_token": f"at-{refresh}",
            "refresh_token": refresh,
            "account_id": account_id,
        },
    }


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    """An isolated CODEX_HOME plus an isolated backup store."""
    home = tmp_path / "codexhome"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setattr(switcher_mod, "running_codex_processes", lambda: [])

    class Env:
        auth_path = home / "auth.json"
        store = CodexAccountStore(root=tmp_path / "backup")

        def __init__(self):
            self.switcher = CodexSwitcher(self.store)

        def login_as(self, **kw):
            write_auth(auth_for(**kw), self.auth_path)

        def logout(self):
            self.auth_path.unlink(missing_ok=True)

    return Env()


class TestStatus:
    def test_logged_out(self, env):
        assert env.switcher.status().logged_in is False

    def test_logged_in_but_unmanaged(self, env):
        env.login_as()
        status = env.switcher.status()
        assert status.logged_in is True
        assert status.is_managed is False
        assert status.identity.email == "one@example.com"

    def test_logged_in_and_managed(self, env):
        env.login_as()
        env.switcher.add_current()
        status = env.switcher.status()
        assert status.is_managed is True
        assert status.account.number == "1"


class TestAddCurrent:
    def test_captures_the_live_login(self, env):
        env.login_as(email="a@example.com")
        account = env.switcher.add_current(alias="work")
        assert account.number == "1"
        assert account.email == "a@example.com"
        assert account.alias == "work"
        assert env.store.read_credentials("1")["tokens"]["refresh_token"] == "rt-v1"

    def test_the_captured_slot_becomes_active(self, env):
        # It IS the live credential; recording anything else would make the very
        # next switch sync-back into the wrong slot.
        env.login_as()
        env.switcher.add_current()
        assert env.store.active_number() == "1"

    def test_refuses_when_logged_out(self, env):
        with pytest.raises(ValidationError):
            env.switcher.add_current()

    def test_refuses_an_api_key_login(self, env):
        write_auth({"OPENAI_API_KEY": "sk-test"}, env.auth_path)
        with pytest.raises(ValidationError, match="API-key"):
            env.switcher.add_current()

    def test_refuses_a_duplicate_account(self, env):
        env.login_as(account_id="acct-1")
        env.switcher.add_current()
        with pytest.raises(SwitchError, match="already managed"):
            env.switcher.add_current()


class TestSwitching:
    @pytest.fixture
    def two_accounts(self, env):
        env.login_as(account_id="acct-1", email="one@example.com")
        env.switcher.add_current()
        env.login_as(account_id="acct-2", email="two@example.com")
        env.switcher.add_current()
        return env

    def test_switch_installs_the_targets_credential(self, two_accounts):
        result = two_accounts.switcher.switch_to("1")
        assert result.account.email == "one@example.com"
        live = read_auth(two_accounts.auth_path)
        assert live["tokens"]["account_id"] == "acct-1"
        assert two_accounts.store.active_number() == "1"

    def test_switch_preserves_unmodelled_fields(self, two_accounts):
        two_accounts.switcher.switch_to("1")
        assert read_auth(two_accounts.auth_path)["auth_mode"] == "chatgpt"

    def test_rotated_tokens_are_saved_before_switching_away(self, two_accounts):
        """The reason switching is reversible.

        Codex rotates refresh tokens, so the copy saved when a slot was added
        goes stale as soon as that account is used. Without sync-back, switching
        back restores a superseded token and the account looks dead.
        """
        # Account 2 is live and has since rotated its refresh token.
        two_accounts.login_as(account_id="acct-2", email="two@example.com", refresh="rt-v2")

        result = two_accounts.switcher.switch_to("1")
        assert result.synced_back is True
        assert two_accounts.store.read_credentials("2")["tokens"]["refresh_token"] == "rt-v2"

    def test_sync_back_targets_the_account_that_owns_the_tokens(self, two_accounts):
        # Not merely "the active slot": the live credential decides where it is
        # filed, which is what stops one account's tokens landing in another's.
        two_accounts.store.set_active("1")  # roster disagrees with reality
        two_accounts.login_as(account_id="acct-2", email="two@example.com", refresh="rt-v9")
        two_accounts.switcher.switch_to("1")
        assert two_accounts.store.read_credentials("2")["tokens"]["refresh_token"] == "rt-v9"
        assert two_accounts.store.read_credentials("1")["tokens"]["refresh_token"] == "rt-v1"

    def test_an_unmanaged_live_login_is_not_discarded(self, two_accounts):
        two_accounts.login_as(account_id="acct-stranger", email="x@example.com")
        with pytest.raises(SwitchError, match="not managed"):
            two_accounts.switcher.switch_to("1")
        # And the login is still there, untouched.
        assert read_auth(two_accounts.auth_path)["tokens"]["account_id"] == "acct-stranger"

    def test_force_discards_an_unmanaged_login(self, two_accounts):
        two_accounts.login_as(account_id="acct-stranger", email="x@example.com")
        result = two_accounts.switcher.switch_to("1", force=True)
        assert result.synced_back is False
        assert read_auth(two_accounts.auth_path)["tokens"]["account_id"] == "acct-1"

    def test_switching_from_logged_out_needs_no_force(self, two_accounts):
        two_accounts.logout()
        result = two_accounts.switcher.switch_to("1")
        assert result.synced_back is False
        assert read_auth(two_accounts.auth_path)["tokens"]["account_id"] == "acct-1"

    def test_refuses_to_switch_to_the_active_account(self, two_accounts):
        with pytest.raises(SwitchError, match="already the active"):
            two_accounts.switcher.switch_to("2")

    def test_refuses_a_slot_with_no_saved_credential(self, two_accounts):
        two_accounts.store.delete_credentials("1")
        with pytest.raises(SwitchError, match="no saved credential"):
            two_accounts.switcher.switch_to("1")

    def test_unknown_identifier_raises(self, two_accounts):
        with pytest.raises(AccountNotFoundError):
            two_accounts.switcher.switch_to("nope")


class TestRestartReporting:
    def test_a_running_codex_means_a_restart_is_required(self, env, monkeypatch):
        """Codex caches auth in-process and its reload refuses to cross account
        ids, so reporting success without this would be a lie."""
        env.login_as(account_id="acct-1")
        env.switcher.add_current()
        env.login_as(account_id="acct-2", email="two@example.com")
        env.switcher.add_current()
        monkeypatch.setattr(
            switcher_mod,
            "running_codex_processes",
            lambda: [CodexProcess(99, "/bin/codex", "codex", tty="pts/2")],
        )
        result = env.switcher.switch_to("1")
        assert result.restart_required is True
        assert result.processes[0].pid == 99

    def test_no_running_codex_means_no_restart(self, env):
        env.login_as(account_id="acct-1")
        env.switcher.add_current()
        env.login_as(account_id="acct-2", email="two@example.com")
        env.switcher.add_current()
        assert env.switcher.switch_to("1").restart_required is False


class TestResolution:
    @pytest.fixture
    def populated(self, env):
        env.login_as(account_id="acct-1", email="one@example.com")
        env.switcher.add_current(alias="work")
        return env

    def test_by_slot_number(self, populated):
        assert populated.switcher.resolve("1").account_id == "acct-1"

    def test_by_alias_case_insensitively(self, populated):
        assert populated.switcher.resolve("WORK").account_id == "acct-1"

    def test_by_email_case_insensitively(self, populated):
        assert populated.switcher.resolve("One@Example.com").account_id == "acct-1"

    def test_unknown_raises(self, populated):
        with pytest.raises(AccountNotFoundError):
            populated.switcher.resolve("ghost")


class TestAliasesAndRemoval:
    @pytest.fixture
    def populated(self, env):
        env.login_as(account_id="acct-1", email="one@example.com")
        env.switcher.add_current()
        env.login_as(account_id="acct-2", email="two@example.com")
        env.switcher.add_current()
        return env

    def test_set_alias(self, populated):
        assert populated.switcher.set_alias("1", "personal").alias == "personal"
        assert populated.switcher.resolve("personal").number == "1"

    def test_duplicate_alias_rejected(self, populated):
        populated.switcher.set_alias("1", "work")
        with pytest.raises(ValidationError, match="already belongs"):
            populated.switcher.set_alias("2", "work")

    def test_invalid_alias_rejected(self, populated):
        with pytest.raises(ValueError):
            populated.switcher.set_alias("1", "123")  # would collide with a slot

    def test_remove_drops_the_account_and_its_credential(self, populated):
        removed = populated.switcher.remove_account("1")
        assert removed.account_id == "acct-1"
        assert populated.store.read_credentials("1") is None
        with pytest.raises(AccountNotFoundError):
            populated.switcher.resolve("1")
