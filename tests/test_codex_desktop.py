from __future__ import annotations

import ctypes
import errno
import io
import json
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from claude_swap.codex import desktop as cd

_READ_MAC_ARGUMENTS = cd._read_mac_arguments
_READ_MAC_EXECUTABLE = cd._read_mac_executable
_READ_LINUX_ARGUMENTS = cd._read_linux_arguments
_READ_LINUX_EXECUTABLE = cd._read_linux_executable
_NATIVE_PLATFORM = sys.platform
_NATIVE_GETUID = getattr(os, "getuid", None)
_NATIVE_KILL = os.kill
_NATIVE_RUN = subprocess.run
_NATIVE_POPEN = subprocess.Popen


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(cd, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(cd.os, "getuid", lambda: 501, raising=False)
    monkeypatch.setattr(cd.os, "kill", Mock(side_effect=AssertionError("Unexpected process existence check")))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setattr(cd, "_gui_home", Path.home)
    monkeypatch.setattr(cd.subprocess, "run", Mock(side_effect=AssertionError("Unexpected subprocess")))
    monkeypatch.setattr(cd.subprocess, "Popen", Mock(side_effect=AssertionError("Unexpected launch")))
    monkeypatch.setattr(cd, "_read_mac_arguments", Mock(side_effect=AssertionError("Unexpected process metadata read")))
    monkeypatch.setattr(cd, "_read_mac_executable", Mock(side_effect=AssertionError("Unexpected executable metadata read")))
    monkeypatch.setattr(cd, "_read_linux_arguments", Mock(side_effect=AssertionError("Unexpected process metadata read")))
    monkeypatch.setattr(cd, "_read_linux_executable", Mock(side_effect=AssertionError("Unexpected executable metadata read")))


@pytest.fixture
def scan(monkeypatch):
    app = Path("/Applications/Codex.app").absolute()
    source = {}
    output = ["50 1 501 S ?? /usr/bin/python3"]
    run = Mock(side_effect=lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "\n".join(output), ""))
    monkeypatch.setattr(cd.subprocess, "run", run)
    monkeypatch.setattr(cd, "_installed_apps", lambda: [app])
    monkeypatch.setattr(cd, "_read_mac_arguments", lambda pid: source[pid])
    monkeypatch.setattr(cd, "_read_mac_executable", lambda pid: "/usr/bin/python3" if pid == 50 else source[pid][0])
    monkeypatch.setattr(cd, "_read_linux_arguments", lambda pid: source[pid])
    monkeypatch.setattr(cd, "_read_linux_executable", lambda pid: "/usr/bin/python3" if pid == 50 else source[pid][0])

    def add(pid, executable, *arguments, parent=1, owner=501, tty="??", state="S"):
        executable = str(executable)
        output.append(f"{pid} {parent} {owner} {state} {tty} {executable}")
        source[pid] = (executable, (executable, *arguments))

    return SimpleNamespace(app=app, add=add, output=output, source=source, run=run, backend=cd.CodexDesktop())


def add_desktop(scan):
    scan.add(100, scan.app / "Contents/MacOS/Codex")
    scan.add(101, scan.app / "Contents/Resources/codex", "app-server", parent=100)
    scan.add(102, scan.app / "Contents/Frameworks/Codex Helper (Renderer).app/Contents/MacOS/Codex Helper (Renderer)", parent=100)


def test_stopped_status_is_read_only_and_has_only_public_fields(scan):
    assert scan.backend.status() == {
        "available": True, "running": False, "desktopRunning": False,
        "terminalCount": 0, "backgroundCount": 0, "canAssist": False,
        "canOpen": True, "message": None,
    }
    cd.subprocess.Popen.assert_not_called()
    args, kwargs = scan.run.call_args
    assert args[0] == ["/bin/ps", "-axww", "-o", "pid=,ppid=,uid=,stat=,tty=,comm="]
    assert kwargs["check"] is True
    assert kwargs["timeout"] == 5
    assert kwargs["env"] == {"PATH": "/usr/bin:/bin", "LC_ALL": "C"}


def test_official_main_embedded_server_and_helpers_allow_assistance(scan):
    add_desktop(scan)
    status = scan.backend.status()
    assert status["available"] is True
    assert status["running"] is True
    assert status["desktopRunning"] is True
    assert status["terminalCount"] == status["backgroundCount"] == 0
    assert status["canAssist"] is True
    assert status["canOpen"] is False


def test_desktop_main_alone_blocks_switching(scan):
    scan.add(100, scan.app / "Contents/MacOS/Codex")
    status = scan.backend.status()
    assert status["running"] is True
    assert status["desktopRunning"] is True


@pytest.mark.parametrize("executable,arguments,tty,terminal,background", [
    ("/opt/bin/codex", (), "ttys003", 1, 0),
    ("/opt/bin/codex", ("app-server",), "??", 0, 1),
    ("/opt/bin/codex-aarch64-apple-darwin", (), "??", 0, 1),
    ("/opt/bin/codex-x86_64-unknown-linux-musl", (), "pts/2", 1, 0),
    ("/opt/bin/node", ("--require", "helper.js", "/path with spaces/codex.js"), "ttys004", 1, 0),
    ("/opt/bin/node", ("--inspect=0", "/path with spaces/codex.js"), "??", 0, 1),
    ("/opt/bin/bun", ("/opt/codex/bin/codex.js",), "??", 0, 1),
    ("/Applications/ChatGPT.app/Contents/Resources/codex", ("app-server",), "??", 0, 1),
    ("/other/Codex.app/Contents/MacOS/Codex", (), "??", 0, 1),
])
def test_terminal_and_other_app_blockers_are_never_closed(scan, executable, arguments, tty, terminal, background):
    add_desktop(scan)
    scan.add(200, executable, *arguments, tty=tty)
    status = scan.backend.status()
    assert status["running"] is True
    assert status["terminalCount"] == terminal
    assert status["backgroundCount"] == background
    assert status["canAssist"] is status["canOpen"] is False
    assert scan.backend.quit(True)["quitRequested"] is False
    assert all(call.args[0][0] == "/bin/ps" for call in scan.run.call_args_list)


@pytest.mark.parametrize("parent,arguments,tty", [
    (1, ("app-server",), "??"),
    (100, ("exec", "app-server-notes"), "??"),
    (100, ("exec", "app-server"), "??"),
    (100, ("-c", "app-server", "exec"), "??"),
    (100, ("app-server-notes",), "??"),
    (100, ("app-server",), "ttys003"),
    (101, ("app-server",), "??"),
])
def test_bundle_executable_requires_proven_app_ownership_and_app_server(scan, parent, arguments, tty):
    scan.add(100, scan.app / "Contents/MacOS/Codex")
    scan.add(101, scan.app / "Contents/Resources/codex", *arguments, parent=parent, tty=tty)
    status = scan.backend.status()
    assert status["running"] is True
    assert status["canAssist"] is False
    assert status["terminalCount"] + status["backgroundCount"] == 1


def test_cli_server_is_blocked_even_when_a_descendant_of_the_app(scan):
    scan.add(100, scan.app / "Contents/MacOS/Codex")
    scan.add(101, "/opt/bin/codex", "app-server", parent=100)
    assert scan.backend.status()["backgroundCount"] == 1
    assert scan.backend.status()["canAssist"] is False


@pytest.mark.parametrize("arguments", [
    ("app-server",), ("-c", "private-setting=value", "app-server"),
    ("--config=key=value", "app-server"), ("--enable", "feature", "app-server"),
])
def test_actual_app_server_subcommand_is_recognized_with_global_options(scan, arguments):
    scan.add(100, scan.app / "Contents/MacOS/Codex")
    scan.add(101, scan.app / "Contents/Resources/codex", *arguments, parent=100)
    assert scan.backend.status()["canAssist"] is True


def test_background_codex_in_frameworks_is_not_mistaken_for_a_gui_helper(scan):
    scan.add(100, scan.app / "Contents/MacOS/Codex")
    scan.add(101, scan.app / "Contents/Frameworks/codex", "exec", parent=100)
    assert scan.backend.status()["backgroundCount"] == 1
    assert scan.backend.status()["canAssist"] is False


def test_shell_between_app_and_embedded_server_does_not_prove_ownership(scan):
    scan.add(100, scan.app / "Contents/MacOS/Codex")
    scan.add(101, "/bin/sh", parent=100)
    scan.add(102, scan.app / "Contents/Resources/codex", "app-server", parent=101)
    assert scan.backend.status()["canAssist"] is False


def test_orphan_helper_remains_a_manual_blocker(scan):
    scan.add(100, scan.app / "Contents/Frameworks/Codex Helper.app/Contents/MacOS/Codex Helper")
    status = scan.backend.status()
    assert status["running"] is True
    assert status["backgroundCount"] == 1
    assert status["canAssist"] is False


def test_status_never_exposes_commands_paths_or_account_identifiers(scan):
    scan.add(100, "/private/secret/codex", "--token", "synthetic-private-token", "test@example.invalid", tty="private-terminal")
    public = json.dumps(scan.backend.status())
    for private in ("synthetic-private-token", "test@example.invalid", "private-terminal", "/private/secret"):
        assert private not in public


@pytest.mark.parametrize("owner,uid,expected", [
    (502, 501, False), (-2, 501, False), (-2, 2**32 - 2, True),
    (-2147483648, 2**31, True), (4294967294, 2**32 - 2, True),
])
def test_ownership_and_macos_signed_uids(scan, monkeypatch, owner, uid, expected):
    monkeypatch.setattr(cd.os, "getuid", lambda: uid)
    scan.output[0] = f"50 1 {uid} S ?? /usr/bin/python3"
    scan.add(100, "/opt/bin/codex", owner=owner)
    assert scan.backend.status()["running"] is expected


def test_other_users_metadata_is_not_read(scan, monkeypatch):
    scan.add(100, "/opt/bin/codex", owner=502)
    reader = Mock(side_effect=AssertionError("Must not inspect another user"))
    monkeypatch.setattr(cd, "_read_mac_arguments", reader)
    assert scan.backend.status()["running"] is False
    reader.assert_not_called()


@pytest.mark.parametrize("uid", [0, 501])
def test_macos_kernel_pid_zero_is_valid_and_never_a_codex_process(scan, monkeypatch, uid):
    monkeypatch.setattr(cd.os, "getuid", lambda: uid)
    scan.output[0] = f"50 1 {uid} S ?? /usr/bin/python3"
    scan.output.insert(0, "0 0 0 R ?? kernel_task")
    reader = Mock(side_effect=AssertionError("Must not inspect kernel PID zero"))
    monkeypatch.setattr(cd, "_read_mac_arguments", reader)
    status = scan.backend.status()
    assert status["available"] is True
    assert status["running"] is False
    reader.assert_not_called()
    cd.os.kill.assert_not_called()


@pytest.mark.parametrize("row", ["0 1 0 R ?? kernel_task", "0 0 501 R ?? kernel_task"])
def test_malformed_kernel_pid_zero_still_fails_closed(scan, row):
    scan.output.insert(0, row)
    assert scan.backend.status()["running"] is None


@pytest.mark.parametrize("row", [
    "", "not a table", "100 1 501 S ??", "100 1 501 S ??   ",
    "100 1 -2147483649 S ?? codex", "100 1 4294967296 S ?? codex",
    "100 1 --2 S ?? codex", "100 1 +501 S ?? codex", "100 1 ٥٠١ S ?? codex",
    "0 1 501 S ?? codex", "100 -1 501 S ?? codex", "100 1 501 broken ?? codex",
    "50 1 501 S ?? codex", "100 1 unknown Z ??", "100 1 501 S ?? /usr/bin/python\ninvalid continuation",
])
def test_malformed_rows_fail_the_whole_scan(scan, row):
    scan.output.insert(0, row)
    status = scan.backend.status()
    assert status["available"] is False
    assert status["running"] is None
    assert status["canAssist"] is status["canOpen"] is False


@pytest.mark.parametrize("output", ["", "   ", None, b"binary", "100 1 502 S ?? /bin/sh"])
def test_empty_nontext_or_noncurrent_process_tables_are_unknown(scan, output):
    scan.run.side_effect = None
    scan.run.return_value = subprocess.CompletedProcess([], 0, output, "")
    assert scan.backend.status()["running"] is None


@pytest.mark.parametrize("row", ["100 1 501 Z ??", "100 1 501 Z+ ?? <defunct>", "100 1 -2 Z ??"])
def test_confirmed_zombies_can_be_ignored(scan, row):
    scan.output.append(row)
    assert scan.backend.status()["running"] is False


@pytest.mark.parametrize("failure", [
    PermissionError("private scan details"),
    subprocess.TimeoutExpired("private command", 5, stderr="private output"),
    subprocess.CalledProcessError(1, "private command", stderr="private output"),
    UnicodeDecodeError("utf-8", b"\xff", 0, 1, "private data"),
])
def test_scan_failures_are_unknown_and_sanitized(scan, failure):
    scan.run.side_effect = failure
    status = scan.backend.status()
    assert status["available"] is False
    assert status["running"] is None
    assert status["canAssist"] is status["canOpen"] is False
    assert "private" not in status["message"]
    assert scan.backend.quit(True)["ok"] is False
    assert scan.backend.open(True)["ok"] is False
    cd.subprocess.Popen.assert_not_called()


def test_nonzero_scan_exit_cannot_report_stopped(scan):
    scan.run.side_effect = None
    scan.run.return_value = subprocess.CompletedProcess([], 1, "50 1 501 S ?? /bin/sh", "private")
    assert scan.backend.status()["running"] is None


def test_process_metadata_race_is_unknown_not_stopped(scan, monkeypatch):
    scan.add(100, "/opt/bin/codex")
    monkeypatch.setattr(cd, "_read_mac_arguments", Mock(side_effect=ProcessLookupError("private process")))
    exists = Mock()
    monkeypatch.setattr(cd.os, "kill", exists)
    assert scan.backend.status()["running"] is None
    exists.assert_called_once_with(100, 0)


@pytest.mark.parametrize("failure", [
    FileNotFoundError(errno.ENOENT, "private process"),
    OSError("private native metadata"), ValueError("private truncated metadata"),
])
def test_process_metadata_failure_is_skipped_only_after_confirmed_esrch(scan, monkeypatch, failure):
    scan.add(100, "/opt/bin/codex")
    monkeypatch.setattr(cd, "_read_mac_arguments", Mock(side_effect=failure))
    exists = Mock(side_effect=ProcessLookupError(errno.ESRCH, "Process exited"))
    monkeypatch.setattr(cd.os, "kill", exists)
    status = scan.backend.status()
    assert status["available"] is True
    assert status["running"] is False
    exists.assert_called_once_with(100, 0)


@pytest.mark.parametrize("failure", [
    PermissionError(errno.EPERM, "private ownership"),
    OSError(errno.EIO, "private process check"),
    ProcessLookupError("Lookup without an ESRCH result"),
])
def test_uncertain_existence_check_preserves_unknown_state(scan, monkeypatch, failure):
    scan.add(100, "/opt/bin/codex")
    monkeypatch.setattr(cd, "_read_mac_arguments", Mock(side_effect=ProcessLookupError(errno.ESRCH, "old scan")))
    exists = Mock(side_effect=failure)
    monkeypatch.setattr(cd.os, "kill", exists)
    status = scan.backend.status()
    assert status["available"] is False
    assert status["running"] is None
    assert "private" not in status["message"]
    exists.assert_called_once_with(100, 0)


def test_exited_process_does_not_hide_other_live_blockers(scan, monkeypatch):
    scan.add(100, "/opt/bin/codex")
    scan.add(101, "/opt/bin/codex", tty="ttys003")
    monkeypatch.setattr(cd, "_read_mac_arguments", Mock(side_effect=[FileNotFoundError(), scan.source[101]]))
    monkeypatch.setattr(cd.os, "kill", Mock(side_effect=ProcessLookupError(errno.ESRCH, "Process exited")))
    status = scan.backend.status()
    assert status["available"] is True
    assert status["running"] is True
    assert status["terminalCount"] == 1


@pytest.mark.parametrize("value", [False, None, 0, 1, "true", "yes", {}, [], object()])
@pytest.mark.parametrize("method,key", [("quit", "quitRequested"), ("open", "launchRequested")])
def test_consent_must_be_exactly_true(scan, value, method, key):
    result = getattr(scan.backend, method)(value)
    assert result["ok"] is result[key] is False
    scan.run.assert_not_called()
    cd.subprocess.Popen.assert_not_called()


@pytest.mark.parametrize("value", ["/custom/codex", "~/.codex", "relative/config", "\n"])
def test_custom_codex_home_disables_assistance_but_not_manual_process_checks(scan, monkeypatch, value):
    monkeypatch.setenv("CODEX_HOME", value)
    add_desktop(scan)
    status = scan.backend.status()
    assert status["available"] is status["running"] is True
    assert status["canAssist"] is status["canOpen"] is False
    assert "CODEX_HOME" in status["message"]
    assert scan.backend.quit(True)["ok"] is False
    scan.output[:] = scan.output[:1]
    assert scan.backend.status()["running"] is False
    assert scan.backend.open(True)["ok"] is False


@pytest.mark.parametrize("explicit_default", [False, True])
def test_empty_and_explicit_default_codex_home_are_compatible(scan, monkeypatch, explicit_default):
    monkeypatch.setenv("CODEX_HOME", str(Path.home() / ".codex") if explicit_default else "")
    assert scan.backend.status()["canOpen"] is True


def test_home_override_cannot_launch_gui_against_another_auth_store(scan, monkeypatch, tmp_path):
    monkeypatch.setattr(cd, "_gui_home", lambda: tmp_path / "different-home")
    assert scan.backend.status()["available"] is True
    assert scan.backend.status()["canOpen"] is False


def test_missing_app_still_allows_manual_stopped_check(scan, monkeypatch):
    monkeypatch.setattr(cd, "_installed_apps", lambda: [])
    status = scan.backend.status()
    assert status["available"] is True
    assert status["running"] is False
    assert status["canAssist"] is status["canOpen"] is False


def test_unverified_main_is_still_a_blocker(scan, monkeypatch):
    scan.add(100, scan.app / "Contents/MacOS/Codex")
    monkeypatch.setattr(cd, "_installed_apps", lambda: [])
    status = scan.backend.status()
    assert status["running"] is True
    assert status["backgroundCount"] == 1
    assert status["canAssist"] is False


def test_unverified_or_uninstalled_bundle_helpers_remain_manual_blockers(scan, monkeypatch):
    scan.add(100, scan.app / "Contents/Frameworks/Codex Helper.app/Contents/MacOS/Codex Helper")
    monkeypatch.setattr(cd, "_installed_apps", lambda: [])
    status = scan.backend.status()
    assert status["running"] is True
    assert status["backgroundCount"] == 1
    assert status["canAssist"] is False


def test_two_running_installations_are_not_auto_closed(scan, monkeypatch):
    other = Path.home() / "Applications/Codex.app"
    monkeypatch.setattr(cd, "_installed_apps", lambda: [scan.app, other])
    scan.add(100, scan.app / "Contents/MacOS/Codex")
    scan.add(101, other / "Contents/MacOS/Codex")
    assert scan.backend.status()["canAssist"] is False


def test_quit_requests_only_verified_running_app_with_fixed_native_script(scan):
    add_desktop(scan)
    output = "\n".join(scan.output)
    scan.run.side_effect = [subprocess.CompletedProcess([], 0, output, ""),
                            subprocess.CompletedProcess([], 0, "requested\n", "")]
    result = scan.backend.quit(confirm=True)
    assert result["ok"] is result["quitRequested"] is True
    assert "has not changed" in result["message"]
    assert "Wait for all Codex processes" in result["message"]
    args, kwargs = scan.run.call_args
    assert args[0] == ["/usr/bin/osascript", "-l", "JavaScript", "-e", cd._QUIT_SCRIPT, str(scan.app)]
    assert kwargs["timeout"] == 10
    assert kwargs["check"] is True
    assert "shell" not in kwargs
    assert "NSWorkspace.sharedWorkspace.runningApplications" in cd._QUIT_SCRIPT
    assert "forceTerminate" not in cd._QUIT_SCRIPT
    assert "applicationWithBundleIdentifier" not in cd._QUIT_SCRIPT
    cd.subprocess.Popen.assert_not_called()


@pytest.mark.parametrize("response", ["refused", "not-running", "ambiguous", "", "private-output"])
def test_quit_refusal_or_race_is_not_success(scan, response):
    add_desktop(scan)
    scan.run.side_effect = [subprocess.CompletedProcess([], 0, "\n".join(scan.output), ""),
                            subprocess.CompletedProcess([], 0, response, "private stderr")]
    result = scan.backend.quit(True)
    assert result["ok"] is result["quitRequested"] is False
    assert "has not changed" in result["message"]
    assert "private" not in result["message"]


@pytest.mark.parametrize("failure", [
    PermissionError("private details"), subprocess.TimeoutExpired("private command", 10),
    subprocess.CalledProcessError(1, "private command", stderr="private data"),
])
def test_quit_permissions_cancellation_and_timeout_are_sanitized(scan, failure):
    add_desktop(scan)
    scan.run.side_effect = [subprocess.CompletedProcess([], 0, "\n".join(scan.output), ""), failure]
    result = scan.backend.quit(True)
    assert result["ok"] is False
    assert "quit it manually" in result["message"]
    assert "private" not in result["message"]
    cd.subprocess.Popen.assert_not_called()


def test_quit_rechecks_instead_of_trusting_old_status(scan):
    add_desktop(scan)
    assert scan.backend.status()["canAssist"] is True
    scan.add(200, "/bin/codex", tty="ttys003")
    assert scan.backend.quit(True)["ok"] is False
    assert all(call.args[0][0] == "/bin/ps" for call in scan.run.call_args_list)


def test_open_uses_fixed_verified_executable_and_minimal_environment(scan, monkeypatch):
    popen = Mock()
    monkeypatch.setattr(cd.subprocess, "Popen", popen)
    dangerous = ["OPENAI_API_KEY", "OPENAI_BASE_URL", "CODEX_TOKEN", "CODEX_HOME", "ANTHROPIC_API_KEY",
                 "CLAUDE_CONFIG_DIR", "NODE_OPTIONS", "NODE_PATH", "ELECTRON_RUN_AS_NODE", "ELECTRON_ENABLE_LOGGING",
                 "DYLD_INSERT_LIBRARIES", "LD_PRELOAD", "SSLKEYLOGFILE", "AWS_SECRET_ACCESS_KEY", "CUSTOM_SECRET", "LC_PRIVATE_TOKEN"]
    for key in dangerous:
        monkeypatch.setenv(key, "" if key == "CODEX_HOME" else "synthetic-private-value")
    result = scan.backend.open(True)
    assert result["ok"] is result["launchRequested"] is True
    assert "Check the active account" in result["message"]
    args, kwargs = popen.call_args
    assert args[0] == [str(scan.app / "Contents/MacOS/Codex")]
    assert kwargs["cwd"] == "/"
    assert kwargs["start_new_session"] is kwargs["close_fds"] is True
    assert kwargs["stdin"] == kwargs["stdout"] == kwargs["stderr"] == subprocess.DEVNULL
    assert not set(dangerous) & set(kwargs["env"])
    assert "shell" not in kwargs
    assert kwargs["env"]["HOME"] == str(Path.home())


def test_open_rechecks_instead_of_trusting_old_status(scan):
    assert scan.backend.status()["canOpen"] is True
    scan.add(100, "/bin/codex")
    assert scan.backend.open(True)["ok"] is False
    cd.subprocess.Popen.assert_not_called()


def test_launch_failure_is_sanitized(scan, monkeypatch):
    monkeypatch.setattr(cd.subprocess, "Popen", Mock(side_effect=OSError("private executable")))
    result = scan.backend.open(True)
    assert result["ok"] is result["launchRequested"] is False
    assert "private" not in result["message"]


@pytest.mark.parametrize("running", [False, True])
def test_linux_supports_manual_check_but_never_assistance(scan, monkeypatch, running):
    monkeypatch.setattr(cd, "sys", SimpleNamespace(platform="linux"))
    if running:
        scan.add(100, "/bin/codex", tty="pts/9")
    status = scan.backend.status()
    assert status["available"] is True
    assert status["running"] is running
    assert status["terminalCount"] == int(running)
    assert status["canAssist"] is status["canOpen"] is False
    assert scan.backend.quit(True)["ok"] is scan.backend.open(True)["ok"] is False
    cd.subprocess.Popen.assert_not_called()


@pytest.mark.parametrize("executable,arguments", [
    ("/usr/bin/node", ("/opt/codex/bin/codex.js",)),
    ("/opt/bin/codex", ()),
])
@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_posix_uses_executable_metadata_instead_of_mutable_process_titles(scan, monkeypatch, executable, arguments, platform):
    monkeypatch.setattr(cd, "sys", SimpleNamespace(platform=platform))
    scan.add(100, executable, *arguments)
    scan.output[-1] = "100 1 501 S ?? MainThread"
    assert scan.backend.status()["running"] is True


@pytest.mark.parametrize("failure,expected", [
    (ProcessLookupError(errno.ESRCH, "Process exited"), False),
    (PermissionError(errno.EPERM, "private permission"), None),
    (None, None),
])
def test_linux_unreadable_executable_requires_a_fresh_esrch_result(scan, monkeypatch, failure, expected):
    monkeypatch.setattr(cd, "sys", SimpleNamespace(platform="linux"))
    scan.add(100, "/opt/bin/codex")
    monkeypatch.setattr(cd, "_read_linux_executable", Mock(side_effect=["/usr/bin/python3", FileNotFoundError()]))
    exists = Mock(side_effect=failure)
    monkeypatch.setattr(cd.os, "kill", exists)
    assert scan.backend.status()["running"] is expected
    exists.assert_called_once_with(100, 0)


def test_linux_protected_non_target_requires_matching_command_identity(scan, monkeypatch):
    monkeypatch.setattr(cd, "sys", SimpleNamespace(platform="linux"))
    scan.add(100, "/usr/bin/protected-agent")
    monkeypatch.setattr(cd, "_read_linux_executable", Mock(side_effect=["/usr/bin/python3", PermissionError(errno.EACCES, "protected")]))
    command = Mock(return_value=("/usr/bin/protected-agent", "--foreground"))
    monkeypatch.setattr(cd, "_read_linux_command", command)
    status = scan.backend.status()
    assert status["available"] is True and status["running"] is False
    command.assert_called_once_with(100)
    cd.os.kill.assert_not_called()
    scan.add(101, "/usr/bin/codex")
    monkeypatch.setattr(cd, "_read_linux_executable", Mock(side_effect=["/usr/bin/python3", PermissionError(errno.EACCES, "protected"), "/usr/bin/codex"]))
    status = scan.backend.status()
    assert status["available"] is True and status["running"] is True
    assert status["backgroundCount"] == 1


@pytest.mark.parametrize("name,arguments", [
    ("node", ("/usr/bin/node", "/private/codex.js")),
    ("node", ("/usr/bin/node", "--eval", "process.stdin.resume()")),
    ("bun", ("/usr/bin/bun", "script.js")),
    ("MainThread", ("MainThread",)),
    ("codex", ("/usr/bin/codex", "app-server")),
    ("codex-x86_64-unknown-linux-musl", ("/bin/codex-x86_64-unknown-linux-musl",)),
    ("protected-agent", ("/usr/bin/different-agent",)),
    ("protected-agent", ("/usr/bin/protected-agent", "/private/codex.js")),
])
def test_linux_protected_relevant_or_inconsistent_commands_remain_unknown(scan, monkeypatch, name, arguments):
    monkeypatch.setattr(cd, "sys", SimpleNamespace(platform="linux"))
    scan.add(100, "/usr/bin/" + name)
    monkeypatch.setattr(cd, "_read_linux_executable", Mock(side_effect=["/usr/bin/python3", PermissionError(errno.EACCES, "protected")]))
    monkeypatch.setattr(cd, "_read_linux_command", Mock(return_value=arguments))
    monkeypatch.setattr(cd.os, "kill", Mock())
    status = scan.backend.status()
    assert status["available"] is False and status["running"] is None


@pytest.mark.skipif(_NATIVE_PLATFORM != "linux", reason="Exercises Linux non-dumpable process metadata")
def test_native_linux_protected_background_process_does_not_hide_codex(monkeypatch):
    monkeypatch.setattr(cd, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(cd.os, "getuid", _NATIVE_GETUID)
    monkeypatch.setattr(cd.os, "kill", _NATIVE_KILL)
    monkeypatch.setattr(cd.subprocess, "run", _NATIVE_RUN)
    monkeypatch.setattr(cd.subprocess, "Popen", _NATIVE_POPEN)
    monkeypatch.setattr(cd, "_installed_apps", lambda: [])
    monkeypatch.setattr(cd, "_read_linux_executable", _READ_LINUX_EXECUTABLE)
    monkeypatch.setattr(cd, "_read_linux_arguments", _READ_LINUX_ARGUMENTS)
    code = 'import ctypes,sys; assert ctypes.CDLL(None).prctl(4,0,0,0,0) == 0; print("ready",flush=True); sys.stdin.readline()'
    with subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=cd._child_environment()) as process:
        try:
            assert process.stdout.readline().strip() == "ready"
            if _NATIVE_GETUID() != 0:
                with pytest.raises(PermissionError):
                    _READ_LINUX_EXECUTABLE(process.pid)
            assert cd.CodexDesktop().status()["available"] is True
        finally:
            process.stdin.close()
            process.wait(timeout=5)


def test_unsupported_platform_cannot_authorize_switching(scan, monkeypatch):
    monkeypatch.setattr(cd, "sys", SimpleNamespace(platform="freebsd"))
    status = scan.backend.status()
    assert status["available"] is False
    assert status["running"] is None
    assert scan.backend.quit(True)["ok"] is scan.backend.open(True)["ok"] is False
    scan.run.assert_not_called()


def windows_payload(rows):
    return {"Complete": True, "CurrentUser": "S-1-5-21-1000", "Processes": rows}


def windows_row(pid=100, owner="S-1-5-21-1000", executable=r"C:\Program Files\Codex\codex.exe", command=None):
    return {"ProcessId": pid, "ParentProcessId": 50, "OwnerSid": owner,
            "ExecutablePath": executable, "CommandLine": command if command is not None else f'"{executable}"'}


@pytest.mark.parametrize("rows,count", [
    ([], 0), ([windows_row()], 1),
    ([windows_row(owner="S-1-5-21-2000")], 0),
    ([windows_row(executable=r"C:\Program Files\nodejs\node.exe",
                  command=r'"C:\Program Files\nodejs\node.exe" "C:\Users\sample user\npm\codex.js"')], 1),
    ([windows_row(executable=r"C:\node.exe", command=r'node C:\server.js')], 0),
])
def test_windows_verified_scan_supports_manual_switching(scan, monkeypatch, rows, count):
    monkeypatch.setattr(cd, "sys", SimpleNamespace(platform="win32"))
    scan.run.side_effect = None
    scan.run.return_value = subprocess.CompletedProcess([], 0, json.dumps(windows_payload(rows)), "")
    status = scan.backend.status()
    assert status["available"] is True
    assert status["running"] is bool(count)
    assert status["backgroundCount"] == count
    assert status["canAssist"] is status["canOpen"] is False
    assert scan.backend.quit(True)["ok"] is scan.backend.open(True)["ok"] is False
    args, kwargs = scan.run.call_args
    assert args[0][-1] == cd._WINDOWS_SCRIPT
    assert "GetOwnerSid" in cd._WINDOWS_SCRIPT
    assert kwargs["check"] is True
    assert "tasklist" not in str(scan.run.call_args_list)


@pytest.mark.parametrize("data", [
    None, [], {}, {"Complete": False, "CurrentUser": "S-1-5-21-1000", "Processes": []},
    {"Complete": True, "CurrentUser": "", "Processes": []},
    windows_payload([None]), windows_payload([windows_row(pid=True)]),
    windows_payload([windows_row(), windows_row()]),
    windows_payload([windows_row(owner="unknown")]),
    windows_payload([{**windows_row(), "CommandLine": None}]),
    windows_payload([{**windows_row(), "ExecutablePath": None}]),
    windows_payload([windows_row(command='"unterminated')]),
])
def test_windows_malformed_or_unowned_metadata_is_unknown(scan, monkeypatch, data):
    monkeypatch.setattr(cd, "sys", SimpleNamespace(platform="win32"))
    scan.run.side_effect = None
    scan.run.return_value = subprocess.CompletedProcess([], 0, json.dumps(data), "")
    assert scan.backend.status()["running"] is None


@pytest.mark.parametrize("output", ["", "private invalid json", "[]"])
def test_windows_bad_output_has_no_unsafe_fallback(scan, monkeypatch, output):
    monkeypatch.setattr(cd, "sys", SimpleNamespace(platform="win32"))
    scan.run.side_effect = None
    scan.run.return_value = subprocess.CompletedProcess([], 0, output, "")
    assert scan.backend.status()["running"] is None
    assert scan.run.call_count == 1


def test_windows_other_users_missing_command_is_ignored(scan, monkeypatch):
    monkeypatch.setattr(cd, "sys", SimpleNamespace(platform="win32"))
    row = {**windows_row(owner="S-1-5-21-2000"), "ExecutablePath": None, "CommandLine": None}
    scan.run.side_effect = None
    scan.run.return_value = subprocess.CompletedProcess([], 0, json.dumps(windows_payload([row])), "")
    assert scan.backend.status()["running"] is False


def test_mac_kernel_arguments_preserve_spaces_and_do_not_parse_environment():
    raw = bytes(ctypes.c_int(3)) + b"/path with spaces/codex\0\0/path with spaces/codex\0app-server\0\0SECRET=private\0"
    assert cd._parse_mac_arguments(raw) == ("/path with spaces/codex", ("/path with spaces/codex", "app-server", ""))


@pytest.mark.parametrize("raw", [b"", b"\0\0", bytes(ctypes.c_int(0)) + b"/bin/codex\0", bytes(ctypes.c_int(2)) + b"/bin/codex\0codex\0", bytes(ctypes.c_int(1)) + b"\0codex\0"])
def test_mac_kernel_arguments_reject_incomplete_records(raw):
    with pytest.raises(ValueError):
        cd._parse_mac_arguments(raw)


def test_mac_native_metadata_uses_fixed_kernel_request(monkeypatch):
    raw = bytes(ctypes.c_int(2)) + b"/bin/codex\0\0codex\0app-server\0SECRET=private\0"

    def sysctl(mib, count, buffer, size, new_value, new_length):
        if list(mib) == [1, 8]:
            assert count == 2
            ctypes.cast(buffer, ctypes.POINTER(ctypes.c_int)).contents.value = 32768
            return 0
        assert list(mib) == [1, 49, 123]
        assert count == 3
        assert ctypes.cast(size, ctypes.POINTER(ctypes.c_size_t)).contents.value == 32768
        assert new_value is None and new_length == 0
        ctypes.memmove(buffer, raw, len(raw))
        ctypes.cast(size, ctypes.POINTER(ctypes.c_size_t)).contents.value = len(raw)
        return 0

    library = SimpleNamespace(sysctl=Mock(side_effect=sysctl))
    loader = Mock(return_value=library)
    monkeypatch.setattr(cd.ctypes, "CDLL", loader)
    assert _READ_MAC_ARGUMENTS(123) == ("/bin/codex", ("codex", "app-server"))
    loader.assert_called_once_with("/usr/lib/libSystem.B.dylib", use_errno=True)


@pytest.mark.parametrize("maximum", [0, -1, 4, cd._MAX_ARGUMENT_BYTES + 1])
def test_mac_argument_limit_must_be_valid_and_bounded(monkeypatch, maximum):
    def sysctl(mib, count, buffer, size, new_value, new_length):
        assert list(mib) == [1, 8]
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_int)).contents.value = maximum
        return 0

    monkeypatch.setattr(cd.ctypes, "CDLL", Mock(return_value=SimpleNamespace(sysctl=Mock(side_effect=sysctl))))
    with pytest.raises(ValueError, match="argument limit"):
        _READ_MAC_ARGUMENTS(123)


def test_mac_native_metadata_permission_failure_is_not_an_empty_command(monkeypatch):
    monkeypatch.setattr(cd.ctypes, "CDLL", Mock(return_value=SimpleNamespace(sysctl=Mock(return_value=-1))))
    with pytest.raises(OSError, match="unavailable"):
        _READ_MAC_ARGUMENTS(123)


def test_mac_native_executable_uses_fixed_kernel_process_path_lookup(monkeypatch):
    def proc_pidpath(pid, buffer, size):
        assert pid == 123
        assert size == 4096
        executable = b"/path with spaces/node\0"
        ctypes.memmove(buffer, executable, len(executable))
        return len(executable) - 1

    library = SimpleNamespace(proc_pidpath=Mock(side_effect=proc_pidpath))
    loader = Mock(return_value=library)
    monkeypatch.setattr(cd.ctypes, "CDLL", loader)
    assert _READ_MAC_EXECUTABLE(123) == "/path with spaces/node"
    loader.assert_called_once_with("/usr/lib/libproc.dylib", use_errno=True)


@pytest.mark.parametrize("result", [0, -1])
def test_mac_native_executable_failures_are_not_empty_executables(monkeypatch, result):
    library = SimpleNamespace(proc_pidpath=Mock(return_value=result))
    monkeypatch.setattr(cd.ctypes, "CDLL", Mock(return_value=library))
    with pytest.raises(OSError, match="unavailable"):
        _READ_MAC_EXECUTABLE(123)


def test_linux_native_metadata_preserves_spaces_and_deleted_executables(monkeypatch):
    monkeypatch.setattr(cd, "_read_linux_executable", _READ_LINUX_EXECUTABLE)
    link = Mock(return_value="/private path/codex (deleted)")
    monkeypatch.setattr(cd.os, "readlink", link)
    opened = []

    def read(path, mode):
        opened.append((path, mode))
        return io.BytesIO(b"/private path/codex\0exec\0a prompt with spaces\0")

    monkeypatch.setattr(Path, "open", read)
    assert _READ_LINUX_ARGUMENTS(123) == ("/private path/codex", ("/private path/codex", "exec", "a prompt with spaces"))
    link.assert_called_once_with(Path("/proc/123/exe"))
    assert opened == [(Path("/proc/123/cmdline"), "rb")]


@pytest.mark.parametrize("raw", [b"", b"codex", b"\0", b"x" * (cd._MAX_ARGUMENT_BYTES + 1) + b"\0"],
                         ids=["empty", "unterminated", "empty-argument", "oversized"])
def test_linux_native_metadata_rejects_missing_or_truncated_arguments(monkeypatch, raw):
    monkeypatch.setattr(cd, "_read_linux_executable", _READ_LINUX_EXECUTABLE)
    monkeypatch.setattr(cd.os, "readlink", lambda path: "/bin/codex")
    monkeypatch.setattr(Path, "open", lambda *args: io.BytesIO(raw))
    with pytest.raises(ValueError):
        _READ_LINUX_ARGUMENTS(123)


@pytest.mark.skipif(_NATIVE_PLATFORM not in {"darwin", "linux"}, reason="Exercises native POSIX process metadata")
def test_native_posix_status_reads_harmless_node_arguments(monkeypatch):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the native argument-reader smoke test")
    monkeypatch.setattr(cd, "sys", SimpleNamespace(platform=_NATIVE_PLATFORM))
    monkeypatch.setattr(cd.os, "getuid", _NATIVE_GETUID)
    monkeypatch.setattr(cd.os, "kill", _NATIVE_KILL)
    monkeypatch.setattr(cd.subprocess, "run", _NATIVE_RUN)
    monkeypatch.setattr(cd.subprocess, "Popen", _NATIVE_POPEN)
    monkeypatch.setattr(cd, "_installed_apps", lambda: [])
    monkeypatch.setattr(cd, "_read_mac_executable", _READ_MAC_EXECUTABLE)
    monkeypatch.setattr(cd, "_read_linux_executable", _READ_LINUX_EXECUTABLE)
    original_scan = cd._posix_processes

    def checked_scan():
        try:
            return original_scan()
        except (OSError, ValueError) as error:
            raise AssertionError("Native process scan failed before classification") from error

    monkeypatch.setattr(cd, "_posix_processes", checked_scan)
    reader = Mock(wraps=_READ_MAC_ARGUMENTS if _NATIVE_PLATFORM == "darwin" else _READ_LINUX_ARGUMENTS)
    name = "_read_mac_arguments" if _NATIVE_PLATFORM == "darwin" else "_read_linux_arguments"
    monkeypatch.setattr(cd, name, reader)
    with subprocess.Popen(
        [node, "-e", "process.stdin.resume()"], env=cd._child_environment(),
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ) as process:
        try:
            assert process.poll() is None
            status = cd.CodexDesktop().status()
            assert status["available"] is True, status["message"]
            assert any(call.args == (process.pid,) for call in reader.call_args_list)
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def create_app(home, metadata=None):
    app = home / "Applications/Codex.app"
    executable = app / "Contents/MacOS/Codex"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"synthetic non-executable fixture")
    executable.chmod(0o755)
    data = metadata if metadata is not None else {"CFBundleIdentifier": "com.openai.codex", "CFBundleExecutable": "Codex", "CFBundlePackageType": "APPL"}
    (app / "Contents/Info.plist").write_bytes(plistlib.dumps(data))
    return app


def test_installed_bundle_identity_and_fixed_candidates(monkeypatch):
    app = create_app(Path.home())
    original_is_file = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda path: False if path == Path("/Applications/Codex.app/Contents/MacOS/Codex") else original_is_file(path))
    assert cd._installed_apps() == [app]


@pytest.mark.parametrize("metadata", [
    {}, {"CFBundleIdentifier": "other.app", "CFBundleExecutable": "Codex", "CFBundlePackageType": "APPL"},
    {"CFBundleIdentifier": "com.openai.codex", "CFBundleExecutable": "../../bin/sh", "CFBundlePackageType": "APPL"},
    {"CFBundleIdentifier": "com.openai.codex", "CFBundleExecutable": "Codex", "CFBundlePackageType": "BNDL"},
])
def test_invalid_bundle_metadata_never_becomes_a_launch_target(monkeypatch, metadata):
    app = create_app(Path.home(), metadata)
    assert app not in cd._installed_apps()


@pytest.mark.parametrize("raw", [b"invalid", b"x" * 262145, b'<?xml version="1.0"?><plist><dict>'],
                         ids=["invalid", "oversized", "incomplete-xml"])
def test_invalid_or_oversized_plist_is_unavailable(raw):
    app = create_app(Path.home())
    (app / "Contents/Info.plist").write_bytes(raw)
    assert app not in cd._installed_apps()


def test_linked_bundle_is_not_a_launch_target(monkeypatch, tmp_path):
    app = create_app(tmp_path)
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == app or original(path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert app not in cd._installed_apps()


def test_missing_or_nonexecutable_binary_is_not_a_launch_target(monkeypatch):
    app = create_app(Path.home())
    monkeypatch.setattr(cd.os, "access", lambda *args: False)
    assert app not in cd._installed_apps()


def test_bundle_paths_cannot_use_traversal_to_prove_ownership(scan):
    scan.add(100, scan.app / "Contents/MacOS/Codex")
    scan.add(101, str(scan.app / "Contents/Frameworks") + "/../../other/codex", "app-server", parent=100)
    assert scan.backend.status()["canAssist"] is False
