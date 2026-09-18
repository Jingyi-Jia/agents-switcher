"""Tests for Codex config-home and backup path resolution."""

from __future__ import annotations

from pathlib import Path

from claude_swap import paths as claude_paths
from claude_swap.codex import paths


class TestCodexHome:
    def test_defaults_to_dot_codex_under_home(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CODEX_HOME", raising=False)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        assert paths.get_codex_home() == tmp_path / ".codex"

    def test_codex_home_env_wins(self, tmp_path, monkeypatch):
        override = tmp_path / "elsewhere"
        monkeypatch.setenv("CODEX_HOME", str(override))
        assert paths.get_codex_home() == override

    def test_empty_codex_home_is_treated_as_unset(self, tmp_path, monkeypatch):
        # Codex reads this with Rust's env::var, which returns Ok("") and would
        # resolve auth.json relative to the cwd. Following that would only write
        # the credential somewhere Codex cannot find either.
        monkeypatch.setenv("CODEX_HOME", "")
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        assert paths.get_codex_home() == tmp_path / ".codex"

    def test_auth_file_sits_directly_in_the_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        assert paths.get_auth_file() == tmp_path / "auth.json"

    def test_config_file_sits_directly_in_the_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        assert paths.get_config_file() == tmp_path / "config.toml"


class TestBackupLayout:
    def test_backups_live_under_the_shared_backup_root(self):
        # A subdirectory, not a sibling: the root is the unit that migrates
        # between layouts and that users copy between machines.
        root = claude_paths.get_backup_root()
        assert paths.get_codex_backup_root() == root / "codex"
        assert root in paths.get_codex_backup_root().parents

    def test_credentials_and_accounts_sit_under_the_codex_root(self):
        root = paths.get_codex_backup_root()
        assert paths.get_codex_credentials_dir().parent == root
        assert paths.get_accounts_file().parent == root

    def test_codex_backups_never_collide_with_claude_ones(self):
        # The Claude side owns <root>/credentials; Codex must not share it.
        assert paths.get_codex_credentials_dir() != (
            claude_paths.get_backup_root() / "credentials"
        )
