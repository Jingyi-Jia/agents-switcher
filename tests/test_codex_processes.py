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
from claude_swap.codex.processes import CodexProcess, running_codex_processes

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
