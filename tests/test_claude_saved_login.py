"""Explicit Claude login recovery using only synthetic credentials."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agents_switcher import oauth
from agents_switcher.exceptions import ConfigError, CredentialReadError, LockError
from agents_switcher.locking import FileLock
from agents_switcher.switcher import ClaudeAccountSwitcher
from agents_switcher.usage_store import FetchRecord


@pytest.fixture
def recovery(temp_home, monkeypatch):
    switcher = ClaudeAccountSwitcher()
    switcher._setup_directories()
    identity = ("recover@example.invalid", "org-one")
    old = json.dumps({"claudeAiOauth": {"accessToken": "old", "refreshToken": "old-rt"}})
    fresh = json.dumps({"claudeAiOauth": {"accessToken": "new", "refreshToken": "new-rt"}})
    account = {"email": identity[0], "organizationUuid": identity[1],
               "organizationName": "Example", "uuid": "user-one", "alias": "Work", "disabled": True}
    switcher._write_json(switcher.sequence_file, {
        "sequence": [1], "accounts": {"1": account}, "activeAccountNumber": 1,
    })
    config = {"oauthAccount": {"emailAddress": identity[0], "organizationUuid": identity[1],
                               "accountUuid": "user-one"}}
    switcher._get_claude_config_path().write_text(json.dumps(config))
    switcher._write_account_credentials("1", identity[0], old)
    switcher._usage_store.record({"1": FetchRecord(
        error="invalid_grant", struck_fp=oauth.credential_fingerprint(old),
    )}, {"1": identity})
    monkeypatch.setattr(switcher, "_read_capture_credentials", lambda: fresh)
    monkeypatch.setattr(oauth, "fetch_oauth_profile", lambda token: {
        "email": identity[0], "organizationUuid": identity[1], "uuid": "user-one",
    })
    monkeypatch.setattr(switcher, "_write_credentials", Mock(side_effect=AssertionError("live write")))
    return SimpleNamespace(switcher=switcher, identity=identity, old=old, fresh=fresh, config=config)


def test_update_saved_login_recovers_existing_slot_without_changing_live_login(recovery):
    s = recovery.switcher
    before = s._get_sequence_data()["accounts"]
    assert s._usage_store.entries({"1": recovery.identity})["1"].token_dead()
    s.update_saved_login("1")
    assert s._read_account_credentials("1", recovery.identity[0]) == recovery.fresh
    assert s._get_sequence_data()["accounts"] == before
    assert s._get_sequence_data()["sequence"] == [1]
    assert not s._usage_store.entries({"1": recovery.identity})["1"].token_dead()
    assert s._usage_store.entries({"1": recovery.identity})["1"].last_error is None
    s._write_credentials.assert_not_called()


@pytest.mark.parametrize("change", ["email", "org", "uuid", "missing", "removed"])
def test_update_refuses_wrong_or_missing_identity_without_saving(recovery, change):
    s = recovery.switcher
    if change == "removed":
        s._write_json(s.sequence_file, {"sequence": [], "accounts": {}})
    elif change == "missing":
        s._get_claude_config_path().write_text("{}")
    else:
        field = {"email": "emailAddress", "org": "organizationUuid", "uuid": "accountUuid"}[change]
        recovery.config["oauthAccount"][field] = "different"
        s._get_claude_config_path().write_text(json.dumps(recovery.config))
    with pytest.raises(ConfigError):
        s.update_saved_login("1")
    assert s._read_account_credentials("1", recovery.identity[0]) == recovery.old
    assert s._usage_store.entries({"1": recovery.identity})["1"].token_dead()


def test_update_refuses_login_changed_during_capture(recovery, monkeypatch):
    s = recovery.switcher

    def changed(token):
        recovery.config["oauthAccount"]["emailAddress"] = "other@example.invalid"
        s._get_claude_config_path().write_text(json.dumps(recovery.config))
        return {"email": recovery.identity[0], "organizationUuid": recovery.identity[1], "uuid": "user-one"}

    monkeypatch.setattr(oauth, "fetch_oauth_profile", changed)
    with pytest.raises(ConfigError, match="active account changed"):
        s.update_saved_login("1")
    assert s._read_account_credentials("1", recovery.identity[0]) == recovery.old
    assert s._usage_store.entries({"1": recovery.identity})["1"].token_dead()


def test_failed_credential_write_preserves_quarantine(recovery, monkeypatch):
    s = recovery.switcher
    monkeypatch.setattr(s, "_write_account_credentials", Mock(side_effect=CredentialReadError("Cannot save")))
    with pytest.raises(CredentialReadError):
        s.update_saved_login("1")
    assert s._usage_store.entries({"1": recovery.identity})["1"].token_dead()


def test_update_refuses_the_same_rejected_login(recovery, monkeypatch):
    s = recovery.switcher
    monkeypatch.setattr(s, "_read_capture_credentials", lambda: recovery.old)
    with pytest.raises(ConfigError, match="still has the rejected login"):
        s.update_saved_login("1")
    assert s._read_account_credentials("1", recovery.identity[0]) == recovery.old
    assert s._usage_store.entries({"1": recovery.identity})["1"].token_dead()


@pytest.mark.parametrize("credentials", [None, ""])
def test_update_refuses_unreadable_or_absent_credentials(recovery, monkeypatch, credentials):
    s = recovery.switcher
    monkeypatch.setattr(s, "_read_capture_credentials", lambda: credentials)
    with pytest.raises(CredentialReadError):
        s.update_saved_login("1")
    assert s._read_account_credentials("1", recovery.identity[0]) == recovery.old
    assert s._usage_store.entries({"1": recovery.identity})["1"].token_dead()


def test_update_refuses_foreign_token_even_when_config_matches(recovery, monkeypatch):
    s = recovery.switcher
    monkeypatch.setattr(oauth, "fetch_oauth_profile", lambda token: {
        "uuid": "foreign-user", "email": "foreign@example.invalid", "organizationUuid": "org-one",
    })
    with pytest.raises(ConfigError, match="does not belong"):
        s.update_saved_login("1")
    assert s._read_account_credentials("1", recovery.identity[0]) == recovery.old
    assert s._usage_store.entries({"1": recovery.identity})["1"].token_dead()


def test_update_holds_cross_node_account_lock_during_capture(recovery, monkeypatch):
    s = recovery.switcher

    def capture():
        with pytest.raises(LockError):
            with FileLock(s.lock_file, timeout=0):
                pass
        return recovery.fresh

    monkeypatch.setattr(s, "_read_capture_credentials", capture)
    s.update_saved_login("1")


@pytest.mark.parametrize("fresh", [False, True])
def test_legacy_quarantine_requires_a_changed_saved_login(recovery, monkeypatch, fresh):
    s = recovery.switcher
    s._usage_store.record({"1": FetchRecord(error="invalid_grant")}, {"1": recovery.identity})
    assert s._usage_store.entries({"1": recovery.identity})["1"].struck_fingerprint is None
    monkeypatch.setattr(s, "_read_capture_credentials", lambda: recovery.fresh if fresh else recovery.old)
    if fresh:
        s.update_saved_login("1")
        assert s._read_account_credentials("1", recovery.identity[0]) == recovery.fresh
        assert not s._usage_store.entries({"1": recovery.identity})["1"].token_dead()
    else:
        with pytest.raises(ConfigError, match="still has the rejected login"):
            s.update_saved_login("1")
        assert s._read_account_credentials("1", recovery.identity[0]) == recovery.old
        assert s._usage_store.entries({"1": recovery.identity})["1"].token_dead()


def test_legacy_quarantine_with_unreadable_backup_cannot_be_cleared(recovery, monkeypatch):
    s = recovery.switcher
    s._usage_store.record({"1": FetchRecord(error="invalid_grant")}, {"1": recovery.identity})
    monkeypatch.setattr(s, "_read_account_credentials_ex", lambda *args: ("", True))
    with pytest.raises(CredentialReadError, match="Could not verify"):
        s.update_saved_login("1")
    assert s._usage_store.entries({"1": recovery.identity})["1"].token_dead()
