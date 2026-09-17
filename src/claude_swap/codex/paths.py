"""Path resolution for Codex CLI's config home and this tool's Codex backups.

Mirrors Codex's own resolution so we read and write the same file Codex does:
``CODEX_HOME`` if set, else ``~/.codex``; the credential is ``<home>/auth.json``
(codex-rs ``get_auth_file``). Backups live under this tool's existing backup
root in a ``codex/`` subdirectory, so the Claude and Codex stores never share a
namespace and the backup root stays one directory to migrate or back up.

References:
- codex-rs/login/src/auth/storage.rs (``get_auth_file``)
- src-tauri/src/auth/switcher.rs in Lampese/codex-switcher (``get_codex_home``)
"""

from __future__ import annotations

import os
from pathlib import Path

from claude_swap.paths import get_backup_root


def get_codex_home() -> Path:
    """Return Codex's config home (``CODEX_HOME`` or ``~/.codex``).

    An empty ``CODEX_HOME`` is treated as unset. Codex itself reads the variable
    with Rust's ``env::var``, which returns ``Ok("")`` for an empty value and so
    would resolve ``auth.json`` relative to the current directory -- a spelling
    that cannot be a real credential store. Following it would only mean writing
    the account somewhere Codex will not find it either.
    """
    env = os.environ.get("CODEX_HOME")
    if env:
        return Path(env)
    return Path.home() / ".codex"


def get_auth_file() -> Path:
    """Return the path to Codex's live ``auth.json``."""
    return get_codex_home() / "auth.json"


def get_config_file() -> Path:
    """Return the path to Codex's ``config.toml``.

    Read-only as far as this tool is concerned: it is where
    ``cli_auth_credentials_store`` selects the file-vs-keyring backend, which
    decides whether ``auth.json`` is authoritative at all.
    """
    return get_codex_home() / "config.toml"


def get_codex_backup_root() -> Path:
    """Return this tool's Codex backup directory.

    A ``codex/`` subdirectory of the shared backup root rather than a sibling of
    it: the root is already the unit that migrates between layouts (XDG vs the
    legacy path) and that users copy between machines, and a second top-level
    directory would silently miss both.
    """
    return get_backup_root() / "codex"


def get_codex_credentials_dir() -> Path:
    """Return the directory holding per-account credential backups."""
    return get_codex_backup_root() / "credentials"


def get_accounts_file() -> Path:
    """Return the file holding Codex account metadata (slot -> identity)."""
    return get_codex_backup_root() / "accounts.json"
