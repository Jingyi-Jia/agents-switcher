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

import os
import subprocess
import sys
from dataclasses import dataclass

#: Bounded so a wedged `ps` cannot hang a switch.
_PS_TIMEOUT_S = 5


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


def _executable_of(command: str) -> str:
    """The executable path from a ``ps`` command line.

    Splits on whitespace, which mis-parses an executable path CONTAINING a
    space. That is acceptable here and fails in the safe direction: the
    Electron helpers are the paths with spaces, and mis-parsing them only makes
    them fail to match a bare ``codex``, which is what we want anyway. A user
    whose own codex binary lives under a path with a space is not detected and
    gets the no-processes-running message -- they are told to restart Codex
    regardless, so the outcome is a weaker warning, never a wrong swap.
    """
    return command.split()[0] if command.strip() else ""


def running_codex_processes() -> list[CodexProcess]:
    """Every running process whose executable is named exactly ``codex``.

    Returns an empty list when the process table cannot be read, which callers
    must treat as "unknown", not as "nothing is running": the safe reading of an
    unavailable ``ps`` is that a switch still needs a restart, and that is what
    callers report anyway.
    """
    if sys.platform == "win32":
        return _running_codex_processes_windows()
    try:
        out = subprocess.run(
            ["ps", "-eo", "pid=,uid=,tty=,args="],
            capture_output=True,
            text=True,
            timeout=_PS_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []

    me = os.getuid()
    self_pid = os.getpid()
    found: list[CodexProcess] = []
    for line in out.stdout.splitlines():
        parts = line.strip().split(None, 3)
        if len(parts) < 4:
            continue
        pid_text, uid_text, tty, command = parts
        if not pid_text.isdigit() or not uid_text.isdigit():
            continue
        if int(uid_text) != me:
            continue  # someone else's Codex is not blocking our switch
        pid = int(pid_text)
        if pid == self_pid:
            continue
        executable = _executable_of(command)
        if os.path.basename(executable) != "codex":
            continue
        found.append(
            CodexProcess(
                pid=pid,
                executable=executable,
                command=command.strip(),
                tty=tty,
            )
        )
    return found


def _running_codex_processes_windows() -> list[CodexProcess]:
    """Windows equivalent via ``tasklist``.

    ``tasklist`` reports the image name but not the full command line, so the
    app-server/CLI distinction is unavailable here and every match is reported
    as a plain CLI process.
    """
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq codex.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=_PS_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []

    found: list[CodexProcess] = []
    for line in out.stdout.splitlines():
        parts = [p.strip('"') for p in line.strip().split('","')]
        if len(parts) < 2 or not parts[0]:
            continue
        name, pid_text = parts[0].strip('"'), parts[1].strip('"')
        if name.lower() != "codex.exe" or not pid_text.isdigit():
            continue
        found.append(CodexProcess(pid=int(pid_text), executable=name, command=name))
    return found
