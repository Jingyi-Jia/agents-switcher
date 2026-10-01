import json
import shutil
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agents_switcher import claude_desktop
from agents_switcher.codex.identity import CodexIdentity
from agents_switcher.codex.store import CodexAccountStore
from agents_switcher.credentials import CredentialStore
from agents_switcher.models import Platform
from agents_switcher.paths import get_backup_root
from agents_switcher.switcher import ClaudeAccountSwitcher


@pytest.mark.parametrize("platform,keychain_available", [
    (Platform.MACOS, True), (Platform.MACOS, False), (Platform.LINUX, False),
])
def test_replacing_application_files_retains_all_saved_profile_data(tmp_path, monkeypatch, block_real_keychain, platform, keychain_available):
    monkeypatch.setattr(Platform, "detect", staticmethod(lambda: platform))
    monkeypatch.setattr(CredentialStore, "_use_keychain", lambda _: keychain_available)
    monkeypatch.setattr(claude_desktop, "sys", SimpleNamespace(platform="darwin" if platform == Platform.MACOS else "linux"))
    monkeypatch.setattr(claude_desktop, "running", Mock(return_value=False))
    monkeypatch.setattr(claude_desktop, "installed_executable", Mock(return_value=None))
    install = tmp_path / "installed-app"
    install.mkdir()
    (install / "backend-version").write_text("old")
    monkeypatch.chdir(install)

    root = get_backup_root()
    claude = ClaudeAccountSwitcher()
    claude._setup_directories()
    roster = {
        "sequence": [1], "activeAccountNumber": "1",
        "accounts": {"1": {"email": "claude@example.test", "uuid": "synthetic-claude", "alias": "work"}},
    }
    claude._write_json(claude.sequence_file, roster)
    saved_claude = json.dumps({"claudeAiOauth": {"accessToken": "synthetic-claude-access", "refreshToken": "synthetic-claude-refresh"}})
    claude._store._write_account_credentials("1", "claude@example.test", saved_claude)
    saved_keychain = dict(block_real_keychain.data)

    codex = CodexAccountStore()
    saved_login = {"auth_mode": "chatgpt", "tokens": {"access_token": "synthetic-access", "refresh_token": "synthetic-refresh"}}
    account = codex.add(CodexIdentity(email="codex@example.test", account_id="synthetic-codex", plan="plus"), saved_login, alias="work")
    profiles = claude_desktop.ClaudeDesktopProfiles()
    profile = profiles.create("Work", emailLabel="desktop@example.test", confirm=True)["profile"]
    desktop_state = profiles.root / "profiles" / profile["id"] / "desktop" / "synthetic-session-state"
    desktop_state.write_text("synthetic persistent Desktop state")
    companion_state = desktop_state.parent.parent / "claude-code" / "synthetic-session-state"
    companion_state.write_text("synthetic persistent companion state")
    saved = {
        path: path.read_bytes() for path in (
            claude.sequence_file, codex.accounts_file,
            codex.credentials_dir / f"{account.number}.json", profiles.root / "profiles.json", desktop_state, companion_state,
        )
    }
    if not keychain_available:
        backup = claude._backup_enc_path("1", "claude@example.test")
        saved[backup] = backup.read_bytes()

    monkeypatch.chdir(tmp_path)
    shutil.rmtree(install)
    replacement = tmp_path / "replacement-app"
    replacement.mkdir()
    (replacement / "backend-version").write_text("new")
    monkeypatch.chdir(replacement)

    reopened_claude = ClaudeAccountSwitcher()
    reopened_claude._setup_directories()
    reopened_claude._init_sequence_file()
    reopened_codex = CodexAccountStore()
    reopened_profiles = claude_desktop.ClaudeDesktopProfiles()
    assert get_backup_root() == root
    assert reopened_claude.backup_dir == root
    assert reopened_claude._get_sequence_data() == roster
    assert reopened_claude._read_account_credentials("1", "claude@example.test") == saved_claude
    assert block_real_keychain.data == saved_keychain
    assert reopened_codex.root == root / "codex"
    assert reopened_codex.get(account.number) == account
    assert reopened_codex.read_credentials(account.number) == saved_login
    assert reopened_profiles.root == root / "claude-desktop"
    assert reopened_profiles.status()["profiles"] == [profile]
    assert {path: path.read_bytes() for path in saved} == saved
