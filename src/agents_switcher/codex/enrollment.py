"""Isolated official-Codex sign-in sessions; never log in over live credentials."""

from __future__ import annotations

import json
import os
import secrets
import shlex
import shutil
import stat
import sys
import threading
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path

from agents_switcher.codex.auth_file import CodexAuthError
from agents_switcher.codex.store import CodexAccount
from agents_switcher.codex.switcher import CodexSwitcher
from agents_switcher.exceptions import AccountNotFoundError, ClaudeSwitchError, SwitchError, ValidationError

LOGIN_ARGUMENTS = ("-c", 'cli_auth_credentials_store="file"', "login")
MAX_AUTH_BYTES = 1024 * 1024
SESSION_TTL_SECONDS = 30 * 60
_CLEANUP_WARNING = (
    "Private temporary login files remain. Stop the terminal login and retry cancellation to clean them up."
)


class EnrollmentError(ClaudeSwitchError):
    """A safe, user-facing enrollment failure without credential contents."""


def _linked(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _windows_handle(path: Path, *, directory: bool):
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    )
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    handle = create(str(path), 0 if directory else 0x80000000, 3, None, 3,
                    0x00200000 | (0x02000000 if directory else 0), None)
    if handle == wintypes.HANDLE(-1).value:
        raise OSError("Cannot open the private enrollment directory or login file.")
    return handle, close


@contextmanager
def _directory(path: Path):
    """Pin every path component without following links or Windows reparse points."""
    path = path.absolute()
    with ExitStack() as stack:
        if os.name == "nt":
            current = Path(path.anchor)
            for component in (None, *path.parts[1:]):
                if component is not None:
                    current /= component
                handle, close = _windows_handle(current, directory=True)
                stack.callback(close, handle)
                info = current.stat(follow_symlinks=False)
                if _linked(info) or not stat.S_ISDIR(info.st_mode):
                    raise OSError("Linked enrollment directories are not supported.")
            yield None
        else:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            descriptor = os.open(path.anchor, flags)
            stack.callback(os.close, descriptor)
            for component in path.parts[1:]:
                descriptor = os.open(component, flags, dir_fd=descriptor)
                stack.callback(os.close, descriptor)
            yield descriptor


def _clear_windows_directory(path: Path, expected: os.stat_result) -> None:
    """Remove children while no-follow Windows handles prevent directory replacement."""
    with _directory(path):
        current = path.stat(follow_symlinks=False)
        if not os.path.samestat(current, expected):
            raise OSError("The private enrollment directory changed.")
        for child in path.iterdir():
            info = child.stat(follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                if not _linked(info):
                    _clear_windows_directory(child, info)
                os.rmdir(child)
            else:
                child.unlink()


@dataclass
class _Session:
    session_id: str
    directory: Path
    directory_stat: os.stat_result
    expires_at: float
    expected: CodexAccount | None
    account: CodexAccount | None = None
    result: dict | None = None
    cancelled: bool = False


class CodexEnrollment:
    """Prepare/import one private login session; never spawn a GUI-owned process."""

    def __init__(self, switcher: CodexSwitcher, *, alias: str = "") -> None:
        from agents_switcher.models import normalize_alias

        self.switcher = switcher
        self._alias = normalize_alias(alias) if alias else ""
        self._session: _Session | None = None
        self._closed_session_id: str | None = None
        self._lock = threading.RLock()
        self._closed = False

    @staticmethod
    def _confirm(confirm: bool) -> None:
        if confirm is not True:
            raise EnrollmentError("Confirm the Codex enrollment action to continue.")

    def _get(self, session_id: str, *, cleanup: bool = False) -> _Session:
        session = self._session
        if (
            self._closed or not isinstance(session_id, str) or not session_id.isascii()
            or len(session_id) != 48 or session is None
            or not secrets.compare_digest(session.session_id, session_id)
        ):
            raise EnrollmentError("Unknown Codex enrollment session. Start sign-in again.")
        if cleanup:
            return session
        if session.cancelled:
            raise EnrollmentError("This Codex sign-in was cancelled. Retry cancellation to finish cleaning up its private files.")
        if time.monotonic() >= session.expires_at:
            session.cancelled = True
            if self._cleanup(session):
                self._forget(session)
            raise EnrollmentError("Codex enrollment expired. Stop its terminal login and start again.")
        return session

    def _forget(self, session: _Session) -> None:
        self._closed_session_id = session.session_id
        self._session = None

    @contextmanager
    def _home(self, session: _Session):
        with _directory(session.directory) as descriptor:
            info = (os.fstat(descriptor) if descriptor is not None
                    else session.directory.stat(follow_symlinks=False))
            if (
                not os.path.samestat(info, session.directory_stat)
                or not stat.S_ISDIR(info.st_mode) or _linked(info)
                or (os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o077))
            ):
                raise OSError("The private enrollment directory changed.")
            yield descriptor

    def prepare(self, *, number: str | None = None, confirm: bool = False) -> dict:
        self._confirm(confirm)
        with self._lock:
            if self._closed:
                raise EnrollmentError("This Codex enrollment controller is closed.")
            if number is not None and (
                not isinstance(number, str) or len(number) > 20 or not number.isascii()
                or not number.isdecimal() or int(number) <= 0 or str(int(number)) != number
            ):
                raise EnrollmentError("Choose a saved Codex slot number for repair.")
            if self._session is not None:
                if (
                    not self._session.cancelled and self._session.account is None
                    and time.monotonic() < self._session.expires_at
                ):
                    expected_number = self._session.expected.number if self._session.expected else None
                    if number == expected_number:
                        return self._prepared(self._session)
                    raise EnrollmentError(
                        "A different Codex sign-in is pending. Stop that terminal login, "
                        "then quit and reopen Agent Switch before choosing another account."
                    )
                if not self._cleanup(self._session):
                    raise EnrollmentError("Stop the previous terminal login before preparing another sign-in.")
                self._forget(self._session)
            expected = None
            if number is not None:
                try:
                    with self.switcher.store.lock():
                        expected = self.switcher.store.get(number)
                except (OSError, ClaudeSwitchError):
                    raise EnrollmentError("Cannot read the saved Codex accounts. Nothing was changed.") from None
                if expected is None or not expected.account_id:
                    raise EnrollmentError("That saved Codex slot is unavailable. Refresh the account list.")
            session_id = secrets.token_hex(24)
            try:
                root = self.switcher.store.root.absolute()
                root.mkdir(parents=True, exist_ok=True)
                if _linked(root.stat(follow_symlinks=False)):
                    raise OSError("The Codex backup directory is linked.")
                root = root.resolve(strict=True)
                directory = root / f".enrollment-{session_id}"
                with _directory(root) as root_fd:
                    os.mkdir(directory if root_fd is None else directory.name, 0o700, dir_fd=root_fd)
                    info = os.stat(directory if root_fd is None else directory.name,
                                   dir_fd=root_fd, follow_symlinks=False)
                session = _Session(session_id, directory, info,
                                   time.monotonic() + SESSION_TTL_SECONDS, expected)
                self._session = session
                with self._home(session) as descriptor:
                    fd = os.open(directory / "config.toml" if descriptor is None else "config.toml",
                                 os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=descriptor)
                    with os.fdopen(fd, "w", encoding="utf-8") as config:
                        config.write('cli_auth_credentials_store = "file"\n')
            except OSError:
                if self._session is not None:
                    self._session.cancelled = True
                    if self._cleanup(self._session):
                        self._forget(self._session)
                raise EnrollmentError("Cannot prepare a private Codex sign-in directory.") from None
            return self._prepared(session)

    def _prepared(self, session: _Session) -> dict:
        try:
            with self._home(session):
                pass
        except OSError:
            raise EnrollmentError("The private sign-in directory changed. Stop that terminal login, then quit and reopen Agent Switch.") from None
        if sys.platform == "win32":
            quoted_home = str(session.directory).replace("'", "''")
            command = (
                "& { $hadCodexHome = Test-Path Env:CODEX_HOME; $oldCodexHome = $env:CODEX_HOME; "
                f"try {{ $env:CODEX_HOME = '{quoted_home}'; "
                "& codex '-c' 'cli_auth_credentials_store=\"file\"' 'login' } "
                "finally { if ($hadCodexHome) { $env:CODEX_HOME = $oldCodexHome } "
                "else { Remove-Item Env:CODEX_HOME -ErrorAction SilentlyContinue } } }"
            )
            shell = "powershell"
        else:
            command = shlex.join(["env", f"CODEX_HOME={session.directory}", "codex", *LOGIN_ARGUMENTS])
            shell = "sh"
        return {
            "ok": True, "sessionId": session.session_id, "command": command, "shell": shell,
            "instructions": (
                "Run this exact command in a terminal and finish the official Codex sign-in. "
                "Wait for it to exit before saving. This private home starts empty; your current "
                "login is not copied or replaced. Stop the terminal login before cancelling. "
                "Already revoked accounts need a fresh sign-in."
            ),
        }

    def cli_environment(self, session_id: str) -> dict[str, str]:
        """Build the fixed CLI child's environment without inherited auth overrides."""
        with self._lock:
            session = self._get(session_id)
            if session.account is not None:
                raise EnrollmentError("This Codex sign-in was already saved.")
            try:
                with self._home(session):
                    pass
            except OSError:
                raise EnrollmentError("The private Codex sign-in directory is unavailable.") from None
            environment = {
                key: value for key, value in os.environ.items()
                if not key.upper().startswith(("CODEX_", "OPENAI_", "CHATGPT_"))
            }
            environment["CODEX_HOME"] = str(session.directory)
            return environment

    def _read_login(self, session: _Session) -> dict:
        for attempt in range(3):
            try:
                with self._home(session) as directory_fd:
                    if directory_fd is None:
                        import msvcrt

                        path = session.directory / "auth.json"
                        handle, close = _windows_handle(path, directory=False)
                        try:
                            if _linked(path.stat(follow_symlinks=False)):
                                raise OSError("Linked login files are not supported.")
                            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
                        except BaseException:
                            close(handle)
                            raise
                    else:
                        fd = os.open("auth.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                     dir_fd=directory_fd)
                    with os.fdopen(fd, "rb") as auth:
                        before = os.fstat(auth.fileno())
                        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                                or before.st_size > MAX_AUTH_BYTES):
                            raise OSError("Invalid login file.")
                        raw = auth.read(MAX_AUTH_BYTES + 1)
                        current = os.stat(session.directory / "auth.json" if directory_fd is None else "auth.json",
                                          dir_fd=directory_fd, follow_symlinks=False)
                        after = os.fstat(auth.fileno())
                    if (
                        len(raw) > MAX_AUTH_BYTES or before.st_size != len(raw)
                        or before.st_mtime_ns != after.st_mtime_ns
                        or before.st_ctime_ns != after.st_ctime_ns
                        or before.st_size != after.st_size
                        or not os.path.samestat(before, current) or _linked(current)
                        or after.st_mtime_ns != current.st_mtime_ns
                        or after.st_size != current.st_size
                    ):
                        raise ValueError("Login is still being written.")
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError("Login is not an object.")
                return data
            except (OSError, ValueError, UnicodeError, RecursionError):
                if attempt < 2:
                    time.sleep(0.02)
        raise EnrollmentError(
            "No complete, safe auth.json was found in this sign-in session. "
            "Wait for the terminal login to finish, then try saving again."
        ) from None

    def complete(self, session_id: str, *, confirm: bool = False) -> dict:
        self._confirm(confirm)
        with self._lock:
            session = self._get(session_id)
            if session.result is not None:
                return {**session.result, "account": dict(session.result["account"])}
            credentials = self._read_login(session)
            try:
                account = self.switcher.import_login(credentials, alias=self._alias, expected=session.expected)
            except (ValidationError, SwitchError) as error:
                raise EnrollmentError(str(error)) from None
            except (OSError, ClaudeSwitchError, ValueError):
                raise EnrollmentError("Could not save the Codex login. Existing accounts were not replaced.") from None
            session.account = account
            session.expected = None
            cleaned = self._cleanup(session)
            result = {
                "ok": True, "account": {"number": account.number, **account.to_dict()},
                "message": "Codex login saved. The current login has not changed; activate it separately.",
                "activationRequired": True,
            }
            if not cleaned:
                result["warning"] = True
                result["cleanupWarning"] = _CLEANUP_WARNING
                result["message"] += " " + _CLEANUP_WARNING
            session.result = result
            return {**result, "account": dict(result["account"])}

    def activate(self, session_id: str, *, confirm: bool = False) -> dict:
        self._confirm(confirm)
        with self._lock:
            session = self._get(session_id)
            if session.account is None:
                raise EnrollmentError("Save the Codex sign-in before activating it.")
            try:
                result = self.switcher.switch_to(session.account.number, allow_same=True, expected=session.account)
            except (SwitchError, ValidationError, AccountNotFoundError) as error:
                raise EnrollmentError(str(error)) from None
            except (OSError, CodexAuthError, ClaudeSwitchError, ValueError):
                raise EnrollmentError("Could not activate the saved Codex login. It remains saved; check the current login before retrying.") from None
            return {
                "ok": True, "account": {"number": result.account.number, **result.account.to_dict()},
                "message": "Saved Codex login activated.", "activationRequired": False,
                "restartRequired": result.restart_required,
            }

    def _cleanup(self, session: _Session) -> bool:
        try:
            with self._home(session) as descriptor:
                if os.name == "nt":
                    _clear_windows_directory(session.directory, session.directory_stat)
                elif descriptor is not None and shutil.rmtree.avoids_symlink_attacks:
                    for name in os.listdir(descriptor):
                        info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                        if stat.S_ISDIR(info.st_mode):
                            shutil.rmtree(name, dir_fd=descriptor)
                        else:
                            os.unlink(name, dir_fd=descriptor)
                else:
                    for child in session.directory.iterdir():
                        info = child.stat(follow_symlinks=False)
                        if stat.S_ISDIR(info.st_mode):
                            os.rmdir(child)
                        else:
                            child.unlink()
            with _directory(session.directory.parent) as parent_fd:
                info = os.stat(session.directory if parent_fd is None else session.directory.name,
                               dir_fd=parent_fd, follow_symlinks=False)
                if not os.path.samestat(info, session.directory_stat) or _linked(info):
                    return False
                os.rmdir(session.directory if parent_fd is None else session.directory.name, dir_fd=parent_fd)
            return True
        except FileNotFoundError:
            return not session.directory.exists() and not session.directory.is_symlink()
        except (OSError, RecursionError):
            return False

    def cancel(self, session_id: str, *, confirm: bool = False) -> dict:
        self._confirm(confirm)
        with self._lock:
            if (
                self._closed_session_id is not None and isinstance(session_id, str)
                and session_id == self._closed_session_id
            ):
                cleaned = True
            else:
                session = self._get(session_id, cleanup=True)
                session.cancelled = True
                cleaned = self._cleanup(session)
                if cleaned:
                    self._forget(session)
            result = {
                "ok": cleaned, "warning": not cleaned,
                "message": (
                    "Codex sign-in session closed. Saved accounts and the current login are unchanged."
                    if cleaned else
                    "Codex sign-in cancelled. " + _CLEANUP_WARNING
                ),
            }
            if not cleaned:
                result["cleanupWarning"] = _CLEANUP_WARNING
            return result

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._session is not None:
                self._session.cancelled = True
                if self._cleanup(self._session):
                    self._forget(self._session)
