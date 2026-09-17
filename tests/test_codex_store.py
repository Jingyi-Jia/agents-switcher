"""Tests for the Codex account roster and credential backups."""

from __future__ import annotations

import base64
import json
import stat
import sys
from pathlib import Path

import pytest

from claude_swap.codex import store as store_mod
from claude_swap.codex.identity import CodexIdentity
from claude_swap.codex.store import CodexAccount, CodexAccountStore
from claude_swap.exceptions import ConfigError

CREDS = {
    "OPENAI_API_KEY": None,
    "auth_mode": "chatgpt",
    "tokens": {"id_token": "a.b.c", "access_token": "at", "refresh_token": "rt"},
}


def ident(account_id="acct-1", email="one@example.com", plan="pro") -> CodexIdentity:
    return CodexIdentity(email=email, account_id=account_id, plan=plan)


@pytest.fixture
def store(tmp_path: Path) -> CodexAccountStore:
    return CodexAccountStore(root=tmp_path / "codex")


class TestRosterReading:
    def test_missing_roster_is_empty_not_an_error(self, store):
        assert store.accounts() == {}
        assert store.active_number() is None

    def test_torn_roster_raises_rather_than_reading_as_empty(self, store):
        # The Claude side's measured incident: a torn roster read as "no
        # accounts", and the next add rebuilt it from nothing over a live
        # credential backup. Raising is what prevents that.
        store.root.mkdir(parents=True)
        store.accounts_file.write_text('{"accounts": {"1": ')
        with pytest.raises(ConfigError):
            store.accounts()

    def test_non_object_roster_raises(self, store):
        store.root.mkdir(parents=True)
        store.accounts_file.write_text("[]")
        with pytest.raises(ConfigError):
            store.accounts()

    def test_malformed_accounts_map_raises(self, store):
        store.root.mkdir(parents=True)
        store.accounts_file.write_text('{"accounts": "not a map"}')
        with pytest.raises(ConfigError):
            store.accounts()


class TestAddingAccounts:
    def test_add_assigns_the_first_slot_and_saves_the_credential(self, store):
        account = store.add(ident(), CREDS)
        assert account.number == "1"
        assert account.email == "one@example.com"
        assert account.plan == "pro"
        assert account.added  # timestamped
        assert store.read_credentials("1") == CREDS

    def test_slots_increment(self, store):
        store.add(ident("acct-1"), CREDS)
        second = store.add(ident("acct-2", "two@example.com"), CREDS)
        assert second.number == "2"
        assert sorted(store.accounts()) == ["1", "2"]

    def test_explicit_slot_is_honoured(self, store):
        assert store.add(ident(), CREDS, number="7").number == "7"

    def test_alias_is_recorded(self, store):
        assert store.add(ident(), CREDS, alias="work").alias == "work"

    def test_next_number_reuses_a_gap(self, store):
        for i in (1, 2, 3):
            store.add(ident(f"acct-{i}", f"{i}@example.com"), CREDS)
        store.remove("2")
        # Appending instead would leave the roster permanently sparse.
        assert store.next_number() == "2"

    def test_accounts_sort_numerically_not_lexically(self, store):
        for i in range(1, 11):
            store.add(ident(f"acct-{i}", f"{i}@e.com"), CREDS, number=str(i))
        assert list(store.accounts())[-1] == "10"  # not "9"


class TestLookup:
    def test_find_by_account_id(self, store):
        store.add(ident("acct-1"), CREDS)
        store.add(ident("acct-2", "two@example.com"), CREDS)
        assert store.find_by_account_id("acct-2").email == "two@example.com"

    def test_find_by_unknown_account_id_is_none(self, store):
        store.add(ident("acct-1"), CREDS)
        assert store.find_by_account_id("acct-absent") is None

    def test_empty_account_id_never_matches(self, store):
        # "Unknown identity" must not match a slot whose id is also unknown --
        # that is how one account's credential gets filed under another.
        store.add(CodexIdentity(email="x@e.com", account_id="", plan=""), CREDS)
        assert store.find_by_account_id("") is None

    def test_get_missing_slot_is_none(self, store):
        assert store.get("9") is None


class TestActiveSlot:
    def test_set_and_read(self, store):
        store.add(ident(), CREDS)
        store.set_active("1")
        assert store.active_number() == "1"

    def test_removing_the_active_slot_clears_it(self, store):
        store.add(ident(), CREDS)
        store.set_active("1")
        store.remove("1")
        assert store.active_number() is None

    def test_removing_another_slot_leaves_active_alone(self, store):
        store.add(ident("acct-1"), CREDS)
        store.add(ident("acct-2", "two@e.com"), CREDS)
        store.set_active("1")
        store.remove("2")
        assert store.active_number() == "1"


class TestRemovalAndUpdate:
    def test_remove_returns_the_account_and_drops_the_backup(self, store):
        store.add(ident(), CREDS)
        removed = store.remove("1")
        assert removed.account_id == "acct-1"
        assert store.accounts() == {}
        assert store.read_credentials("1") is None

    def test_remove_missing_slot_is_none(self, store):
        assert store.remove("3") is None

    def test_update_persists_changes(self, store):
        account = store.add(ident(), CREDS)
        store.update(CodexAccount(**{**account.__dict__, "disabled": True, "alias": "rest"}))
        reloaded = store.get("1")
        assert reloaded.disabled is True
        assert reloaded.alias == "rest"

    def test_update_unknown_slot_raises(self, store):
        with pytest.raises(ConfigError):
            store.update(CodexAccount(number="4", email="x@e.com", account_id="a"))


class TestCredentialBackups:
    def test_round_trips_unmodelled_fields(self, store):
        payload = {**CREDS, "some_future_key": [1, 2]}
        store.write_credentials("1", payload)
        assert store.read_credentials("1") == payload

    def test_missing_backup_is_none(self, store):
        assert store.read_credentials("1") is None

    def test_corrupt_backup_raises_rather_than_reading_as_absent(self, store):
        # "No backup" and "unreadable backup" lead to opposite actions; the
        # former invites re-adding over a credential that is still recoverable.
        store.credentials_dir.mkdir(parents=True)
        store.credentials_path("1").write_bytes(b"not base64 at all !!")
        with pytest.raises(ConfigError):
            store.read_credentials("1")

    def test_backup_of_non_object_raises(self, store):
        store.credentials_dir.mkdir(parents=True)
        store.credentials_path("1").write_bytes(base64.b64encode(b'["a"]'))
        with pytest.raises(ConfigError):
            store.read_credentials("1")

    def test_not_stored_as_plaintext(self, store):
        # Obscurity, not encryption -- but a token must not sit in the clear
        # where a grep or a scrollback would surface it.
        store.write_credentials("1", CREDS)
        raw = store.credentials_path("1").read_bytes()
        assert b"access_token" not in raw
        assert json.loads(base64.b64decode(raw)) == CREDS

    def test_rejects_non_dict(self, store):
        with pytest.raises(TypeError):
            store.write_credentials("1", ["nope"])

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission model")
    def test_permissions_are_owner_only(self, store):
        store.write_credentials("1", CREDS)
        assert stat.S_IMODE(store.credentials_path("1").stat().st_mode) == 0o600
        assert stat.S_IMODE(store.credentials_dir.stat().st_mode) == 0o700

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission model")
    def test_roster_is_owner_only(self, store):
        store.add(ident(), CREDS)
        assert stat.S_IMODE(store.accounts_file.stat().st_mode) == 0o600

    def test_leaves_no_temporary_files(self, store):
        store.add(ident(), CREDS)
        assert not [p for p in store.root.rglob("*.tmp")]

    def test_failed_backup_write_preserves_the_previous_one(self, store, monkeypatch):
        store.write_credentials("1", CREDS)

        def boom(*a, **kw):
            raise OSError("simulated I/O failure")

        monkeypatch.setattr(store_mod, "replace_with_retry", boom)
        with pytest.raises(OSError):
            store.write_credentials("1", {"tokens": {"access_token": "new"}})
        assert store.read_credentials("1") == CREDS
        assert not [p for p in store.credentials_dir.glob("*.tmp")]


class TestLocking:
    def test_lock_is_a_directory_not_a_file(self, store):
        """mkdir is the mutex. A file lock here would be fcntl.flock, which
        gives NO cross-node exclusion on an NFS home mounted local_lock=all --
        measured at 2653 violations in 2829 acquisitions."""
        with store.lock():
            assert store.lock_dir.is_dir()
        assert not store.lock_dir.exists()

    def test_lock_excludes_a_second_holder(self, store):
        from claude_swap.exceptions import LockError

        with store.lock():
            with pytest.raises(LockError):
                with store.lock(timeout=0.3):
                    pass

    def test_lock_is_released_on_exception(self, store):
        with pytest.raises(RuntimeError):
            with store.lock():
                raise RuntimeError("boom")
        assert not store.lock_dir.exists()
        with store.lock():  # reacquirable
            pass
