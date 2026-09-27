"""Historical stores are touched only by the explicit copy importer."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from agents_switcher.credentials import SECURITY_SERVICE
from agents_switcher.migrations import LEGACY_KEYRING_SERVICE, LEGACY_SECURITY_SERVICE
from agents_switcher.models import Platform
from agents_switcher.paths import get_backup_root, get_legacy_backup_root, get_legacy_xdg_backup_root
from agents_switcher.switcher import ClaudeAccountSwitcher


@pytest.mark.parametrize("platform", list(Platform))
def test_startup_never_reads_or_moves_legacy_stores(monkeypatch, block_real_keychain, platform):
    monkeypatch.setattr(Platform, "detect", staticmethod(lambda: platform))
    for root in (get_legacy_backup_root(), get_legacy_xdg_backup_root()):
        root.mkdir(parents=True, exist_ok=True)
        (root / "sequence.json").write_text("not even valid JSON")
        (root / "credentials").mkdir()
        (root / "credentials/sentinel").write_text("unchanged")
    block_real_keychain.set_password(LEGACY_SECURITY_SERVICE, "account-1-user@example.test", "old")
    keyring = sys.modules["keyring"]
    keyring.set_password(LEGACY_KEYRING_SERVICE, "account-1-user@example.test", "old-keyring")
    before = dict(block_real_keychain.data)
    monkeypatch.setattr(keyring, "get_password", lambda *a: pytest.fail("implicit Keyring read"))
    monkeypatch.setattr(keyring, "delete_password", lambda *a: pytest.fail("implicit Keyring delete"))
    switcher = ClaudeAccountSwitcher()
    assert switcher.backup_dir == get_backup_root()
    assert not switcher.backup_dir.exists()
    assert block_real_keychain.data == before
    for root in (get_legacy_backup_root(), get_legacy_xdg_backup_root()):
        assert (root / "sequence.json").read_text() == "not even valid JSON"
        assert (root / "credentials/sentinel").read_text() == "unchanged"


@pytest.mark.parametrize("platform", [Platform.LINUX, Platform.WSL, Platform.WINDOWS, Platform.MACOS])
def test_remove_and_purge_leave_legacy_files_and_services_unchanged(monkeypatch, block_real_keychain, platform):
    monkeypatch.setattr(Platform, "detect", staticmethod(lambda: platform))
    username = "account-1-user@example.test"
    for service in (LEGACY_SECURITY_SERVICE, LEGACY_KEYRING_SERVICE):
        block_real_keychain.set_password(service, username, "old")
    keyring = sys.modules["keyring"]
    keyring.set_password(LEGACY_KEYRING_SERVICE, username, "old-keyring")
    for root in (get_legacy_backup_root(), get_legacy_xdg_backup_root()):
        root.mkdir(parents=True, exist_ok=True)
        (root / "sentinel").write_text("leave upstream alone")
    switcher = ClaudeAccountSwitcher()
    switcher._setup_directories()
    switcher._write_account_credentials("1", "user@example.test", "synthetic")
    switcher._write_account_config("1", "user@example.test", "{}")
    switcher.sequence_file.write_text(json.dumps({
        "accounts": {"1": {"email": "user@example.test", "organizationUuid": ""}},
        "sequence": [1], "activeAccountNumber": 1,
    }))
    switcher.remove_account("1", assume_yes=True)
    switcher._write_account_credentials("1", "user@example.test", "synthetic")
    switcher.sequence_file.write_text(json.dumps({
        "accounts": {"1": {"email": "user@example.test", "organizationUuid": ""}},
        "sequence": [1], "activeAccountNumber": 1,
    }))
    monkeypatch.setattr("builtins.input", lambda _: "y")
    switcher.purge()
    assert not switcher.backup_dir.exists()
    for service in (LEGACY_SECURITY_SERVICE, LEGACY_KEYRING_SERVICE):
        assert block_real_keychain.get_password(service, username) == "old"
    assert keyring.get_password(LEGACY_KEYRING_SERVICE, username) == "old-keyring"
    assert block_real_keychain.get_password(SECURITY_SERVICE, username) is None
    for root in (get_legacy_backup_root(), get_legacy_xdg_backup_root()):
        assert (root / "sentinel").read_text() == "leave upstream alone"


def test_windows_file_backend_never_uses_keyring(monkeypatch):
    monkeypatch.setattr(Platform, "detect", staticmethod(lambda: Platform.WINDOWS))
    keyring = sys.modules["keyring"]
    for operation in ("get_password", "set_password", "delete_password"):
        monkeypatch.setattr(keyring, operation, lambda *a: pytest.fail("implicit Keyring operation"))
    switcher = ClaudeAccountSwitcher()
    switcher._setup_directories()
    assert switcher._uses_file_backup_backend()
    switcher._write_account_credentials("1", "user@example.test", "synthetic")
    assert switcher._read_account_credentials("1", "user@example.test") == "synthetic"
    switcher._delete_account_credentials("1", "user@example.test")
    assert switcher._read_account_credentials("1", "user@example.test") == ""


def test_stale_historical_migration_flag_does_not_trigger_deletion():
    source = get_legacy_backup_root()
    source.mkdir()
    (source / "sentinel").write_text("source")
    destination = get_backup_root()
    destination.mkdir(parents=True)
    (destination / "sentinel").write_text("destination")
    flag = destination.parent / f".{destination.name}.migrating"
    flag.touch()
    ClaudeAccountSwitcher()
    assert (source / "sentinel").read_text() == "source"
    assert (destination / "sentinel").read_text() == "destination"
    assert flag.exists()
