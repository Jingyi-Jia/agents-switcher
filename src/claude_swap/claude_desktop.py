"""Experimental local profiles for the official Claude Desktop app."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import uuid
from contextlib import ExitStack, contextmanager
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
_MAX_REGISTRY_BYTES = 262144


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

    def _read(self) -> list[dict]:
        if not self.root.exists() and not self.root.is_symlink():
            return []
        self._directory(self.root)
        path = self.root / "profiles.json"
        if path.is_symlink():
            raise ProviderActionError("The Claude Desktop profile registry must not be a link.")
        try:
            with path.open("rb") as stream:
                raw = stream.read(_MAX_REGISTRY_BYTES + 1)
        except FileNotFoundError:
            return []
        try:
            if len(raw) > _MAX_REGISTRY_BYTES:
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

    def _write(self, profiles: list[dict], *, root_fd: int | None = None) -> None:
        if root_fd is None:
            descriptor, name = tempfile.mkstemp(prefix=".profiles-", dir=self.root)
        else:
            name = f".profiles-{uuid.uuid4().hex}"
            descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=root_fd)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump({"version": 2, "profiles": profiles}, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            if root_fd is None:
                replace_with_retry(temporary, self.root / "profiles.json")
            else:
                os.replace(temporary, "profiles.json", src_dir_fd=root_fd, dst_dir_fd=root_fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=root_fd)
            except FileNotFoundError:
                pass

    def status(self) -> dict:
        supported = sys.platform in {"darwin", "linux"}
        installed = supported and installed_executable() is not None
        result = {
            "supported": supported, "installed": installed, "available": False, "canCreate": False,
            "canManage": False, "canDelete": False, "deleteError": None,
            "experimental": True, "notice": NOTICE, "error": None,
            "running": None, "profiles": [],
        }
        if not supported:
            result["error"] = "Experimental Claude Desktop profiles are available on macOS and Linux only."
            result["deleteError"] = result["error"]
            return result
        try:
            result["profiles"] = self._read()
        except ProviderActionError as error:
            result["error"] = str(error)
        except OSError:
            result["error"] = "Could not read the Claude Desktop profile registry. No profiles were changed."
        if result["error"]:
            result["deleteError"] = result["error"]
            return result
        result["canCreate"] = result["canManage"] = True
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
            result["canDelete"] = not active
            if active:
                result["deleteError"] = "Fully quit Claude Desktop before deleting a profile. Closing its window may not quit it."
            elif not shutil.rmtree.avoids_symlink_attacks:
                result["canDelete"] = False
                result["deleteError"] = "This system cannot safely delete profile directories. No profiles were changed."
        except ProviderActionError as error:
            result["deleteError"] = str(error)
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

    def delete(self, profileId, *, confirm=False) -> dict:
        self._allowed(confirm)
        if not isinstance(profileId, str) or not _PROFILE_ID.fullmatch(profileId):
            raise ProviderActionError("Select a saved Claude Desktop profile; the usual profile cannot be deleted.")
        with self._locked():
            profiles = self._read()
            if not any(profile["id"] == profileId for profile in profiles):
                raise ProviderActionError("That Claude Desktop profile is not in the saved list.")
            directory = self._profile_directory(profileId)
            root_stat = self.root.stat(follow_symlinks=False)
            profile_stat = directory.stat(follow_symlinks=False)
            active = running()
            if active is True:
                raise ProviderActionError("Fully quit Claude Desktop before deleting a profile. Closing its window may not quit it.")
            if active is not False:
                raise ProviderActionError("Unable to confirm that Claude Desktop is not running. No profiles were deleted.")
            if not shutil.rmtree.avoids_symlink_attacks:
                raise ProviderActionError("This system cannot safely delete profile directories. No profiles were changed.")
            with ExitStack() as descriptors:
                flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                directory_fds = []
                for name in (self.root, "profiles", profileId):
                    descriptor = os.open(name, flags, dir_fd=directory_fds[-1] if directory_fds else None)
                    descriptors.callback(os.close, descriptor)
                    directory_fds.append(descriptor)
                root_fd, profiles_fd, profile_fd = directory_fds
                if not os.path.samestat(root_stat, os.fstat(root_fd)) or not os.path.samestat(profile_stat, os.fstat(profile_fd)):
                    raise ProviderActionError("The profile directories changed during the safety check. No profile data was erased.")
                for name in ("desktop", "claude-code"):
                    os.close(os.open(name, flags, dir_fd=profile_fd))
                staging = f".deleting-{uuid.uuid4().hex}"
                os.mkdir(staging, mode=0o700, dir_fd=root_fd)
                staging_fd = os.open(staging, flags, dir_fd=root_fd)
                descriptors.callback(os.close, staging_fd)
                try:
                    os.rename(profileId, "profile", src_dir_fd=profiles_fd, dst_dir_fd=staging_fd)
                except OSError:
                    os.rmdir(staging, dir_fd=root_fd)
                    raise
                try:
                    if not os.path.samestat(profile_stat, os.stat("profile", dir_fd=staging_fd, follow_symlinks=False)):
                        raise ProviderActionError("The selected profile directory changed before deletion.")
                    self._write([profile for profile in profiles if profile["id"] != profileId], root_fd=root_fd)
                except (OSError, ProviderActionError):
                    try:
                        os.rename("profile", profileId, src_dir_fd=staging_fd, dst_dir_fd=profiles_fd)
                    except OSError:
                        raise ProviderActionError(
                            "The profile registry could not be saved and its staged data could not be returned. "
                            "No profile data was erased; it remains in private staged storage. "
                            "Check folder permissions and free disk space before making more profile changes."
                        ) from None
                    os.rmdir(staging, dir_fd=root_fd)
                    raise ProviderActionError(
                        "The profile registry could not be saved. Its directory was restored and no profile data was erased. "
                        "Check folder permissions and free disk space, then try again."
                    ) from None
                try:
                    shutil.rmtree("profile", dir_fd=staging_fd)
                    os.rmdir(staging, dir_fd=root_fd)
                except OSError:
                    return {
                        "ok": False, "warning": True, "profileId": profileId,
                        "message": "The profile was removed from the list, but cleanup failed and local profile data may remain "
                        "in private staged storage. Check folder permissions and free disk space; this was not a complete deletion.",
                    }
        return {
            "ok": True, "profileId": profileId,
            "message": "The selected profile and its local data were deleted. Other profiles and CLI accounts were not changed.",
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
