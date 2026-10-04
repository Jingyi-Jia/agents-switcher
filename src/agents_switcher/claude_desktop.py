"""Experimental local profiles for the official Claude Desktop app."""

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
from datetime import datetime, timezone
from pathlib import Path

from agents_switcher.dirlock import directory_lock
from agents_switcher.exceptions import LockError
from agents_switcher.fsutil import replace_with_retry
from agents_switcher.paths import get_backup_root
from agents_switcher.providers import ProviderActionError

NOTICE = (
    "Experimental Claude Desktop profiles for macOS and Linux. Sign in separately "
    "inside Claude for each profile and check the account there. Fully quit Claude "
    "before opening another profile. Custom profile locations disable local pairing "
    "with Claude in Chrome. Mac account persistence and Code/Cowork behavior are not "
    "yet verified. Claude Code CLI accounts and automation are separate."
)
_PROFILE_ID = re.compile(r"[0-9a-f]{32}\Z")
_MAX_PROFILES = 100
_MAX_REGISTRY_BYTES = 262144
# Removing a profile only moves its labels here; the profile folder, with Claude's
# history and sign-in, stays in place so restoring returns it at the same path.
_REMOVED_REGISTRY = "removed-profiles.json"
_REMOVED_AT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


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
            ["/bin/ps", "-axww", "-o", "uid=,stat=,comm="],
            capture_output=True, text=True, timeout=3, check=True,
        )
        if not isinstance(result.stdout, str) or not result.stdout.strip():
            raise ValueError
        uid = os.getuid()
        found = False
        for line in result.stdout.splitlines():
            owner, state, *commands = line.strip().split(None, 2)
            if re.fullmatch(r"-?[0-9]+", owner) is None:
                raise ValueError
            owner_id = int(owner)
            if sys.platform == "darwin" and -(2**31) <= owner_id < 0:
                owner_id += 2**32
            if not 0 <= owner_id < 2**32:
                raise ValueError
            if state.startswith("Z"):
                continue
            if not commands:
                raise ValueError
            command = commands[0]
            if owner_id == uid and Path(command).name in {"Claude", "claude-desktop"}:
                found = True
        return found
    except subprocess.TimeoutExpired:
        detail = "The system process check timed out."
    except subprocess.SubprocessError:
        detail = "The system process check failed."
    except OSError:
        detail = "The system process checker could not be started."
    except (ValueError, AttributeError):
        detail = "The system returned an unreadable process list."
    raise ProviderActionError(
        f"Unable to check whether Claude Desktop is running. {detail} "
        "No launch was attempted. Try Refresh; if it still fails, report this message."
    ) from None


def _valid_name(name) -> bool:
    return (
        isinstance(name, str)
        and 1 <= len(name) <= 64
        and name == name.strip()
        and not any(unicodedata.category(char).startswith("C") for char in name)
    )


def _valid_email_label(email_label) -> bool:
    return (
        isinstance(email_label, str)
        and len(email_label) <= 320
        and not any(unicodedata.category(char).startswith("C") for char in email_label)
        and (email_label == "" or re.fullmatch(r"[^\s@]+@[^\s@]+", email_label) is not None)
    )


def _labels(name, email_label) -> tuple[str, str]:
    if (
        not isinstance(name, str)
        or any(unicodedata.category(char).startswith("C") for char in name)
        or not _valid_name(name.strip())
    ):
        raise ProviderActionError("Use a profile name of 1–64 characters without control characters.")
    if (
        not isinstance(email_label, str)
        or any(unicodedata.category(char).startswith("C") for char in email_label)
        or not _valid_email_label(email_label.strip())
    ):
        raise ProviderActionError(
            "Use an email label of up to 320 characters with one @ and no spaces or control characters, "
            "or leave it empty. This label is not a verified sign-in."
        )
    return name.strip(), email_label.strip()


def _strict_json(raw: bytes):
    """Parse a bounded registry file, refusing duplicate keys that could hide a second value."""
    if len(raw) > _MAX_REGISTRY_BYTES:
        raise ValueError

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    return json.loads(raw, object_pairs_hook=unique_object)


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

    def _profile_directory(self, profile_id: str) -> Path:
        directory = self.root / "profiles" / profile_id
        for path in (self.root / "profiles", directory, directory / "desktop", directory / "claude-code"):
            self._directory(path)
        return directory

    def _registry_bytes(self, filename: str, label: str) -> bytes | None:
        if not self.root.exists() and not self.root.is_symlink():
            return None
        self._directory(self.root)
        path = self.root / filename
        if path.is_symlink():
            raise ProviderActionError(f"The Claude Desktop {label} must not be a link.")
        try:
            with path.open("rb") as stream:
                return stream.read(_MAX_REGISTRY_BYTES + 1)
        except FileNotFoundError:
            return None

    def _read(self) -> list[dict]:
        raw = self._registry_bytes("profiles.json", "profile registry")
        if raw is None:
            return []
        try:
            data = _strict_json(raw)
            if not isinstance(data, dict) or set(data) != {"version", "profiles"}:
                raise ValueError
            profiles = data["profiles"]
            version = data["version"]
            if type(version) is not int or version not in {1, 2} or (version == 1 and len(raw) > 65536):
                raise ValueError
            if not isinstance(profiles, list) or len(profiles) > _MAX_PROFILES:
                raise ValueError
            ids, names = set(), set()
            for profile in profiles:
                fields = {"id", "name"} if version == 1 else {"id", "name", "emailLabel"}
                if not isinstance(profile, dict) or set(profile) != fields:
                    raise ValueError
                key, name = profile["id"], profile["name"]
                if not isinstance(key, str) or not _PROFILE_ID.fullmatch(key) or not _valid_name(name):
                    raise ValueError
                if key in ids or name.casefold() in names:
                    raise ValueError
                if version == 2 and not _valid_email_label(profile["emailLabel"]):
                    raise ValueError
                ids.add(key)
                names.add(name.casefold())
            return [{**profile, "emailLabel": profile.get("emailLabel", "")} for profile in profiles]
        except (ValueError, TypeError, RecursionError):
            raise ProviderActionError(
                "The Claude Desktop profile registry is invalid. It has not been overwritten."
            ) from None

    def _read_removed(self) -> list[dict]:
        raw = self._registry_bytes(_REMOVED_REGISTRY, "removed-profile list")
        if raw is None:
            return []
        try:
            data = _strict_json(raw)
            if not isinstance(data, dict) or set(data) != {"version", "profiles"}:
                raise ValueError
            if type(data["version"]) is not int or data["version"] != 1 or not isinstance(data["profiles"], list):
                raise ValueError
            ids = set()
            for entry in data["profiles"]:
                if not isinstance(entry, dict) or set(entry) != {"id", "name", "emailLabel", "removedAt"}:
                    raise ValueError
                key, removed_at = entry["id"], entry["removedAt"]
                if not isinstance(key, str) or not _PROFILE_ID.fullmatch(key) or key in ids:
                    raise ValueError
                if not _valid_name(entry["name"]) or not _valid_email_label(entry["emailLabel"]):
                    raise ValueError
                # Only the canonical UTC form is accepted, so string order is time order.
                if not isinstance(removed_at, str) or (
                    datetime.strptime(removed_at, _REMOVED_AT_FORMAT).strftime(_REMOVED_AT_FORMAT) != removed_at
                ):
                    raise ValueError
                ids.add(key)
            return data["profiles"]
        except (ValueError, TypeError, RecursionError):
            raise ProviderActionError(
                "The removed Claude Desktop profile list is invalid. It has not been overwritten."
            ) from None

    def _restorable(self, profiles: list[dict]) -> list[dict]:
        """Removed profiles that can come back, newest first.

        An id that is active again is a leftover of an interrupted restore, and a
        missing or linked folder could not be reopened, so neither is offered.
        """
        active = {profile["id"] for profile in profiles}
        restorable = []
        for entry in self._read_removed():
            if entry["id"] in active:
                continue
            try:
                self._profile_directory(entry["id"])
            except ProviderActionError:
                continue
            restorable.append(entry)
        # Entries are appended as they are removed; reversing first keeps the later of two
        # same-second removals ahead, because the stable sort preserves order among ties.
        return sorted(reversed(restorable), key=lambda entry: entry["removedAt"], reverse=True)

    def _write_json(self, filename: str, payload: dict) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if len(encoded) > _MAX_REGISTRY_BYTES:
            raise ProviderActionError(
                "Too many Claude Desktop profile labels are saved to record this change. No profiles were changed."
            )
        descriptor, name = tempfile.mkstemp(prefix=f".{filename.removesuffix('.json')}-", dir=self.root)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            replace_with_retry(temporary, self.root / filename)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _write(self, profiles: list[dict]) -> None:
        self._write_json("profiles.json", {"version": 2, "profiles": profiles})

    def _write_removed(self, removed: list[dict]) -> None:
        self._write_json(_REMOVED_REGISTRY, {"version": 1, "profiles": removed})

    def status(self) -> dict:
        supported = sys.platform in {"darwin", "linux"}
        installed = supported and installed_executable() is not None
        result = {
            "supported": supported, "installed": installed, "available": False, "canCreate": False,
            "canManage": False, "experimental": True, "notice": NOTICE, "error": None,
            "running": None, "profiles": [], "removedProfiles": [], "removedError": None,
        }
        if not supported:
            result["error"] = "Experimental Claude Desktop profiles are available on macOS and Linux only."
            return result
        try:
            result["profiles"] = self._read()
        except ProviderActionError as error:
            result["error"] = str(error)
        except OSError:
            result["error"] = "Could not read the Claude Desktop profile registry. No profiles were changed."
        if result["error"]:
            return result
        result["canCreate"] = result["canManage"] = True
        try:
            result["removedProfiles"] = self._restorable(result["profiles"])
        except ProviderActionError as error:
            result["removedError"] = str(error)
        except OSError:
            result["removedError"] = "Could not read the removed Claude Desktop profile list. No profiles were changed."
        if not installed:
            result["error"] = (
                "To open profiles, install official Claude Desktop in /Applications/Claude.app or "
                "~/Applications/Claude.app on macOS, or /usr/bin/claude-desktop on Linux, then choose Refresh."
            )
        try:
            active = running()
            if type(active) is not bool:
                raise ProviderActionError("Unable to confirm whether Claude Desktop is running. No profiles were changed.")
            result["running"] = active
            result["available"] = installed
        except ProviderActionError as error:
            if installed:
                result["error"] = str(error)
        return result

    def _allowed(self, confirm) -> None:
        if confirm is not True:
            raise ProviderActionError("Confirm the experimental Claude Desktop profile limitations first.")
        if sys.platform not in {"darwin", "linux"}:
            raise ProviderActionError("Experimental Claude Desktop profiles support macOS and Linux only.")

    def create(self, name, *, emailLabel="", confirm=False) -> dict:
        self._allowed(confirm)
        name, emailLabel = _labels(name, emailLabel)
        with self._locked():
            profiles = self._read()
            if any(profile["name"].casefold() == name.casefold() for profile in profiles):
                raise ProviderActionError("A Claude Desktop profile already has that name.")
            if len(profiles) >= _MAX_PROFILES:
                raise ProviderActionError("The limit of 100 Claude Desktop profiles has been reached.")
            profile = {"id": uuid.uuid4().hex, "name": name, "emailLabel": emailLabel}
            self._directory(self.root / "profiles", create=True)
            directory = self.root / "profiles" / profile["id"]
            directory.mkdir(mode=0o700)
            self._directory(directory / "desktop", create=True)
            self._directory(directory / "claude-code", create=True)
            self._write([*profiles, profile])
        return {"ok": True, "profile": profile, "message": "Empty profile created. Open it and sign in directly in Claude."}

    def update(self, profileId, name, emailLabel, *, confirm=False) -> dict:
        self._allowed(confirm)
        if not isinstance(profileId, str) or not _PROFILE_ID.fullmatch(profileId):
            raise ProviderActionError("Select a saved Claude Desktop profile; the usual profile cannot be changed.")
        name, emailLabel = _labels(name, emailLabel)
        with self._locked():
            profiles = self._read()
            if not any(profile["id"] == profileId for profile in profiles):
                raise ProviderActionError("That Claude Desktop profile is not in the saved list.")
            if any(profile["id"] != profileId and profile["name"].casefold() == name.casefold() for profile in profiles):
                raise ProviderActionError("A Claude Desktop profile already has that name.")
            self._profile_directory(profileId)
            updated = {"id": profileId, "name": name, "emailLabel": emailLabel}
            self._write([updated if profile["id"] == profileId else profile for profile in profiles])
        return {
            "ok": True, "profile": updated,
            "message": "Profile labels updated. The email label is user-entered and does not verify the signed-in account.",
        }

    def remove(self, profileId, *, confirm=False) -> dict:
        """Take a profile off the list without touching its folder, so it can be restored later."""
        self._allowed(confirm)
        if not isinstance(profileId, str) or not _PROFILE_ID.fullmatch(profileId):
            raise ProviderActionError("Select a saved Claude Desktop profile; the usual profile cannot be removed.")
        with self._locked():
            profiles = self._read()
            profile = next((entry for entry in profiles if entry["id"] == profileId), None)
            if profile is None:
                raise ProviderActionError("That Claude Desktop profile is not in the saved list.")
            # The folder is never touched here, so a missing or linked one must not block taking a
            # broken entry off the list; _restorable() and restore() refuse such folders instead.
            previous = self._read_removed()
            active = {entry["id"] for entry in profiles}
            record = {**profile, "removedAt": datetime.now(timezone.utc).strftime(_REMOVED_AT_FORMAT)}
            # Record the removal first: if the active list then fails to save, the profile is still
            # listed there, and the active list always wins over a removed entry.
            self._write_removed([entry for entry in previous if entry["id"] not in active] + [record])
            try:
                self._write([entry for entry in profiles if entry["id"] != profileId])
            except OSError:
                try:
                    self._write_removed(previous)
                except OSError:
                    pass
                raise ProviderActionError(
                    "The profile registry could not be saved, so nothing was removed. "
                    "Check folder permissions and free disk space, then try again."
                ) from None
        return {
            "ok": True, "profile": record,
            "message": "Profile removed from the list. Its local Claude data stays on this computer and can be restored.",
        }

    def restore(self, profileId, name, emailLabel, *, confirm=False) -> dict:
        """Put a removed profile back on the list, at its original folder, under new labels."""
        self._allowed(confirm)
        if not isinstance(profileId, str) or not _PROFILE_ID.fullmatch(profileId):
            raise ProviderActionError("Select a removed Claude Desktop profile; the usual profile cannot be restored.")
        name, emailLabel = _labels(name, emailLabel)
        with self._locked():
            profiles = self._read()
            removed = self._read_removed()
            if any(entry["id"] == profileId for entry in profiles) or not any(entry["id"] == profileId for entry in removed):
                raise ProviderActionError("That Claude Desktop profile is not in the removed list.")
            if any(entry["name"].casefold() == name.casefold() for entry in profiles):
                raise ProviderActionError("A Claude Desktop profile already has that name.")
            if len(profiles) >= _MAX_PROFILES:
                raise ProviderActionError("The limit of 100 Claude Desktop profiles has been reached.")
            self._profile_directory(profileId)
            restored = {"id": profileId, "name": name, "emailLabel": emailLabel}
            self._write([*profiles, restored])
            try:
                self._write_removed([entry for entry in removed if entry["id"] != profileId])
            except OSError:
                # Harmless: the active list now wins over the leftover entry, and the next removal drops it.
                pass
        return {
            "ok": True, "profile": restored,
            "message": "Profile restored with its local Claude history. Open it to continue where you left off.",
        }

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
                directory = self._profile_directory(profileId)
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
