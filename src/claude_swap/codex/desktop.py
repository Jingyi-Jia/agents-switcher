"""Read-only Codex process checks and consent-gated macOS app assistance.

Prefer native executable identities to mutable process titles. Linux may deny
the executable link of a protected background process while exposing its argv.
Only matching, non-Codex, non-interpreter names can use that bounded command
fallback. Missing, inconsistent, or relevant metadata remains unknown.
"""

from __future__ import annotations

import ctypes
import errno
import json
import os
import plistlib
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from xml.parsers.expat import ExpatError

_BUNDLE_ID = "com.openai.codex"
_MAX_ARGUMENT_BYTES = 4 * 1024 * 1024
_INTERPRETERS = {"node", "nodejs", "node.exe", "bun", "bun.exe"}
_QUIT_SCRIPT = '''ObjC.import("AppKit");
function run(argv) {
    var apps = $.NSWorkspace.sharedWorkspace.runningApplications;
    var matches = [];
    for (var i = 0; i < apps.count; i++) {
        var app = apps.objectAtIndex(i);
        if (ObjC.unwrap(app.bundleIdentifier) === "com.openai.codex" &&
            ObjC.unwrap(app.bundleURL.path) === argv[0] &&
            ObjC.unwrap(app.executableURL.path) === argv[0] + "/Contents/MacOS/Codex") {
            matches.push(app);
        }
    }
    if (matches.length === 0) return "not-running";
    if (matches.length !== 1) return "ambiguous";
    return matches[0].terminate ? "requested" : "refused";
}'''
_WINDOWS_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
$PSModuleAutoLoadingPreference = 'None'
try {
    Import-Module "$PSHOME\Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1" -ErrorAction Stop
    Import-Module "$PSHOME\Modules\CimCmdlets\CimCmdlets.psd1" -ErrorAction Stop
    $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $rows = @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
        $_.Name -match '^(codex.*|node|nodejs|bun)(\.exe)?$'
    } | ForEach-Object {
        $owner = Invoke-CimMethod -InputObject $_ -MethodName GetOwnerSid -ErrorAction Stop
        if ($owner.ReturnValue -ne 0 -or -not $owner.Sid) { throw 'owner unavailable' }
        [pscustomobject]@{
            ProcessId = $_.ProcessId
            ParentProcessId = $_.ParentProcessId
            OwnerSid = $owner.Sid
            ExecutablePath = $_.ExecutablePath
            CommandLine = $_.CommandLine
        }
    })
    [pscustomobject]@{ Complete = $true; CurrentUser = $sid; Processes = $rows } |
        ConvertTo-Json -Depth 4 -Compress
} catch { exit 1 }
'''


@dataclass(frozen=True)
class _Process:
    pid: int
    parent: int
    executable: str
    arguments: tuple[str, ...]
    terminal: bool = False


def _basename(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def _native_codex(name: str) -> bool:
    return name.lower() in {"codex", "codex.exe"} or re.fullmatch(
        r"codex-(?:aarch64|x86_64|arm64|i686)-[a-z0-9_-]+(?:\.exe)?", name.lower(),
    ) is not None


def _codex_process(process: _Process) -> bool:
    name = _basename(process.executable).lower()
    if _native_codex(name):
        return True
    if name not in _INTERPRETERS:
        return False
    return any(_basename(argument).lower() in {"codex", "codex.js", "codex.mjs"}
               for argument in process.arguments[1:])


def _app_server(arguments: tuple[str, ...]) -> bool:
    remaining = iter(arguments[1:])
    for argument in remaining:
        if argument in {"-c", "--config", "--enable", "--disable"}:
            if next(remaining, None) is None:
                return False
        elif argument.startswith(("--config=", "--enable=", "--disable=")) or argument.startswith("-c"):
            continue
        else:
            return argument == "app-server"
    return False


def _child_environment() -> dict[str, str]:
    allowed = {"HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE", "LC_MESSAGES",
               "LC_COLLATE", "LC_MONETARY", "LC_NUMERIC", "LC_TIME", "__CF_USER_TEXT_ENCODING"}
    result = {key: value for key, value in os.environ.items()
              if key in allowed}
    result["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
    return result


def _read_mac_executable(pid: int) -> str:
    library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    buffer = ctypes.create_string_buffer(4096)
    library.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    library.proc_pidpath.restype = ctypes.c_int
    if library.proc_pidpath(pid, buffer, len(buffer)) <= 0 or not buffer.value:
        raise OSError(ctypes.get_errno(), "Process executable unavailable")
    return os.fsdecode(buffer.value)


def _read_mac_arguments(pid: int) -> tuple[str, tuple[str, ...]]:
    library = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    library.sysctl.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_uint,
                              ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t),
                              ctypes.c_void_p, ctypes.c_size_t]
    library.sysctl.restype = ctypes.c_int
    maximum = ctypes.c_int()
    maximum_size = ctypes.c_size_t(ctypes.sizeof(maximum))
    if library.sysctl((ctypes.c_int * 2)(1, 8), 2, ctypes.byref(maximum), ctypes.byref(maximum_size), None, 0) != 0:
        raise OSError(ctypes.get_errno(), "Process argument limit unavailable")
    if maximum_size.value != ctypes.sizeof(maximum) or not ctypes.sizeof(maximum) < maximum.value <= _MAX_ARGUMENT_BYTES:
        raise ValueError("Invalid process argument limit")
    mib = (ctypes.c_int * 3)(1, 49, pid)
    size = ctypes.c_size_t(maximum.value)
    buffer = ctypes.create_string_buffer(size.value)
    if library.sysctl(mib, 3, buffer, ctypes.byref(size), None, 0) != 0:
        raise OSError(ctypes.get_errno(), "Process arguments unavailable")
    return _parse_mac_arguments(buffer.raw[:size.value])


def _parse_mac_arguments(raw: bytes) -> tuple[str, tuple[str, ...]]:
    width = ctypes.sizeof(ctypes.c_int)
    if len(raw) <= width:
        raise ValueError
    count = ctypes.c_int.from_buffer_copy(raw[:width]).value
    if not 1 <= count <= 65536:
        raise ValueError
    executable, separator, remaining = raw[width:].partition(b"\0")
    if not executable or not separator:
        raise ValueError
    remaining = remaining.lstrip(b"\0")
    arguments = []
    for _ in range(count):
        argument, separator, remaining = remaining.partition(b"\0")
        if not separator:
            raise ValueError
        arguments.append(os.fsdecode(argument))
    if not arguments[0]:
        raise ValueError
    return os.fsdecode(executable), tuple(arguments)


def _read_linux_executable(pid: int) -> str:
    return os.readlink(Path("/proc") / str(pid) / "exe").removesuffix(" (deleted)")


def _read_linux_command(pid: int) -> tuple[str, ...]:
    root = Path("/proc") / str(pid)
    with (root / "cmdline").open("rb") as stream:
        raw = stream.read(_MAX_ARGUMENT_BYTES + 1)
    if not raw or not raw.endswith(b"\0") or len(raw) > _MAX_ARGUMENT_BYTES:
        raise ValueError
    arguments = tuple(os.fsdecode(argument) for argument in raw[:-1].split(b"\0"))
    if not arguments[0]:
        raise ValueError
    return arguments


def _read_linux_arguments(pid: int) -> tuple[str, tuple[str, ...]]:
    executable = _read_linux_executable(pid)
    if not executable:
        raise ValueError
    return executable, _read_linux_command(pid)


def _posix_processes() -> list[_Process]:
    result = subprocess.run(
        ["/bin/ps", "-axww", "-o", "pid=,ppid=,uid=,stat=,tty=,comm="],
        capture_output=True, text=True, timeout=5, check=True,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
    )
    if result.returncode != 0 or not isinstance(result.stdout, str) or not result.stdout.strip():
        raise ValueError
    uid = os.getuid()
    processes = []
    seen = set()
    for line in result.stdout.splitlines():
        parts = line.split(None, 5)
        if len(parts) < 5:
            raise ValueError
        pid_text, parent_text, owner_text, state, tty = parts[:5]
        if (re.fullmatch(r"[0-9]+", pid_text) is None
                or re.fullmatch(r"[0-9]+", parent_text) is None
                or re.fullmatch(r"-?[0-9]+", owner_text) is None
                or re.fullmatch(r"[A-Z][A-Za-z0-9+<>=-]*", state) is None):
            raise ValueError
        pid, parent, owner = int(pid_text), int(parent_text), int(owner_text)
        if sys.platform == "darwin" and -(2**31) <= owner < 0:
            owner += 2**32
        if not (0 <= pid < 2**31 and 0 <= parent < 2**31 and 0 <= owner < 2**32) or pid in seen:
            raise ValueError
        if pid == 0 and (sys.platform != "darwin" or parent != 0 or owner != 0):
            raise ValueError
        seen.add(pid)
        if state.startswith("Z"):
            continue
        if len(parts) != 6 or not parts[5].strip():
            raise ValueError
        if pid == 0 or owner != uid:
            continue
        executable = parts[5].strip()
        processes.append(_Process(pid, parent, executable, (), tty not in {"?", "??", "-"}))
    if not processes:
        raise ValueError
    found = []
    for process in processes:
        executable = process.executable
        arguments = ()
        try:
            read_executable = _read_mac_executable if sys.platform == "darwin" else _read_linux_executable
            try:
                executable = read_executable(process.pid)
            except PermissionError:
                if sys.platform != "linux":
                    raise
                arguments = _read_linux_command(process.pid)
                name = _basename(arguments[0]).lower()
                if (name != _basename(process.executable).lower()
                        or name in _INTERPRETERS | {"mainthread"} or name.startswith("codex")
                        or any(_basename(argument).lower() in {"codex", "codex.js", "codex.mjs"} for argument in arguments[1:])):
                    raise
                executable = arguments[0]
            name = _basename(executable).lower()
            if (name.startswith("codex") or name in _INTERPRETERS
                    or "/Codex.app/Contents/" in executable):
                reader = _read_mac_arguments if sys.platform == "darwin" else _read_linux_arguments
                executable, arguments = reader(process.pid)
        except (OSError, ValueError):
            try:
                os.kill(process.pid, 0)
            except OSError as error:
                if error.errno == errno.ESRCH:
                    continue
            raise
        found.append(_Process(process.pid, process.parent, executable, arguments, process.terminal))
    return found


def _windows_processes() -> list[_Process]:
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-Command", _WINDOWS_SCRIPT],
        capture_output=True, text=True, timeout=10, check=True,
    )
    if result.returncode != 0:
        raise ValueError
    data = json.loads(result.stdout)
    if (not isinstance(data, dict) or data.get("Complete") is not True
            or not isinstance(data.get("CurrentUser"), str)
            or re.fullmatch(r"S-[0-9]+(?:-[0-9]+)+", data["CurrentUser"]) is None
            or not isinstance(data.get("Processes"), list)):
        raise ValueError
    processes = []
    seen = set()
    for row in data["Processes"]:
        if not isinstance(row, dict):
            raise ValueError
        pid, parent, owner = row.get("ProcessId"), row.get("ParentProcessId"), row.get("OwnerSid")
        if (type(pid) is not int or not 0 < pid < 2**32 or pid in seen
                or type(parent) is not int or not 0 <= parent < 2**32
                or not isinstance(owner, str) or re.fullmatch(r"S-[0-9]+(?:-[0-9]+)+", owner) is None):
            raise ValueError
        seen.add(pid)
        if owner != data["CurrentUser"]:
            continue
        executable, command = row.get("ExecutablePath"), row.get("CommandLine")
        if not isinstance(executable, str) or not executable or not isinstance(command, str) or not command.strip():
            raise ValueError
        arguments = tuple(re.findall(r'"([^"\r\n]*)"|([^\s"]+)', command))
        flattened = tuple(quoted or plain for quoted, plain in arguments)
        if not flattened or command.count('"') % 2:
            raise ValueError
        processes.append(_Process(pid, parent, executable, flattened))
    return processes


def _installed_apps() -> list[Path]:
    if sys.platform != "darwin":
        return []
    found = []
    for app in (Path("/Applications/Codex.app"), Path.home() / "Applications/Codex.app"):
        try:
            executable = app / "Contents/MacOS/Codex"
            info = app / "Contents/Info.plist"
            if any(path.is_symlink() for path in (app, app / "Contents", executable.parent, executable, info)):
                continue
            if not executable.is_file() or not os.access(executable, os.X_OK):
                continue
            with info.open("rb") as stream:
                raw = stream.read(262145)
            if len(raw) > 262144:
                continue
            data = plistlib.loads(raw)
            if (isinstance(data, dict) and data.get("CFBundleIdentifier") == _BUNDLE_ID
                    and data.get("CFBundleExecutable") == "Codex" and data.get("CFBundlePackageType") == "APPL"):
                found.append(app)
        except (OSError, ValueError, TypeError, ExpatError, RecursionError, plistlib.InvalidFileException):
            continue
    return found


def _gui_home() -> Path:
    import pwd

    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def _compatible_home() -> bool:
    try:
        home = _gui_home().resolve()
        configured = os.environ.get("CODEX_HOME")
        return (Path.home().resolve() == home
                and (not configured or Path(configured).resolve() == (home / ".codex").resolve()))
    except (OSError, ValueError, RuntimeError, KeyError):
        return False


def _in_bundle(executable: str, app: Path) -> bool:
    path = Path(executable)
    return path.is_absolute() and ".." not in path.parts and path.is_relative_to(app / "Contents")


def _app_owned(process: _Process, main: _Process, app: Path, processes: dict[int, _Process]) -> bool:
    seen = set()
    current = process
    while current.pid not in seen:
        if current.terminal or not _in_bundle(current.executable, app):
            return False
        if current.pid == main.pid:
            return True
        seen.add(current.pid)
        if current.parent not in processes:
            return False
        current = processes[current.parent]
    return False


class CodexDesktop:
    """`available` means the process check succeeded, not that assistance is supported."""

    def status(self) -> dict:
        return self._inspect()[0]

    def _inspect(self) -> tuple[dict, Path | None]:
        status = {
            "available": False, "running": None, "desktopRunning": False,
            "terminalCount": 0, "backgroundCount": 0, "canAssist": False,
            "canOpen": False, "message": None,
        }
        if sys.platform not in {"darwin", "linux", "win32"}:
            status["message"] = "Codex process checks are unavailable on this platform. Switching is blocked."
            return status, None
        try:
            processes = _windows_processes() if sys.platform == "win32" else _posix_processes()
        except (OSError, subprocess.SubprocessError, ValueError, TypeError, AttributeError, OverflowError):
            status["message"] = (
                "Could not reliably check Codex processes. No app action was taken. "
                "Check system permissions and try again; switching remains blocked."
            )
            return status, None
        apps = _installed_apps()
        by_pid = {process.pid: process for process in processes}
        mains = [(app, process) for app in apps for process in processes
                 if process.executable == str(app / "Contents/MacOS/Codex")]
        for process in processes:
            matching = [(app, main) for app, main in mains if _app_owned(process, main, app, by_pid)]
            embedded = any(process.executable == str(app / "Contents/Resources/codex")
                           and _app_server(process.arguments) for app, _ in matching)
            helper = any(Path(process.executable).is_relative_to(app / "Contents/Frameworks")
                         and (_basename(process.executable) == "browser_crashpad_handler"
                              or re.fullmatch(r"Codex Helper(?: \([A-Za-z ]+\))?", _basename(process.executable)))
                         for app, _ in matching)
            main = any(process.pid == candidate.pid for _, candidate in matching)
            if main or embedded or helper:
                status["desktopRunning"] = True
            elif _codex_process(process) or "/Codex.app/Contents/" in process.executable.replace("\\", "/"):
                field = "terminalCount" if process.terminal else "backgroundCount"
                status[field] += 1
        status["available"] = True
        status["running"] = bool(status["desktopRunning"] or status["terminalCount"] or status["backgroundCount"])
        app = mains[0][0] if len(mains) == 1 else (apps[0] if apps else None)
        if sys.platform != "darwin":
            status["message"] = "Quit Codex apps, terminal sessions, and background clients manually, then check again. App assistance is macOS-only."
        elif not _compatible_home():
            status["message"] = "App assistance requires the usual home folder and default CODEX_HOME (~/.codex). Quit Codex manually for this configuration."
        elif app is None:
            status["message"] = "Install the official Codex app in /Applications or ~/Applications to use app assistance. Quit any Codex clients manually."
        elif status["terminalCount"] or status["backgroundCount"]:
            status["message"] = "Quit Codex terminal sessions, background clients, and other apps using Codex manually, then check again. They will not be closed for you."
        elif len(mains) > 1:
            status["message"] = "More than one Codex app is running. Quit them manually, then check again."
        else:
            status["canAssist"] = status["desktopRunning"] and len(mains) == 1
            status["canOpen"] = status["running"] is False
        return status, app

    def quit(self, confirm=False) -> dict:
        result = {"ok": False, "quitRequested": False, "message": "Confirm before asking Codex to quit. Your account has not changed."}
        if confirm is not True:
            return result
        status, app = self._inspect()
        if not status["canAssist"] or app is None:
            result["message"] = status["message"] or "There is no eligible running Codex app to close. Your account has not changed."
            return result
        try:
            response = subprocess.run(
                ["/usr/bin/osascript", "-l", "JavaScript", "-e", _QUIT_SCRIPT, str(app)],
                capture_output=True, text=True, timeout=10, check=True, env=_child_environment(),
            )
            if response.returncode != 0 or response.stdout.strip() != "requested":
                raise ValueError
        except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
            result["message"] = (
                "Codex did not accept the quit request, or the request could not be confirmed. "
                "Check for a prompt in Codex or macOS, quit it manually, then check again. Your account has not changed."
            )
            return result
        return {"ok": True, "quitRequested": True, "message": "Closing Codex… Your account has not changed. Wait for all Codex processes to exit."}

    def open(self, confirm=False) -> dict:
        result = {"ok": False, "launchRequested": False, "message": "Confirm before opening Codex."}
        if confirm is not True:
            return result
        status, app = self._inspect()
        if not status["canOpen"] or app is None:
            result["message"] = status["message"] or "Fully quit all Codex clients before requesting a launch, then check again."
            return result
        try:
            subprocess.Popen(
                [str(app / "Contents/MacOS/Codex")], env=_child_environment(), cwd="/",
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True, close_fds=True,
            )
        except (OSError, subprocess.SubprocessError, ValueError):
            result["message"] = "Codex could not be opened. Check the official app installation and try again."
            return result
        return {"ok": True, "launchRequested": True, "message": "Codex launch requested. Check the active account inside Codex."}
