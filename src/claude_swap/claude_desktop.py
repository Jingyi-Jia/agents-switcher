"""Experimental launch-only profiles for the official Claude Desktop app."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
import uuid
from contextlib import contextmanager
from pathlib import Path

from claude_swap.dirlock import directory_lock
from claude_swap.exceptions import LockError
from claude_swap.fsutil import replace_with_retry
from claude_swap.paths import get_backup_root
from claude_swap.providers import ProviderActionError

NOTICE = (
    "Experimental Claude Desktop profiles for macOS and Linux. Sign in separately "
    "inside Claude for each profile and check the account there. Fully quit Claude "
    "before opening another profile. Custom profile locations disable local pairing "
    "with Claude in Chrome. Mac account persistence and Code/Cowork behavior are not "
    "yet verified. Claude Code CLI accounts and automation are separate."
)
_PROFILE_ID = re.compile(r"[0-9a-f]{32}\Z")
_MAX_PROFILES = 100


def installed_executable() -> Path | None:
    if sys.platform == "darwin":
        candidates = [
            Path("/Applications/Claude.app/Contents/MacOS/Claude"),
            Path.home() / "Applications/Claude.app/Contents/MacOS/Claude",
        ]
    elif sys.platform == "linux":
        candidates = [Path("/usr/bin/claude-desktop")]
    else:
        return None
    for path in candidates:
        try:
            if path.is_file() and os.access(path, os.X_OK):
                return path
        except OSError:
            continue
    return None


def running() -> bool:
    try:
        result = subprocess.run(
            ["/bin/ps", "-axo", "uid=,comm="],
            capture_output=True, text=True, timeout=3, check=True,
        )
        if not isinstance(result.stdout, str) or not result.stdout.strip():
            raise ValueError
        uid = os.getuid()
        found = False
        for line in result.stdout.splitlines():
            owner, command = line.strip().split(None, 1)
            if not owner.isascii() or not owner.isdigit():
                raise ValueError
            if int(owner) == uid and Path(command).name in {"Claude", "claude-desktop"}:
                found = True
        return found
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
        raise ProviderActionError(
            "Unable to check whether Claude Desktop is running. No launch was attempted. Try refreshing."
        ) from None


def _valid_name(name) -> bool:
    return (
        isinstance(name, str)
        and 1 <= len(name) <= 64
        and name == name.strip()
        and not any(unicodedata.category(char).startswith("C") for char in name)
    )


class ClaudeDesktopProfiles:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root if root is not None else get_backup_root() / "claude-desktop"

    @contextmanager
    def _locked(self):
        try:
            self._directory(self.root, create=True)
            with directory_lock(self.root / ".lock", timeout=5):
                yield
        except (OSError, LockError):
            raise ProviderActionError(
                "Could not access Claude Desktop profile data. Check folder permissions and free disk space, "
                "then retry after any other profile operation finishes."
            ) from None

    def _directory(self, path: Path, *, create: bool = False) -> None:
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise ProviderActionError("Claude Desktop profile directories must be real directories, not links.")
        if create:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.chmod(0o700)
        elif not path.is_dir():
            raise ProviderActionError("This profile directory is missing. It was not recreated or launched.")

    def _read(self) -> list[dict]:
        if not self.root.exists() and not self.root.is_symlink():
            return []
        self._directory(self.root)
        path = self.root / "profiles.json"
        if path.is_symlink():
            raise ProviderActionError("The Claude Desktop profile registry must not be a link.")
        try:
            with path.open("rb") as stream:
                raw = stream.read(65537)
        except FileNotFoundError:
            return []
        try:
            if len(raw) > 65536:
                raise ValueError

            def unique_object(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError
                    result[key] = value
                return result

            data = json.loads(raw, object_pairs_hook=unique_object)
            if not isinstance(data, dict) or set(data) != {"version", "profiles"}:
                raise ValueError
            profiles = data["profiles"]
            if type(data["version"]) is not int or data["version"] != 1:
                raise ValueError
            if not isinstance(profiles, list) or len(profiles) > _MAX_PROFILES:
                raise ValueError
            ids, names = set(), set()
            for profile in profiles:
                if not isinstance(profile, dict) or set(profile) != {"id", "name"}:
                    raise ValueError
                key, name = profile["id"], profile["name"]
                if not isinstance(key, str) or not _PROFILE_ID.fullmatch(key) or not _valid_name(name):
                    raise ValueError
                if key in ids or name.casefold() in names:
                    raise ValueError
                ids.add(key)
                names.add(name.casefold())
            return profiles
        except (ValueError, TypeError, RecursionError):
            raise ProviderActionError(
                "The Claude Desktop profile registry is invalid. It has not been overwritten."
            ) from None

    def _write(self, profiles: list[dict]) -> None:
        descriptor, name = tempfile.mkstemp(prefix=".profiles-", dir=self.root)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump({"version": 1, "profiles": profiles}, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            replace_with_retry(temporary, self.root / "profiles.json")
        finally:
            temporary.unlink(missing_ok=True)

    def status(self) -> dict:
        supported = sys.platform in {"darwin", "linux"}
        installed = supported and installed_executable() is not None
        result = {
            "supported": supported, "installed": installed, "available": False, "canCreate": False,
            "experimental": True, "notice": NOTICE, "error": None,
            "running": None, "profiles": [],
        }
        if not supported:
            result["error"] = "Experimental Claude Desktop profiles are available on macOS and Linux only."
            return result
        try:
            result["profiles"] = self._read()
            result["canCreate"] = True
            if not installed:
                result["error"] = (
                    "To open profiles, install official Claude Desktop in /Applications/Claude.app or "
                    "~/Applications/Claude.app on macOS, or /usr/bin/claude-desktop on Linux, then check again."
                )
                return result
            result["running"] = running()
            result["available"] = True
        except ProviderActionError as error:
            result["error"] = str(error)
        except OSError:
            result["error"] = "Could not read the Claude Desktop profile registry. No profiles were changed."
        return result

    def _allowed(self, confirm) -> None:
        if confirm is not True:
            raise ProviderActionError("Confirm the experimental Claude Desktop profile limitations first.")
        if sys.platform not in {"darwin", "linux"}:
            raise ProviderActionError("Experimental Claude Desktop profiles support macOS and Linux only.")

    def create(self, name, *, confirm=False) -> dict:
        self._allowed(confirm)
        if not isinstance(name, str) or not _valid_name(name.strip()):
            raise ProviderActionError("Use a profile name of 1–64 characters without control characters.")
        name = name.strip()
        with self._locked():
            profiles = self._read()
            if any(profile["name"].casefold() == name.casefold() for profile in profiles):
                raise ProviderActionError("A Claude Desktop profile already has that name.")
            if len(profiles) >= _MAX_PROFILES:
                raise ProviderActionError("The limit of 100 Claude Desktop profiles has been reached.")
            profile = {"id": uuid.uuid4().hex, "name": name}
            self._directory(self.root / "profiles", create=True)
            directory = self.root / "profiles" / profile["id"]
            directory.mkdir(mode=0o700)
            self._directory(directory / "desktop", create=True)
            self._directory(directory / "claude-code", create=True)
            self._write([*profiles, profile])
        return {"ok": True, "profile": profile, "message": "Empty profile created. Open it and sign in directly in Claude."}

    def open(self, profileId, *, confirm=False) -> dict:
        self._allowed(confirm)
        executable = installed_executable()
        if executable is None:
            raise ProviderActionError("Install the official Claude Desktop app in its usual location first.")
        if not isinstance(profileId, str) or (profileId != "default" and not _PROFILE_ID.fullmatch(profileId)):
            raise ProviderActionError("Select a saved Claude Desktop profile or the usual profile.")
        with self._locked():
            command = [str(executable)]
            environment = {
                key: value for key, value in os.environ.items()
                if not key.startswith(("CLAUDE_", "ANTHROPIC_", "ELECTRON_"))
                and key not in {
                    "NODE_OPTIONS", "NODE_PATH", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES",
                    "SSLKEYLOGFILE", "sslkeylogfile",
                }
            }
            if profileId != "default":
                if not any(profile["id"] == profileId for profile in self._read()):
                    raise ProviderActionError("That Claude Desktop profile is not in the saved list.")
                directory = self.root / "profiles" / profileId
                for path in (self.root / "profiles", directory, directory / "desktop", directory / "claude-code"):
                    self._directory(path)
                command.append(f"--user-data-dir={directory / 'desktop'}")
                environment["CLAUDE_CONFIG_DIR"] = str(directory / "claude-code")
            if running():
                raise ProviderActionError("Fully quit Claude Desktop before opening a profile. Closing its window may not quit it.")
            try:
                process = subprocess.Popen(
                    command, env=environment, stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True, close_fds=True,
                )
                process.wait(timeout=0.75)
            except subprocess.TimeoutExpired:
                pass
            except OSError:
                raise ProviderActionError("Claude Desktop could not be launched. Check its installation and try again.") from None
            else:
                raise ProviderActionError("Claude Desktop exited before startup could be confirmed. No account switch is confirmed.")
        return {
            "ok": True,
            "message": "Claude Desktop launch requested. Check the account inside Claude; sign in there if this profile is new.",
        }
