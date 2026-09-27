"""Explicit, offline, copy-only import from fixed historical account stores."""

from __future__ import annotations

import base64
import ctypes
import json
import math
import os
import re
import stat
import subprocess
import sys
import tomllib
import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path

from agents_switcher import macos_keychain, oauth
from agents_switcher.codex import desktop as codex_desktop
from agents_switcher.codex.identity import OPENAI_AUTH_CLAIM, decode_jwt_claims, identity_from_auth
from agents_switcher.codex.paths import get_auth_file, get_config_file
from agents_switcher.credentials import SECURITY_SERVICE, _active_oauth_keychain_services, looks_like_api_key
from agents_switcher.dirlock import directory_lock
from agents_switcher.exceptions import MigrationError
from agents_switcher.locking import FileLock
from agents_switcher.migrations import LEGACY_KEYRING_SERVICE, LEGACY_SECURITY_SERVICE
from agents_switcher.models import Platform, get_timestamp, normalize_alias
from agents_switcher.paths import (
    get_backup_root,
    get_credentials_path,
    get_global_config_path,
    get_legacy_import_roots,
    migration_flag_for,
)
from agents_switcher.settings import SETTING_SPECS


_STATE = "legacy-import.json"
_LIMIT = 16 * 1024 * 1024
_PREFERENCES = ("settings.json", "ui-preferences.json", "menubar_settings.json")
_STOP_WARNING = "Stop all other switchers, automation, Claude Code and Codex clients before importing. Historical tools do not share Agent Switch's locks."
_EXCLUDED_WARNING = "Desktop profiles/cookies, terminal session trees, mappings, caches, logs, locks and recovery artifacts are not imported."
_OPAQUE_WARNING = "Claude tokens are opaque: saved roster/config identities are checked, but token ownership cannot be independently verified offline. No provider requests are made."


def _fail(message: str) -> None:
    raise MigrationError(message) from None


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _json(raw: bytes | str) -> dict:
    try:
        data = json.loads(raw, object_pairs_hook=_unique, parse_constant=lambda _: _fail("Invalid JSON data."))
        if not isinstance(data, dict):
            raise ValueError
        return data
    except (ValueError, UnicodeError, RecursionError):
        _fail("An import file is not a valid JSON object. Repair the source before importing.")


def _encoded(data: dict) -> bytes:
    return json.dumps(data, indent=2, allow_nan=False).encode("utf-8")


def _linked(info) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _win_open(path: Path, *, directory: bool) -> int:
    """Open without following reparse points or allowing the path to be moved."""
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW(str(path), 0 if directory else 0x80000000, 3, None, 3, 0x02200000, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    attributes = (wintypes.DWORD * 2)()
    if (not kernel.GetFileInformationByHandleEx(handle, 9, attributes, ctypes.sizeof(attributes))
            or attributes[0] & 0x400 or bool(attributes[0] & 0x10) != directory):
        kernel.CloseHandle(handle)
        _fail("Linked or non-regular import paths are not supported.")
    return handle


@contextmanager
def _directory(path: Path, *, create: bool = False):
    """Pin each ancestor, refusing links, including Windows junctions."""
    path = path.absolute()
    if ".." in path.parts:
        _fail("Unsafe import path.")
    with ExitStack() as stack:
        if os.name == "nt":
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            current = Path(path.anchor)
            for name in ("", *path.parts[1:]):
                if name:
                    current /= name
                try:
                    handle = _win_open(current, directory=True)
                except FileNotFoundError:
                    if not create:
                        raise
                    current.mkdir(mode=0o700)
                    handle = _win_open(current, directory=True)
                stack.callback(kernel.CloseHandle, handle)
            yield None
        else:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            descriptor = os.open(path.anchor, flags)
            stack.callback(os.close, descriptor)
            for name in path.parts[1:]:
                try:
                    child = os.open(name, flags, dir_fd=descriptor)
                except FileNotFoundError:
                    if not create:
                        raise
                    try:
                        os.mkdir(name, mode=0o700, dir_fd=descriptor)
                    except FileExistsError:
                        pass
                    child = os.open(name, flags, dir_fd=descriptor)
                stack.callback(os.close, child)
                descriptor = child
            if create:
                os.fchmod(descriptor, 0o700)
            yield descriptor


def _stat(path: Path):
    try:
        with _directory(path.parent) as parent:
            return os.stat(path if parent is None else path.name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _read(path: Path, *, optional: bool = False, linked: bool = False) -> bytes | None:
    try:
        with _directory(path.parent) as parent:
            if os.name == "nt":
                import msvcrt

                descriptor = msvcrt.open_osfhandle(_win_open(path, directory=False), os.O_RDONLY | os.O_BINARY)
            else:
                descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or _linked(info) or (not linked and info.st_nlink != 1):
                    _fail("Linked or non-regular import files are not supported.")
                raw = stream.read(_LIMIT + 1)
                if len(raw) > _LIMIT:
                    _fail("An import file is too large.")
                if _stamp(info) != _stamp(os.fstat(stream.fileno())):
                    _fail("An import file changed while it was being read. Stop other tools and retry.")
                return raw
    except FileNotFoundError:
        if optional:
            return None
        _fail("An account is missing its saved config or credentials. Repair the source before importing.")


def _entries(path: Path) -> dict[str, os.stat_result]:
    try:
        with _directory(path) as descriptor:
            result = {}
            for name in os.listdir(path if descriptor is None else descriptor):
                result[name] = os.stat(path / name if descriptor is None else name, dir_fd=descriptor, follow_symlinks=False)
            return result
    except FileNotFoundError:
        return {}


def _stamp(info):
    if info is None:
        return None
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _write_new(path: Path, raw: bytes) -> None:
    with _directory(path.parent, create=True) as parent:
        descriptor = os.open(path if parent is None else path.name,
                             os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                             0o600, dir_fd=parent)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())


def _unlink_owned(path: Path, identity) -> bool:
    with _directory(path.parent) as parent:
        try:
            current = os.stat(path if parent is None else path.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return True
        if _linked(current) or not os.path.samestat(current, identity):
            return False
        os.unlink(path if parent is None else path.name, dir_fd=parent)
        return True


def _check_quiescent() -> None:
    """Use the strict native scan; consent also covers other machines/tools."""
    try:
        if sys.platform not in {"darwin", "linux", "win32"}:
            _fail("Process checks are unavailable on this platform; import is blocked.")
        processes = _windows_processes() if sys.platform == "win32" else codex_desktop._posix_processes()
        names = {"claude", "codex", "cswap", "claude-swap", "agent-switch", "agents-switcher", "agent-switch-backend"}
        for process in processes:
            if process.pid == os.getpid():
                continue
            executable = process.executable.replace("\\", "/")
            basename = executable.rsplit("/", 1)[-1].lower().removesuffix(".exe")
            arguments = process.arguments
            if not arguments and basename.startswith(("python", "pypy")):
                reader = codex_desktop._read_mac_arguments if sys.platform == "darwin" else codex_desktop._read_linux_arguments
                _, arguments = reader(process.pid)
            python_manager = False
            if basename.startswith(("python", "pypy")):
                remaining = iter(arguments[1:])
                for argument in remaining:
                    if argument == "-m":
                        python_manager = next(remaining, "").split(".")[0] in {"claude_swap", "agents_switcher"}
                        break
                    if argument == "-c":
                        break
                    if argument in {"-W", "-X"}:
                        next(remaining, None)
                        continue
                    if argument.startswith("-"):
                        continue
                    python_manager = argument.replace("\\", "/").rsplit("/", 1)[-1].lower().removesuffix(".exe") in names
                    break
            provider_script = any("claude-code/" in arg.replace("\\", "/") for arg in arguments)
            if (basename in names or python_manager or provider_script
                    or codex_desktop._codex_process(process) or re.search(r"/claude/versions/[^/]+$", executable, re.IGNORECASE)
                    or "/Claude.app/" in executable or "/Codex.app/" in executable):
                _fail("Other switcher or provider processes are running. Stop them and their automation before importing.")
    except MigrationError:
        raise
    except Exception:
        _fail("Could not reliably check running processes. Import is blocked; check permissions and retry.")


def _windows_processes():
    script = r'''
$ErrorActionPreference = 'Stop'
$PSModuleAutoLoadingPreference = 'None'
try {
    Import-Module "$PSHOME\Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1" -ErrorAction Stop
    Import-Module "$PSHOME\Modules\CimCmdlets\CimCmdlets.psd1" -ErrorAction Stop
    $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $rows = @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
        $_.Name -match '^(codex.*|claude.*|cswap|agent-switch.*|agents-switcher.*|python.*|pypy.*|node|nodejs|bun)(\.exe)?$' -or
        $_.ExecutablePath -match '[\\/]claude[\\/]versions[\\/]'
    } | ForEach-Object {
        $owner = Invoke-CimMethod -InputObject $_ -MethodName GetOwnerSid -ErrorAction Stop
        if ($owner.ReturnValue -ne 0 -or -not $owner.Sid) { throw 'owner unavailable' }
        if ($owner.Sid -eq $sid) {
            [pscustomobject]@{ Pid = $_.ProcessId; Executable = $_.ExecutablePath; Command = $_.CommandLine }
        }
    })
    [pscustomobject]@{ Complete = $true; Processes = $rows } | ConvertTo-Json -Depth 3 -Compress
} catch { exit 1 }
'''
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, text=True, timeout=10, check=True)
    data = _json(result.stdout)
    if result.returncode != 0 or data.get("Complete") is not True or not isinstance(data.get("Processes"), list):
        raise ValueError
    processes = []
    seen = set()
    for row in data["Processes"]:
        if not isinstance(row, dict):
            raise ValueError
        pid, executable, command = row.get("Pid"), row.get("Executable"), row.get("Command")
        if (type(pid) is not int or not 0 < pid < 2**32 or pid in seen
                or not isinstance(executable, str) or not executable
                or not isinstance(command, str) or not command.strip() or command.count('"') % 2):
            raise ValueError
        seen.add(pid)
        arguments = tuple(quoted or plain for quoted, plain in re.findall(r'"([^"\r\n]*)"|([^\s"]+)', command))
        processes.append(codex_desktop._Process(pid, 0, executable, arguments))
    return processes


def _roster(raw: bytes | None, *, codex: bool) -> dict:
    if raw is None:
        return {"accounts": {}}
    data = _json(raw)
    if not isinstance(data.get("accounts"), dict):
        _fail("A historical account roster is malformed.")
    if codex and (type(data.get("version")) is not int or data["version"] != 1):
        _fail("Unsupported Codex roster version.")
    identities = set()
    aliases = set()
    for number, row in data["accounts"].items():
        if (not isinstance(number, str) or re.fullmatch(r"[1-9][0-9]{0,8}", number) is None
                or not isinstance(row, dict)):
            _fail("A historical account roster contains an invalid slot.")
        email = row.get("email", "")
        if (not isinstance(email, str) or len(email) > 254 or (not email and not codex)
                or any(ord(char) < 32 or char in '/\\<>:"|?*' for char in email)
                or (email and "@" not in email)):
            _fail("A historical account roster contains an invalid email label.")
        for field in ("uuid", "organizationUuid", "organizationName", "accountId", "plan", "added", "alias"):
            if field in row and not isinstance(row[field], str):
                _fail("A historical account roster contains invalid metadata.")
        if "disabled" in row and type(row["disabled"]) is not bool:
            _fail("A historical account roster contains an invalid disabled flag.")
        if row.get("kind", "oauth") not in {"api_key", "oauth"}:
            _fail("Unsupported Claude credential kind.")
        identity = row.get("accountId") if codex else (email, row.get("organizationUuid", ""))
        if not identity or identity in identities:
            _fail("A historical account roster contains an ambiguous identity.")
        identities.add(identity)
        alias = row.get("alias")
        if alias:
            try:
                normalized = normalize_alias(alias)
            except ValueError:
                _fail("A historical account alias is invalid.")
            if normalized in aliases:
                _fail("Historical account aliases are ambiguous.")
            aliases.add(normalized)
    active = data.get("activeAccountNumber")
    if active is not None and (type(active) not in {str, int} or str(active) not in data["accounts"]):
        _fail("A historical roster has an invalid active account.")
    if not codex:
        sequence = data.get("sequence")
        if (not isinstance(sequence, list) or any(type(n) is not int for n in sequence)
                or len(sequence) != len(set(sequence)) or {str(n) for n in sequence} != set(data["accounts"])):
            _fail("The historical Claude account sequence is malformed.")
    return data


class _Source:
    def __init__(self, root: Path):
        self.root = root
        self.reads = {}
        self.secrets = {}
        self.directories = {}
        for relative in ("", "configs", "credentials", "codex", "codex/credentials"):
            directory = root / relative
            entries = _entries(directory)
            info = _stat(directory)
            self.directories[directory] = (frozenset(entries), _stamp(info)[:3] if info else None)
            if any(name.endswith(".lock") and stat.S_ISDIR(info.st_mode) for name, info in entries.items()):
                _fail("A historical store is locked. Stop other tools before importing.")
        if _stat(migration_flag_for(root)) is not None:
            _fail("A historical migration is incomplete. Repair that store before importing.")

    def read(self, path: Path, *, optional: bool = False):
        raw = _read(path, optional=optional)
        self.reads[path] = (raw, _stamp(_stat(path)))
        return raw

    def secret(self, service: str, username: str, *, keyring: bool = False):
        try:
            if keyring:
                import keyring as backend
            else:
                backend = macos_keychain
            value = backend.get_password(service, username)
            if value is not None and (not isinstance(value, str) or not value):
                _fail("A historical credential entry is malformed.")
            self.secrets[(service, username, keyring)] = value
            return value
        except MigrationError:
            raise
        except Exception:
            _fail("A credential store is denied, locked or unavailable. Nothing was imported.")

    def verify(self):
        for path, (raw, stamp) in self.reads.items():
            if _read(path, optional=True) != raw or _stamp(_stat(path)) != stamp:
                _fail("Source data changed during import. Stop other tools and retry.")
        for path, (names, identity) in self.directories.items():
            info = _stat(path)
            if frozenset(_entries(path)) != names or (_stamp(info)[:3] if info else None) != identity:
                _fail("Source directories changed during import. Stop other tools and retry.")
        for (service, username, keyring), value in list(self.secrets.items()):
            if self.secret(service, username, keyring=keyring) != value:
                _fail("Source credentials changed during import. Stop other tools and retry.")


class LegacyImport:
    """Root injection is for isolated tests; callers select only fixed source IDs."""

    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root is not None else get_backup_root()

    def _imported(self) -> bool:
        raw = _read(self.root / _STATE, optional=True)
        if raw is None:
            return False
        data = _json(raw)
        if (set(data) != {"version", "source", "importedAt", "counts"}
                or type(data["version"]) is not int or data["version"] != 1
                or data["source"] not in {"legacy", "xdg"}
                or not isinstance(data["importedAt"], str)
                or not isinstance(data["counts"], dict) or set(data["counts"]) != {"claude", "codex"}
                or any(type(n) is not int or n < 0 for n in data["counts"].values())):
            _fail("The import record is unreadable. Inspect the destination before retrying.")
        return True

    def _destination_empty(self):
        destination = self.root.absolute()
        if any(destination == historical.absolute() or destination in historical.absolute().parents
               or historical.absolute() in destination.parents for historical in get_legacy_import_roots().values()):
            _fail("The destination overlaps a historical store. Choose an independent data location before importing.")
        allowed = {"cache", "configs", "credentials", "codex", "web-url", ".lock", ".legacy-import.lock",
                   ".ui-preferences.lock", *_PREFERENCES}
        for name, info in _entries(self.root).items():
            if _linked(info):
                _fail("The destination contains linked paths; import is blocked.")
            if name not in allowed and re.fullmatch(r"agents-switcher\.log(?:\.[0-9]+)?", name) is None:
                _fail("The destination already contains account data or an incomplete import. Refusing to merge or overwrite it.")
        for directory in ("configs", "credentials", "codex/credentials"):
            if _entries(self.root / directory):
                _fail("The destination already contains saved account data. Refusing to merge or overwrite it.")
        for name, info in _entries(self.root / "codex").items():
            if _linked(info) or name not in {"credentials", "cache", ".lock"}:
                _fail("The destination already contains Codex data. Refusing to merge or overwrite it.")

    def status(self) -> dict:
        """Read only rosters and the receipt, never credential values or Keychain."""
        result = {"destination": str(self.root), "imported": False, "canImport": False,
                  "sources": [], "warnings": []}
        destination_ready = False
        try:
            result["imported"] = self._imported()
            if not result["imported"]:
                self._destination_empty()
                destination_ready = True
        except MigrationError as exc:
            result["warnings"].append(str(exc))
        except (OSError, ValueError, TypeError, RecursionError):
            result["warnings"].append("The destination could not be safely inspected.")
        for source, root in get_legacy_import_roots().items():
            entry = {"id": source, "label": "Historical home store" if source == "legacy" else "Historical XDG store"}
            try:
                if _stat(root) is None:
                    continue
                claude = _roster(_read(root / "sequence.json", optional=True), codex=False)
                codex = _roster(_read(root / "codex/accounts.json", optional=True), codex=True)
                entry["counts"] = {"claude": len(claude["accounts"]), "codex": len(codex["accounts"])}
                if any(entry["counts"].values()) and destination_ready:
                    result["canImport"] = True
            except (MigrationError, OSError, ValueError, TypeError, RecursionError):
                entry["error"] = "This source is unreadable, linked or malformed. Repair it before importing."
            result["sources"].append(entry)
        if result["sources"]:
            result["warnings"].extend([_STOP_WARNING, _EXCLUDED_WARNING])
        if len(result["sources"]) > 1:
            result["warnings"].append("Multiple historical locations exist. Choose one source; they will not be merged.")
        return result

    def _claude_credentials(self, snapshot: _Source, number: str, row: dict, accounts: dict) -> str:
        email = row["email"]
        numbers = [number]
        if sum(account["email"] == email for account in accounts.values()) == 1:
            numbers.append("None")
        platform = Platform.detect()
        for candidate in numbers:
            raw = snapshot.read(snapshot.root / "credentials" / f".creds-{candidate}-{email}.enc", optional=True)
            if raw is not None:
                try:
                    return base64.b64decode(raw.strip(), validate=True).decode("utf-8")
                except (ValueError, UnicodeError):
                    _fail("A saved Claude credential is corrupt.")
            username = f"account-{candidate}-{email}"
            if platform == Platform.MACOS:
                value = snapshot.secret(LEGACY_SECURITY_SERVICE, username)
                if value is not None:
                    return value
                value = snapshot.secret(LEGACY_KEYRING_SERVICE, username)
                if value is not None:
                    return value
            elif platform == Platform.WINDOWS:
                value = snapshot.secret(LEGACY_KEYRING_SERVICE, username, keyring=True)
                if value is not None:
                    return value
        _fail("A saved Claude credential is missing. Repair the source before importing.")

    def _validate_claude(self, row: dict, config: dict, credential: str) -> None:
        account = config.get("oauthAccount")
        if not isinstance(account, dict) or account.get("emailAddress") != row["email"]:
            _fail("A saved Claude config does not match its roster identity.")
        for field, config_field in (("uuid", "accountUuid"), ("organizationUuid", "organizationUuid")):
            value = account.get(config_field, "") or ""
            if not isinstance(value, str) or (field in row and row[field] != value):
                _fail("A saved Claude config does not match its roster identity.")
            row[field] = value
        row.setdefault("organizationName", account.get("organizationName", "") or "")
        if not isinstance(row["organizationName"], str):
            _fail("A saved Claude organization label is malformed.")
        if row.get("kind") == "api_key":
            if not looks_like_api_key(credential):
                _fail("A saved Claude API-key credential is malformed.")
            if config.get("primaryApiKey", credential) != credential:
                _fail("A saved Claude API-key config does not match its credential.")
            return
        data = _json(credential)
        token = data.get("claudeAiOauth")
        if (not isinstance(token, dict) or not isinstance(token.get("accessToken"), str) or not token["accessToken"]
                or ("refreshToken" in token and not isinstance(token["refreshToken"], str))):
            _fail("A saved Claude OAuth credential is malformed.")
        for field, expected in (("accountUuid", row["uuid"]), ("organizationUuid", row["organizationUuid"]), ("emailAddress", row["email"])):
            if field in token and token[field] != expected:
                _fail("A saved Claude credential has conflicting identity metadata.")

    def _validate_codex(self, row: dict, auth: dict) -> None:
        tokens = auth.get("tokens")
        if (auth.get("OPENAI_API_KEY") or not isinstance(tokens, dict)
                or any(not isinstance(tokens.get(field), str) or not tokens[field]
                       for field in ("access_token", "refresh_token", "id_token"))):
            _fail("A saved Codex credential is not a supported OAuth login.")
        identity = identity_from_auth(auth)
        claims = decode_jwt_claims(tokens["id_token"])
        if claims is None:
            _fail("A saved Codex identity token is malformed.")
        namespace = claims.get(OPENAI_AUTH_CLAIM, {})
        if (not isinstance(namespace, dict) or identity is None or not identity.account_id
                or identity.account_id != row["accountId"]
                or (row.get("email") and identity.email != row["email"])
                or (tokens.get("account_id") and tokens["account_id"] != identity.account_id)):
            _fail("A saved Codex credential does not match its roster identity.")

    def _preferences(self, snapshot: _Source, files: dict, warnings: list):
        for name in _PREFERENCES:
            raw = snapshot.read(snapshot.root / name, optional=True)
            if raw is None:
                continue
            data = _json(raw)
            if name == "settings.json":
                if type(data.get("version", 1)) is not int or data.get("version", 1) != 1:
                    _fail("Unsupported settings version.")
                selected = {"version": 1}
                for spec in SETTING_SPECS.values():
                    section = data.get(spec.section, {})
                    if not isinstance(section, dict):
                        _fail("Historical settings are malformed.")
                    if spec.json_key not in section:
                        continue
                    value = section[spec.json_key]
                    valid = ((spec.kind == "bool" and type(value) is bool)
                             or (spec.kind == "choice" and isinstance(value, str) and value in spec.choices)
                             or (spec.kind == "string" and (value is None or isinstance(value, str)))
                             or (spec.kind in {"int", "float"} and type(value) in ({int} if spec.kind == "int" else {int, float})
                                 and math.isfinite(value) and spec.lo <= value <= spec.hi))
                    if not valid:
                        _fail("A supported historical setting has an invalid value.")
                    selected.setdefault(spec.section, {})[spec.json_key] = value
                data = selected
            elif name == "ui-preferences.json":
                if (type(data.get("version")) is not int or data["version"] != 1
                        or data.get("theme") not in {"system", "light", "dark"}
                        or type(data.get("profileNoticeVersion")) is not int or data["profileNoticeVersion"] not in {0, 1}):
                    _fail("Historical interface preferences are malformed.")
                data = {key: data[key] for key in ("version", "theme", "profileNoticeVersion")}
            else:
                from agents_switcher.menubar import MenuBarSettings, TITLE_PCT_CHOICES, REFRESH_CHOICES

                defaults = vars(MenuBarSettings())
                if any(key in data and type(data[key]) is not type(default) for key, default in defaults.items()):
                    _fail("Historical menu bar settings are malformed.")
                if data.get("title_pct", "both") not in TITLE_PCT_CHOICES or data.get("refresh_interval", 60) not in REFRESH_CHOICES:
                    _fail("Historical menu bar settings are malformed.")
                data = {key: value for key, value in data.items() if key in defaults}
                if data.get("auto_switch_enabled"):
                    data["auto_switch_enabled"] = False
                    warnings.append("The menu bar's automatic-switch toggle was disabled; enable automation deliberately after import.")
            if _stat(self.root / name) is not None:
                warnings.append(f"Existing {name} was kept; its historical preferences were not copied.")
            else:
                files[name] = _encoded(data)

    def _live_credentials(self, snapshot: _Source, claude: dict, credentials: dict, codex: dict, files: dict, warnings: list):
        if claude["accounts"]:
            config = snapshot.read(get_global_config_path(), optional=True)
            if config is not None:
                account = _json(config).get("oauthAccount")
                matches = [(number, row) for number, row in claude["accounts"].items()
                           if isinstance(account, dict) and account.get("emailAddress") == row["email"]
                           and (account.get("organizationUuid") or "") == row["organizationUuid"]
                           and (account.get("accountUuid") or "") == row["uuid"] and row.get("kind") != "api_key"]
                if len(matches) == 1:
                    number, row = matches[0]
                    live = None
                    if Platform.detect() == Platform.MACOS:
                        for service in _active_oauth_keychain_services():
                            live = snapshot.secret(service, macos_keychain.keychain_account_name())
                            if live is not None:
                                break
                    if live is None:
                        raw = snapshot.read(get_credentials_path(), optional=True)
                        live = raw.decode("utf-8") if raw is not None else None
                    if live:
                        self._validate_claude(dict(row), _json(config), live)
                        if (oauth.credential_fingerprint(live)
                                and oauth.credential_fingerprint(live) == oauth.credential_fingerprint(credentials[number])):
                            credentials[number] = live
                        elif live != credentials[number]:
                            warnings.append("A live Claude login had a different token lineage. Saved credentials were kept; re-add that login after import if needed.")
        if codex["accounts"]:
            config = snapshot.read(get_config_file(), optional=True)
            if config is not None:
                try:
                    mode = tomllib.loads(config.decode("utf-8")).get("cli_auth_credentials_store", "file")
                except (ValueError, UnicodeError):
                    _fail("The live Codex storage configuration is unreadable.")
                if mode != "file":
                    warnings.append("The live Codex login was not read because its storage mode is not file-backed.")
                    return
            raw = snapshot.read(get_auth_file(), optional=True)
            if raw is not None:
                live = _json(raw)
                identity = identity_from_auth(live)
                matches = [(number, row) for number, row in codex["accounts"].items()
                           if identity is not None and identity.account_id == row["accountId"]]
                if len(matches) == 1:
                    number, row = matches[0]
                    self._validate_codex(row, live)
                    files[f"codex/credentials/{number}.json"] = base64.b64encode(_encoded(live))

    def import_accounts(self, source: str, *, confirm=False) -> dict:
        """Consent confirms that other tools/providers, including remote ones, stopped."""
        if confirm is not True:
            _fail("Confirm the copy and that all other switchers, automation and provider clients are stopped.")
        roots = get_legacy_import_roots()
        if not isinstance(source, str) or source not in roots:
            _fail("Choose a fixed historical source ID: legacy or xdg where available.")
        sys.audit("agents_switcher.legacy_import", str(self.root))
        warnings = [_EXCLUDED_WARNING]
        try:
            _check_quiescent()
            if self._imported():
                _fail("Historical accounts were already imported. No data was changed.")
            self._destination_empty()
            root = roots[source]
            if _stat(root) is None:
                _fail("The selected historical source is unavailable.")
            with _directory(self.root, create=True):
                with FileLock(self.root / ".lock"), directory_lock(self.root / ".legacy-import.lock", timeout=1):
                    with _directory(self.root / "codex", create=True):
                        with directory_lock(self.root / "codex/.lock", timeout=1), directory_lock(self.root / ".ui-preferences.lock", timeout=1):
                            return self._copy(root, source, warnings)
        except MigrationError:
            raise
        except Exception:
            _fail("Import could not complete safely. Check permissions and that other tools are stopped; no source data was changed.")

    def _copy(self, root: Path, source: str, warnings: list) -> dict:
        self._destination_empty()
        snapshot = _Source(root)
        claude = _roster(snapshot.read(root / "sequence.json", optional=True), codex=False)
        codex = _roster(snapshot.read(root / "codex/accounts.json", optional=True), codex=True)
        counts = {"claude": len(claude["accounts"]), "codex": len(codex["accounts"])}
        if not any(counts.values()):
            _fail("The selected source contains no saved accounts.")
        files = {}
        credentials = {}
        for number, row in claude["accounts"].items():
            name = f"configs/.claude-config-{number}-{row['email']}.json"
            config = _json(snapshot.read(root / name))
            credential = self._claude_credentials(snapshot, number, row, claude["accounts"])
            self._validate_claude(row, config, credential)
            credentials[number] = credential
            files[name] = _encoded(config)
        for number, row in codex["accounts"].items():
            name = f"codex/credentials/{number}.json"
            try:
                auth = _json(base64.b64decode(snapshot.read(root / name), validate=True))
            except ValueError:
                _fail("A saved Codex credential is corrupt.")
            self._validate_codex(row, auth)
            files[name] = base64.b64encode(_encoded(auth))
        self._preferences(snapshot, files, warnings)
        self._live_credentials(snapshot, claude, credentials, codex, files, warnings)
        if counts["claude"]:
            _roster(_encoded(claude), codex=False)
        keychain = {}
        for number, credential in credentials.items():
            email = claude["accounts"][number]["email"]
            if Platform.detect() == Platform.MACOS:
                username = f"account-{number}-{email}"
                if macos_keychain.get_password(SECURITY_SERVICE, username) is not None:
                    _fail("The destination Keychain already contains saved credentials. Refusing to overwrite them.")
                keychain[username] = credential
            else:
                files[f"credentials/.creds-{number}-{email}.enc"] = base64.b64encode(credential.encode("utf-8"))
        if counts["claude"]:
            files["sequence.json"] = _encoded(claude)
            warnings.append(_OPAQUE_WARNING)
        if counts["codex"]:
            files["codex/accounts.json"] = _encoded(codex)
        files[_STATE] = _encoded({"version": 1, "source": source, "importedAt": get_timestamp(), "counts": counts})
        snapshot.verify()
        self._destination_empty()
        stage = self.root / f".legacy-import-{uuid.uuid4().hex}"
        published = []
        written_keys = []
        staged = []
        stage_created = False
        try:
            with _directory(stage.parent) as parent:
                os.mkdir(stage if parent is None else stage.name, mode=0o700, dir_fd=parent)
            stage_created = True
            for name, raw in files.items():
                path = stage / name
                staged.append(path)
                _write_new(path, raw)
                if _read(path) != raw:
                    _fail("Staged import verification failed.")
            _check_quiescent()
            snapshot.verify()
            for username, credential in keychain.items():
                if macos_keychain.get_password(SECURITY_SERVICE, username) is not None:
                    _fail("Destination credentials changed during import. Nothing will be overwritten.")
                written_keys.append(username)
                macos_keychain.set_password(SECURITY_SERVICE, username, credential)
                if macos_keychain.get_password(SECURITY_SERVICE, username) != credential:
                    _fail("Destination Keychain verification failed.")
            snapshot.verify()
            for name, raw in files.items():
                target = self.root / name
                path = stage / name
                identity = _stat(path)
                with _directory(path.parent) as source_fd, _directory(target.parent, create=True) as dest_fd:
                    published.append((target, identity))
                    try:
                        os.link(path if source_fd is None else path.name, target if dest_fd is None else target.name,
                                src_dir_fd=source_fd, dst_dir_fd=dest_fd, follow_symlinks=False)
                    except FileExistsError:
                        published.pop()
                        _fail("Destination data appeared during import. Existing files were not overwritten.")
                if _read(target, linked=True) != raw:
                    _fail("Published import verification failed.")
            snapshot.verify()
        except BaseException:
            rollback_ok = True
            for target, identity in reversed(published):
                try:
                    rollback_ok = _unlink_owned(target, identity) and rollback_ok
                except Exception:
                    rollback_ok = False
            for username in reversed(written_keys):
                try:
                    if macos_keychain.get_password(SECURITY_SERVICE, username) == keychain[username]:
                        macos_keychain.delete_password(SECURITY_SERVICE, username)
                    else:
                        rollback_ok = False
                except Exception:
                    rollback_ok = False
            if not rollback_ok:
                _fail("Import failed and some newly created destination data could not be rolled back. Inspect the destination before retrying; the source was not changed.")
            raise
        finally:
            cleanup_ok = True
            for path in reversed(staged):
                try:
                    info = _stat(path)
                    if info is not None:
                        cleanup_ok = _unlink_owned(path, info) and cleanup_ok
                except Exception:
                    cleanup_ok = False
            directories = (stage / "codex/credentials", stage / "codex", stage / "credentials", stage / "configs", stage) if stage_created else ()
            for directory in directories:
                try:
                    with _directory(directory.parent) as parent:
                        os.rmdir(directory if parent is None else directory.name, dir_fd=parent)
                except FileNotFoundError:
                    pass
                except OSError:
                    cleanup_ok = False
            if not cleanup_ok:
                if sys.exc_info()[0] is not None:
                    _fail("Import failed and private staging files could not all be removed. Inspect the destination's .legacy-import-* directory before retrying; the source was not changed.")
                warnings.append("Private import staging files could not all be removed. Inspect the destination's .legacy-import-* directory before retrying.")
        return {"ok": True, "message": "Saved accounts were copied into Agent Switch's independent store. Historical data and live logins were not changed.",
                "counts": counts, "warnings": warnings}
