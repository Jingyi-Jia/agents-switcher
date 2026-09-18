"""Tests for detecting running Codex processes.

Matching is narrow on purpose: the ChatGPT desktop app fills the process table
with Electron helpers whose names contain "Codex", and a shared login node's
process table is mostly other people.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from claude_swap.codex import processes as proc_mod
from claude_swap.codex.processes import (
    CodexProcess,
    _parse_windows_powershell,
    _parse_windows_tasklist,
    _running_codex_processes_windows,
    running_codex_processes,
)

ME = os.getuid() if hasattr(os, "getuid") else 0


def ps_line(pid, command, uid=None, tty="?"):
    return f"{pid} {ME if uid is None else uid} {tty} {command}"


@pytest.fixture
def fake_ps(monkeypatch):
    """Drive the scanner from a synthetic `ps` table."""

    def install(lines, returncode=0):
        def fake_run(*a, **kw):
            return subprocess.CompletedProcess(
                args=a, returncode=returncode, stdout="\n".join(lines), stderr=""
            )

        monkeypatch.setattr(proc_mod.subprocess, "run", fake_run)

    monkeypatch.setattr(proc_mod.sys, "platform", "linux")
    return install


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX process model")
class TestMatching:
    def test_matches_a_bare_codex(self, fake_ps):
        fake_ps([ps_line(100, "codex")])
        assert [p.pid for p in running_codex_processes()] == [100]

    def test_matches_an_absolute_codex_path(self, fake_ps):
        fake_ps([ps_line(101, "/usr/local/bin/codex --model gpt-5")])
        found = running_codex_processes()
        assert found[0].executable == "/usr/local/bin/codex"

    def test_rejects_names_that_merely_contain_codex(self, fake_ps):
        # These are what a substring match gets wrong.
        fake_ps([
            ps_line(1, "/usr/bin/codex-helper"),
            ps_line(2, "/opt/mycodex"),
            ps_line(3, "/usr/bin/codex-switcher"),
            ps_line(4, "python -m codex_tool"),
        ])
        assert running_codex_processes() == []

    def test_rejects_the_chatgpt_desktop_electron_stack(self, fake_ps):
        # Measured from a real Mac: seven helpers with "Codex" in the path, none
        # of which reads auth.json.
        fake_ps([
            ps_line(1, "/Applications/ChatGPT.app/Contents/Frameworks/Codex Framework.framework/Helpers/browser_crashpad_handler --monitor-self"),
            ps_line(2, "/Applications/ChatGPT.app/Contents/Frameworks/Codex Framework.framework/Helpers/Codex (Renderer).app/Contents/MacOS/Codex (Renderer) --type=renderer"),
            ps_line(3, "/Applications/ChatGPT.app/Contents/Frameworks/Codex Framework.framework/Helpers/Codex (Service).app/Contents/MacOS/Codex (Service) --type=gpu-process"),
        ])
        assert running_codex_processes() == []

    def test_matches_the_desktop_apps_embedded_app_server(self, fake_ps):
        # This one DOES hold auth in memory and must be reported.
        fake_ps([ps_line(3246, "/Applications/ChatGPT.app/Contents/Resources/codex -c x app-server")])
        found = running_codex_processes()
        assert len(found) == 1
        assert found[0].is_app_server


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX process model")
class TestNpmInstalledCodex:
    """The npm package declares bin: {codex: "bin/codex.js"}, so an npm install
    runs as `node .../codex.js` and argv[0] is the interpreter, not codex.
    Missing that reports "nothing running" while a live process still serves the
    old account -- the dangerous direction."""

    def test_matches_a_node_run_codex_script(self, fake_ps):
        fake_ps([ps_line(300, "node /usr/lib/node_modules/@openai/codex/bin/codex.js exec")])
        found = running_codex_processes()
        assert len(found) == 1
        assert found[0].executable.endswith("codex.js")

    def test_matches_past_interpreter_flags(self, fake_ps):
        fake_ps([ps_line(301, "/usr/bin/node --enable-source-maps /opt/codex/bin/codex.js")])
        assert len(running_codex_processes()) == 1

    def test_does_not_match_node_running_anything_else(self, fake_ps):
        fake_ps([
            ps_line(302, "node /usr/lib/node_modules/other/bin/thing.js"),
            ps_line(303, "node /srv/app/server.js --codex-mode"),
        ])
        assert running_codex_processes() == []

    def test_other_interpreters_are_not_trusted(self, fake_ps):
        # Codex is not a Python tool; widening this invites false positives.
        fake_ps([ps_line(304, "python /opt/codex")])
        assert running_codex_processes() == []


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX process model")
class TestOwnership:
    def test_other_users_processes_are_ignored(self, fake_ps):
        """A cluster head node showed 799 processes, 8 of them the caller's.
        A stranger's Codex does not block our switch."""
        fake_ps([
            ps_line(200, "/usr/bin/codex", uid=ME + 1),
            ps_line(201, "/usr/bin/codex", uid=ME + 2),
        ])
        assert running_codex_processes() == []

    def test_our_own_process_is_ignored(self, fake_ps):
        fake_ps([ps_line(os.getpid(), "/usr/bin/codex")])
        assert running_codex_processes() == []


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX process model")
class TestRobustness:
    def test_a_failing_ps_yields_no_processes(self, fake_ps):
        fake_ps(["irrelevant"], returncode=1)
        assert running_codex_processes() == []

    def test_an_unavailable_ps_yields_no_processes(self, monkeypatch):
        monkeypatch.setattr(proc_mod.sys, "platform", "linux")

        def boom(*a, **kw):
            raise OSError("no ps")

        monkeypatch.setattr(proc_mod.subprocess, "run", boom)
        assert running_codex_processes() == []

    def test_malformed_lines_are_skipped(self, fake_ps):
        fake_ps(["", "   ", "notapid x ? codex", "1 2", ps_line(42, "/bin/codex")])
        assert [p.pid for p in running_codex_processes()] == [42]


class TestDescription:
    def test_interactive_reports_its_tty(self):
        p = CodexProcess(1, "/bin/codex", "codex", tty="pts/3")
        assert p.is_interactive
        assert "pts/3" in p.describe

    def test_no_tty_is_not_interactive(self):
        # ps spells it "?" on Linux and "??" on macOS.
        for spelling in ("?", "??", "", "-"):
            assert not CodexProcess(1, "/bin/codex", "codex", tty=spelling).is_interactive

    def test_app_server_is_named_as_the_desktop_app(self):
        p = CodexProcess(1, "/x/codex", "/x/codex app-server", tty="??")
        assert "ChatGPT app" in p.describe

    def test_app_server_match_requires_a_whole_argument(self):
        # A PATH containing the text must not make an interactive CLI look like
        # the desktop server -- the leading space is what makes it an argument
        # rather than a substring.
        p = CodexProcess(1, "/x/codex", "/x/codex --dir /home/me/app-server-notes", tty="pts/1")
        assert p.is_app_server is False
        assert "codex CLI on pts/1" in p.describe

    def test_app_server_matches_the_real_argument(self):
        p = CodexProcess(1, "/x/codex", "/x/codex -c k=v app-server --flag", tty="??")
        assert p.is_app_server is True


# ---------------------------------------------------------------------------
# Windows. These parsers are pure, so they are exercised on EVERY platform --
# the previous tasklist-only implementation was skipped off Windows and so had
# no coverage at all.
# ---------------------------------------------------------------------------


class TestWindowsPowerShellParsing:
    def test_matches_a_native_codex(self):
        payload = '[{"ProcessId":100,"CommandLine":"C:\\\\tools\\\\codex.exe --model x"}]'
        found = _parse_windows_powershell(payload, self_pid=1)
        assert [p.pid for p in found] == [100]

    def test_matches_an_npm_installed_codex(self):
        """The reason PowerShell is preferred over tasklist: tasklist reports no
        command line, so `node ...codex.js` is invisible to it."""
        payload = (
            '[{"ProcessId":101,"CommandLine":'
            '"node C:\\\\Users\\\\me\\\\AppData\\\\npm\\\\codex.js exec"}]'
        )
        found = _parse_windows_powershell(payload, self_pid=1)
        assert len(found) == 1
        assert found[0].executable.endswith("codex.js")

    def test_a_single_match_arrives_as_an_object_not_a_list(self):
        # PowerShell's ConvertTo-Json emits a bare object for one result, which
        # is the single most common way this parsing goes wrong.
        payload = '{"ProcessId":102,"CommandLine":"C:\\\\bin\\\\codex.exe"}'
        assert [p.pid for p in _parse_windows_powershell(payload, self_pid=1)] == [102]

    def test_rejects_processes_that_merely_contain_codex(self):
        payload = (
            '[{"ProcessId":1,"CommandLine":"C:\\\\x\\\\codex-helper.exe"},'
            '{"ProcessId":2,"CommandLine":"node C:\\\\app\\\\server.js --codex"}]'
        )
        assert _parse_windows_powershell(payload, self_pid=99) == []

    def test_excludes_our_own_process(self):
        payload = '[{"ProcessId":55,"CommandLine":"C:\\\\bin\\\\codex.exe"}]'
        assert _parse_windows_powershell(payload, self_pid=55) == []

    def test_tolerates_empty_and_malformed_output(self):
        for payload in ("", "   ", "not json", "[1,2,3]", '{"ProcessId":null}'):
            assert _parse_windows_powershell(payload, self_pid=1) == []

    def test_skips_entries_without_a_command_line(self):
        # Protected processes report ProcessId but no CommandLine.
        payload = '[{"ProcessId":7,"CommandLine":null}]'
        assert _parse_windows_powershell(payload, self_pid=1) == []


class TestExecutableNameCasing:
    """Case rules differ by platform and a blanket choice breaks one of them."""

    def test_windows_spelling_matches_in_any_casing(self):
        payload = '[{"ProcessId":1,"CommandLine":"C:\\\\bin\\\\CODEX.EXE"}]'
        assert len(_parse_windows_powershell(payload, self_pid=9)) == 1

    @pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX process model")
    def test_the_macos_electron_helper_named_Codex_is_not_matched(self, fake_ps):
        """Matching the bare name case-insensitively reported the whole ChatGPT
        Electron stack -- measured at 14 processes instead of 1."""
        fake_ps([
            ps_line(1, "/Applications/ChatGPT.app/Contents/Frameworks/Codex Framework.framework/Helpers/Codex (Renderer)"),
            ps_line(2, "/Applications/ChatGPT.app/Contents/Frameworks/Codex Framework.framework/Versions/1/Helpers/browser_crashpad_handler"),
        ])
        assert running_codex_processes() == []

    @pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX process model")
    def test_the_posix_spelling_stays_exact(self, fake_ps):
        fake_ps([ps_line(3, "/usr/bin/Codex")])   # capital C is a different file
        assert running_codex_processes() == []


class TestWindowsTasklistParsing:
    def test_parses_the_csv_rows(self):
        text = '"codex.exe","1234","Console","1","12,345 K"\n'
        found = _parse_windows_tasklist(text, self_pid=1)
        assert [p.pid for p in found] == [1234]

    def test_a_quoted_comma_does_not_break_the_row(self):
        # The memory column contains a comma; naive splitting mangles it.
        text = '"codex.exe","99","Services","0","1,048,576 K"\n'
        assert [p.pid for p in _parse_windows_tasklist(text, self_pid=1)] == [99]

    def test_rejects_other_images(self):
        text = '"node.exe","10","Console","1","5 K"\n"codexhelper.exe","11","Console","1","5 K"\n'
        assert _parse_windows_tasklist(text, self_pid=1) == []

    def test_excludes_our_own_process(self):
        text = '"codex.exe","42","Console","1","5 K"\n'
        assert _parse_windows_tasklist(text, self_pid=42) == []

    def test_tolerates_the_no_matching_tasks_message(self):
        assert _parse_windows_tasklist("INFO: No tasks are running.", self_pid=1) == []

    def test_tolerates_empty_output(self):
        assert _parse_windows_tasklist("", self_pid=1) == []


class TestWindowsScanFallback:
    def _stub(self, monkeypatch, powershell=None, tasklist=None):
        def fake_run(command):
            if command and command[0] == "powershell":
                return powershell
            if command and command[0] == "tasklist":
                return tasklist
            return None

        monkeypatch.setattr(proc_mod, "_run", fake_run)

    def test_powershell_is_preferred(self, monkeypatch):
        self._stub(
            monkeypatch,
            powershell='[{"ProcessId":5,"CommandLine":"node /x/codex.js"}]',
            tasklist='"codex.exe","9","Console","1","5 K"\n',
        )
        found = _running_codex_processes_windows()
        assert [p.pid for p in found] == [5]  # not the tasklist row

    def test_falls_back_to_tasklist_when_powershell_is_unavailable(self, monkeypatch):
        # Locked down by policy, or missing entirely.
        self._stub(monkeypatch, powershell=None,
                   tasklist='"codex.exe","9","Console","1","5 K"\n')
        assert [p.pid for p in _running_codex_processes_windows()] == [9]

    def test_falls_back_when_powershell_finds_nothing(self, monkeypatch):
        self._stub(monkeypatch, powershell="[]",
                   tasklist='"codex.exe","9","Console","1","5 K"\n')
        assert [p.pid for p in _running_codex_processes_windows()] == [9]

    def test_both_unavailable_is_empty_not_an_error(self, monkeypatch):
        self._stub(monkeypatch, powershell=None, tasklist=None)
        assert _running_codex_processes_windows() == []
