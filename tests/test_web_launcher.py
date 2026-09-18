"""Tests for the desktop launcher and single-instance reuse.

Every launcher's CONTENT is built by a pure function, so all three platforms are
covered wherever this suite runs. The previous platform-specific code in this
project could only execute on the platform it targeted and therefore went
untested -- and broken -- everywhere else.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from claude_swap.web import launcher


class TestContent:
    """Built on any OS, so no platform goes uncovered."""

    def test_macos_script_execs_so_quitting_stops_the_server(self):
        script = launcher.macos_launch_script("/opt/bin/agent-switch")
        assert script.startswith("#!/bin/sh")
        # exec, not a plain call: the bundle's process must BE the server, or
        # quitting the app orphans it.
        assert "exec '/opt/bin/agent-switch' web" in script

    def test_macos_script_quotes_a_path_with_spaces(self):
        script = launcher.macos_launch_script("/Users/a b/Applications/agent-switch")
        assert "'/Users/a b/Applications/agent-switch' web" in script

    def test_macos_plist_declares_an_app(self):
        plist = launcher.macos_info_plist()
        assert "<string>APPL</string>" in plist
        assert launcher.BUNDLE_ID in plist
        assert f"<string>{launcher.APP_NAME}</string>" in plist

    def test_macos_plist_does_not_hide_the_app(self):
        # The dashboard opens a browser; the Dock icon is the obvious way to
        # quit it, so the app must not be background-only.
        assert "LSBackgroundOnly" not in launcher.macos_info_plist()

    def test_linux_entry_is_a_valid_desktop_file(self):
        entry = launcher.linux_desktop_entry("/usr/bin/agent-switch")
        assert entry.startswith("[Desktop Entry]")
        assert "Type=Application" in entry
        assert "Exec=/usr/bin/agent-switch web" in entry
        assert "Terminal=false" in entry

    def test_windows_script_creates_a_shortcut_with_the_web_argument(self):
        script = launcher.windows_shortcut_script(
            r"C:\tools\agent-switch.exe", Path(r"C:\Menu\agent-switch.lnk")
        )
        assert "CreateShortcut" in script
        assert r"agent-switch.exe" in script
        assert "$s.Arguments = 'web'" in script
        assert "$s.Save()" in script


class TestPlan:
    @pytest.mark.parametrize(
        "platform,suffix",
        [("darwin", ".app"), ("win32", ".lnk"), ("linux", ".desktop")],
    )
    def test_each_platform_has_a_target(self, platform, suffix):
        target = launcher.plan(platform)
        assert target.supported
        assert target.paths[0].name.endswith(suffix)

    def test_an_unknown_platform_is_reported_not_guessed(self):
        target = launcher.plan("aix")
        assert target.supported is False
        assert "aix" in target.reason
        assert target.paths == ()

    def test_macos_installs_per_user_not_system_wide(self):
        # ~/Applications needs no sudo and matches where everything else this
        # tool writes lives.
        assert launcher.plan("darwin").paths[0].is_relative_to(Path.home())

    def test_linux_honours_xdg_data_home(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        assert launcher.plan("linux").paths[0].parent == tmp_path / "applications"


class TestExecutableResolution:
    def test_prefers_the_script_beside_the_interpreter(self, monkeypatch, tmp_path):
        """A launcher must not rely on the desktop session having the terminal's
        PATH -- it will not."""
        suffix = ".exe" if sys.platform == "win32" else ""
        script = tmp_path / f"{launcher.APP_NAME}{suffix}"
        script.write_text("")
        monkeypatch.setattr(sys, "executable", str(tmp_path / "python"))
        assert launcher.executable_path() == str(script)

    def test_falls_back_to_path_lookup(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "executable", str(tmp_path / "python"))
        monkeypatch.setattr(launcher.shutil, "which", lambda name: "/usr/bin/agent-switch")
        assert launcher.executable_path() == "/usr/bin/agent-switch"

    def test_last_resort_reenters_through_the_interpreter(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "executable", str(tmp_path / "python"))
        monkeypatch.setattr(launcher.shutil, "which", lambda name: None)
        assert "claude_swap.cli" in launcher.executable_path()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX install paths")
class TestInstallUninstall:
    @pytest.fixture(autouse=True)
    def _home(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))

    def test_macos_bundle_structure(self, tmp_path):
        launcher.install("darwin")
        app = launcher.macos_app_path()
        assert (app / "Contents" / "Info.plist").exists()
        script = app / "Contents" / "MacOS" / launcher.APP_NAME
        assert script.exists()
        assert script.stat().st_mode & 0o111  # executable, or nothing launches

    def test_install_is_idempotent(self, tmp_path):
        launcher.install("darwin")
        launcher.install("darwin")   # must replace, not fail
        assert launcher.is_installed("darwin")

    def test_linux_entry_is_written(self, tmp_path):
        launcher.install("linux")
        entry = launcher.linux_desktop_path()
        assert entry.exists()
        assert "Exec=" in entry.read_text()

    def test_uninstall_removes_and_reports(self, tmp_path):
        launcher.install("darwin")
        removed = launcher.uninstall("darwin")
        assert removed == [launcher.macos_app_path()]
        assert not launcher.is_installed("darwin")

    def test_uninstalling_nothing_is_not_an_error(self, tmp_path):
        assert launcher.uninstall("darwin") == []

    def test_an_unsupported_platform_installs_nothing(self, tmp_path):
        assert launcher.install("aix").supported is False
