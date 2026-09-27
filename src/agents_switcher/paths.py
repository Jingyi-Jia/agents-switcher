"""Path resolution for Claude Code config and credential files.

Mirrors claude-code's own resolution so the official live files do not move:

- Config home: ``CLAUDE_CONFIG_DIR`` if set, else ``~/.claude``.
- Global config: ``<config_home>/.config.json`` if it exists (legacy),
  otherwise ``(CLAUDE_CONFIG_DIR || $HOME)/.claude.json``.
- Credentials: ``<config_home>/.credentials.json``.

Agent Switch's saved-account root is independent from these live paths and
from the historical stores exposed to the explicit copy-only importer.
"""

from __future__ import annotations

import os
from pathlib import Path

from agents_switcher.models import Platform

LEGACY_BACKUP_DIRNAME = ".claude-swap-backup"


def get_claude_config_home() -> Path:
    """Return the Claude config home directory (CLAUDE_CONFIG_DIR or ~/.claude)."""
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        return Path(env)
    return Path.home() / ".claude"


def get_global_config_path() -> Path:
    """Return the path to the global Claude config file.

    Returns the legacy ``<config_home>/.config.json`` if it exists, else
    ``(CLAUDE_CONFIG_DIR || $HOME)/.claude.json``.
    """
    legacy = get_claude_config_home() / ".config.json"
    if legacy.exists():
        return legacy
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    base = Path(env) if env else Path.home()
    return base / ".claude.json"


def get_default_claude_config_home() -> Path:
    """Return the *default* profile's config home, ignoring ``CLAUDE_CONFIG_DIR``.

    ``_read_capture_credentials`` has to tell an env var that names the default
    profile from one that names another, since only the former's credential is
    the active store's.
    """
    return Path.home() / ".claude"


def get_default_global_config_path() -> Path:
    """Return the global config path of the *default* profile.

    Same legacy fallback as :func:`get_global_config_path`, but deliberately
    ignores ``CLAUDE_CONFIG_DIR``: callers that mirror the user's real profile
    (session sharing) must not source from another session when invoked from
    inside one.
    """
    legacy = get_default_claude_config_home() / ".config.json"
    if legacy.exists():
        return legacy
    return Path.home() / ".claude.json"


def get_credentials_path() -> Path:
    """Return the path to the official Claude credentials file."""
    return get_claude_config_home() / ".credentials.json"


def get_legacy_backup_root() -> Path:
    """Return the historical pre-XDG store without reading or migrating it."""
    return Path.home() / LEGACY_BACKUP_DIRNAME


def _xdg_data_home() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME", "")
    if xdg:
        path = Path(os.path.expanduser(xdg))
        if path.is_absolute():
            return path
    return Path.home() / ".local" / "share"


def get_backup_root() -> Path:
    """Return Agent Switch's independent data root for the current platform.

    XDG values must be absolute after expanding a leading tilde. Windows uses
    LOCALAPPDATA when absolute, otherwise the home-relative platform default.
    """
    platform = Platform.detect()
    if platform in (Platform.LINUX, Platform.WSL):
        return _xdg_data_home() / "agents-switcher"
    if platform == Platform.MACOS:
        return Path.home() / "Library" / "Application Support" / "agents-switcher"
    if platform == Platform.WINDOWS:
        local = os.environ.get("LOCALAPPDATA", "")
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        if not base.is_absolute():
            base = Path.home() / "AppData" / "Local"
        return base / "agents-switcher"
    return Path.home() / ".agents-switcher"


def get_legacy_xdg_backup_root() -> Path:
    """Return the historical XDG store without reading or migrating it."""
    return _xdg_data_home() / "claude-swap"


def get_legacy_import_roots() -> dict[str, Path]:
    """Fixed source locations; IDs, never request-supplied paths, select them."""
    roots = {"legacy": get_legacy_backup_root()}
    if Platform.detect() in (Platform.LINUX, Platform.WSL):
        roots["xdg"] = get_legacy_xdg_backup_root()
    return roots


def migration_flag_for(target: Path) -> Path:
    """Historical marker path, retained for real-store test protection only."""
    return target.parent / f".{target.name}.migrating"
