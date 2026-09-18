"""Detect running Codex processes, which a credential swap cannot reach.

This is the interlock that stands in for a lock Codex does not provide. Codex's
``AuthManager`` loads ``auth.json`` once and its only reload path refuses to
cross account ids, so a running process keeps using the account it started with
no matter what lands on disk. Two consequences:

* A switch is only visible to the NEXT process, so the user must be told to
  restart -- silently swapping and reporting success would be a lie.
* With nothing running there is no in-process token refresher to race, which is
  what makes swapping safe without an advisory lock to cooperate with.

MATCHING IS DELIBERATELY NARROW. The ChatGPT desktop app ships an Electron stack
whose processes are full of the word "Codex" -- ``Codex (Renderer)``,
``Codex (Service)``, ``browser_crashpad_handler`` under a ``Codex Framework``
path. None of them reads ``auth.json``. A substring match on "codex" reports
half a dozen phantom blockers on any Mac with the app installed, so only a
process whose EXECUTABLE is named exactly ``codex`` counts. That still catches
both things that matter: the CLI, and the desktop app's embedded
``codex app-server``, which holds auth in memory exactly like the CLI does.

OTHER USERS ARE NOT OUR BUSINESS. ``ps -e`` lists the whole machine. On a shared
login node that is overwhelmingly other people -- measured on one cluster head
node, 799 processes visible and 8 owned by the caller -- so an unfiltered match
reports a stranger's Codex as the thing blocking your switch. Only processes
owned by the current uid count.

WSL runs the Linux path, which is correct rather than incidental: a WSL install
has its own ``$HOME/.codex`` entirely separate from the Windows one, so the
credential being swapped and the process that must restart are both on the Linux
side. A Codex running on the Windows host is a different install with a
different credential and is rightly invisible here.
"""

from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import sys
from dataclasses import dataclass

#: Bounded so a wedged `ps` cannot hang a switch.
_PS_TIMEOUT_S = 5

#: Interpreters that can be running Codex without appearing as ``codex``.
#: The npm package (@openai/codex) declares ``bin: {codex: "bin/codex.js"}``, so
#: an npm install runs as ``node .../bin/codex.js`` and argv[0] is the
#: interpreter. A native install (Homebrew cask, the standalone wrapper's
#: ``exec "$real_codex"``) does put ``codex`` in argv[0]; both shapes are real
#: and a miss here is the DANGEROUS direction -- it reports "nothing running"
#: and lets a switch look complete while a live process still serves the old
#: account.
_INTERPRETERS = frozenset({"node", "node.exe"})
#: Case rules differ by platform and a blanket choice is wrong either way.
#: Windows spells it ``codex.exe`` on a case-insensitive filesystem, so that
#: name must match in any casing. But macOS's ChatGPT app ships a helper binary
#: literally named ``Codex``, so matching the bare name case-insensitively
#: reports the whole Electron stack -- measured, 14 processes instead of 1.
#: Hence: exact for the POSIX spelling, case-insensitive only for the .exe one.
#: Script names that identify Codex when run under one of those interpreters.
_CODEX_SCRIPT_NAMES = frozenset({"codex", "codex.js"})


@dataclass(frozen=True)
class CodexProcess:
    """A running ``codex`` process that will not observe a credential swap."""

    pid: int
    executable: str
    command: str
    tty: str = ""

    @property
    def is_interactive(self) -> bool:
        """Whether this process is attached to a terminal.

        Distinguishes a session someone is typing into from a background helper
        left behind, which changes what the user is being asked to restart.
        ``ps`` spells "no tty" as ``?`` on Linux and ``??`` on macOS.
        """
        return self.tty not in ("", "?", "??", "-")

    @property
    def is_app_server(self) -> bool:
        """Whether this is the desktop app's embedded server rather than a CLI.

        Worth distinguishing only for the message shown to the user: quitting
        the ChatGPT app and restarting a terminal session are different actions.
        """
        return " app-server" in f" {self.command}"

    @property
    def describe(self) -> str:
        if self.is_app_server:
            kind = "ChatGPT app (app-server)"
        elif self.is_interactive:
            kind = f"codex CLI on {self.tty}"
        else:
            kind = "codex (background)"
        return f"pid {self.pid} — {kind}"


def _basename(path: str) -> str:
    """Last path segment, splitting on BOTH separators regardless of host OS.

    ``os.path.basename`` only treats ``\\`` as a separator when running ON
    Windows, so a Windows command line parsed anywhere else keeps its whole
    path as the "basename" and never matches. That made the Windows scan
    correct only when executed on Windows -- and therefore untestable
    everywhere else, which is exactly how it went uncovered.
    """
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def _is_codex_name(name: str) -> bool:
    """Whether an executable basename is Codex itself."""
    if name == "codex":
        return True
    return name.lower() == "codex.exe"


def _codex_executable(command: str) -> str | None:
    """The path identifying ``command`` as Codex, or ``None`` if it is not.

    Splits on whitespace, which mis-parses an executable path CONTAINING a
    space. That fails in the safe direction for the case that actually occurs:
    the ChatGPT app's Electron helpers are the paths with spaces, and
    mis-parsing them only stops them matching a bare ``codex``, which is the
    desired outcome anyway.
    """
    tokens = command.split()
    if not tokens:
        return None

    executable = tokens[0]
    if _is_codex_name(_basename(executable)):
        return executable

    if _basename(executable).lower() in _INTERPRETERS:
        for token in tokens[1:]:
            if token.startswith("-"):
                continue  # interpreter flags precede the script
            # The first non-flag argument IS the script; if that is not Codex,
            # this interpreter is running something else entirely.
            if _basename(token).lower() in _CODEX_SCRIPT_NAMES:
                return token
            return None
    return None


def _parse_posix_ps(text: str, *, uid: int, self_pid: int) -> list[CodexProcess]:
    """Parse ``ps -eo pid=,uid=,tty=,args=``. Pure, so it is testable anywhere."""
    found: list[CodexProcess] = []
    for line in text.splitlines():
        parts = line.strip().split(None, 3)
        if len(parts) < 4:
            continue
        pid_text, uid_text, tty, command = parts
        if not pid_text.isdigit() or not uid_text.isdigit():
            continue
        if int(uid_text) != uid:
            continue  # someone else's Codex is not blocking our switch
        pid = int(pid_text)
        if pid == self_pid:
            continue
        executable = _codex_executable(command)
        if executable is None:
            continue
        found.append(CodexProcess(pid=pid, executable=executable,
                                  command=command.strip(), tty=tty))
    return found


def _parse_windows_powershell(text: str, *, self_pid: int) -> list[CodexProcess]:
    """Parse the JSON of ProcessId/CommandLine pairs from Get-CimInstance.

    PowerShell emits a bare object rather than a list when exactly one process
    matches, which is the single most common way a caller gets this wrong.
    """
    try:
        payload = json.loads(text) if text.strip() else []
    except ValueError:
        return []
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        return []

    found: list[CodexProcess] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        pid = entry.get("ProcessId")
        command = entry.get("CommandLine") or ""
        if not isinstance(pid, int) or pid == self_pid or not command:
            continue
        executable = _codex_executable(command)
        if executable is None:
            continue
        found.append(CodexProcess(pid=pid, executable=executable,
                                  command=command.strip(), tty=""))
    return found


def _parse_windows_tasklist(text: str, *, self_pid: int) -> list[CodexProcess]:
    """Parse ``tasklist /FO CSV /NH``.

    The fallback, and a weaker one: tasklist reports the image name but NOT the
    command line, so an npm-installed Codex (``node ...\\codex.js``) is
    invisible here. That is why PowerShell is tried first.
    """
    found: list[CodexProcess] = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 2:
            continue
        name, pid_text = row[0].strip(), row[1].strip()
        if name.lower() != "codex.exe" or not pid_text.isdigit():
            continue
        pid = int(pid_text)
        if pid == self_pid:
            continue
        found.append(CodexProcess(pid=pid, executable=name, command=name, tty=""))
    return found


def _run(command: list[str]) -> str | None:
    """Run a command and return stdout, or None if it could not be run."""
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=_PS_TIMEOUT_S
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def running_codex_processes() -> list[CodexProcess]:
    """Every running process of the current user whose executable is Codex.

    Returns an empty list when the process table cannot be read, which callers
    must treat as "unknown", not as "nothing is running": the safe reading of an
    unavailable process list is that a switch still needs a restart, and that is
    what callers report anyway.
    """
    if sys.platform == "win32":
        return _running_codex_processes_windows()
    output = _run(["ps", "-eo", "pid=,uid=,tty=,args="])
    if output is None:
        return []
    return _parse_posix_ps(output, uid=os.getuid(), self_pid=os.getpid())


def _running_codex_processes_windows() -> list[CodexProcess]:
    """Windows scan: PowerShell for command lines, tasklist as a fallback.

    PowerShell is preferred because it is the only one of the two that reports
    a COMMAND LINE, and without that an npm-installed Codex -- which runs as
    ``node ...\\codex.js`` -- cannot be recognised at all. tasklist is kept
    for the case where PowerShell is unavailable or locked down by policy; it
    still catches a native ``codex.exe``, which is the other common install.

    Neither path filters by user. On POSIX that filter matters because a shared
    login node's process table is mostly other people; a Windows box running
    Codex is overwhelmingly single-user, and Win32_Process carries no owner
    without a second per-process call that would cost more than it buys.
    """
    self_pid = os.getpid()
    output = _run([
        "powershell", "-NoProfile", "-NonInteractive", "-Command",
        "Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress",
    ])
    if output is not None:
        found = _parse_windows_powershell(output, self_pid=self_pid)
        if found:
            return found
    output = _run(["tasklist", "/FI", "IMAGENAME eq codex.exe", "/FO", "CSV", "/NH"])
    if output is None:
        return []
    return _parse_windows_tasklist(output, self_pid=self_pid)
