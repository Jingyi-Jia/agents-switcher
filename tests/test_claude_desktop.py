from __future__ import annotations

import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from claude_swap import claude_desktop as cd
from claude_swap.providers import ProviderActionError
from claude_swap.web.server import DashboardState
from tests.test_web_actions import request, web


@pytest.fixture
def profiles(tmp_path, monkeypatch):
    monkeypatch.setattr(cd, "sys", SimpleNamespace(platform="linux"))
    executable = tmp_path / "official-app" / "claude-desktop"
    monkeypatch.setattr(cd, "installed_executable", lambda: executable)
    monkeypatch.setattr(cd, "running", lambda: False)
    process = Mock()
    process.wait.side_effect = subprocess.TimeoutExpired("claude-desktop", 0.75)
    launch = Mock(return_value=process)
    monkeypatch.setattr(cd.subprocess, "Popen", launch)
    return SimpleNamespace(manager=cd.ClaudeDesktopProfiles(tmp_path / "registry"), launch=launch, executable=executable)


def test_status_is_read_only_and_profiles_are_not_accounts(profiles):
    result = profiles.manager.status()
    assert result["available"] and result["experimental"]
    assert result["profiles"] == [] and result["running"] is False
    assert "Chrome" in result["notice"] and "not yet verified" in result["notice"]
    assert not profiles.manager.root.exists()
    profiles.launch.assert_not_called()


def test_create_only_makes_empty_private_directories_and_label_registry(profiles):
    manager = profiles.manager
    first = manager.create(" Work ", confirm=True)["profile"]
    second = manager.create("Personal", confirm=True)["profile"]
    assert first["name"] == "Work"
    assert first["id"] != second["id"]
    assert manager.status()["profiles"] == [first, second]
    for entry in (first, second):
        root = manager.root / "profiles" / entry["id"]
        for path in (root, root / "desktop", root / "claude-code"):
            assert path.is_dir()
            if os.name != "nt":
                assert path.stat().st_mode & 0o777 == 0o700
        assert not list((root / "desktop").iterdir())
        assert not list((root / "claude-code").iterdir())
    if os.name != "nt":
        assert (manager.root / "profiles.json").stat().st_mode & 0o777 == 0o600
    profiles.launch.assert_not_called()


@pytest.mark.parametrize("confirm", [None, False, 1, "true", [], {}])
def test_consent_is_never_coerced(profiles, confirm):
    with pytest.raises(ProviderActionError, match="Confirm"):
        profiles.manager.create("Work", confirm=confirm)
    with pytest.raises(ProviderActionError, match="Confirm"):
        profiles.manager.open("default", confirm=confirm)
    assert not profiles.manager.root.exists()
    profiles.launch.assert_not_called()


@pytest.mark.parametrize("name", [None, True, 3, [], {}, "", " ", "x" * 65, "a\nb", "a\x00b", "a\u202eb", "a\ud800b"])
def test_invalid_names_never_create_a_store(profiles, name):
    with pytest.raises(ProviderActionError, match="profile name"):
        profiles.manager.create(name, confirm=True)
    assert not profiles.manager.root.exists()


def test_duplicate_label_is_refused_without_changing_registry(profiles):
    profiles.manager.create("Work", confirm=True)
    before = (profiles.manager.root / "profiles.json").read_bytes()
    with pytest.raises(ProviderActionError, match="already"):
        profiles.manager.create("work", confirm=True)
    assert (profiles.manager.root / "profiles.json").read_bytes() == before


@pytest.mark.parametrize("raw", [b"{", b"null", b"{}", b'{"version":true,"profiles":[]}',
    b'{"version":1,"profiles":[{"id":"../../private","name":"Work"}]}',
    pytest.param(b"x" * 65537, id="oversized-registry"),
    b'{"version":1,"profiles":[],"profiles":[]}',
    b'{"version":1,"profiles":[],"token":"private-value"}', b"\xff"])
def test_corrupt_registry_is_not_an_empty_store_and_is_never_overwritten(profiles, raw):
    root = profiles.manager.root
    root.mkdir()
    (root / "profiles.json").write_bytes(raw)
    result = profiles.manager.status()
    assert not result["available"] and "invalid" in result["error"]
    assert "private-value" not in json.dumps(result)
    with pytest.raises(ProviderActionError, match="invalid"):
        profiles.manager.create("Work", confirm=True)
    assert (root / "profiles.json").read_bytes() == raw
    profiles.launch.assert_not_called()


def test_unicode_labels_at_capacity_remain_readable(profiles):
    entries = [{"id": f"{n:032x}", "name": f"{n:03}" + "🌿" * 61} for n in range(100)]
    profiles.manager.root.mkdir()
    profiles.manager._write(entries)
    assert profiles.manager._read() == entries
    with pytest.raises(ProviderActionError, match="limit"):
        profiles.manager.create("More", confirm=True)


def test_concurrent_creations_are_serialized(profiles):
    def create(number):
        return cd.ClaudeDesktopProfiles(profiles.manager.root).create(f"Profile {number}", confirm=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(create, range(4)))
    assert all(result["ok"] for result in results)
    assert len(profiles.manager.status()["profiles"]) == 4


@pytest.mark.parametrize("profile_id", [None, [], {}, True, 42, "", "../x", "a" * 31, "A" * 32, "--no-sandbox"])
def test_open_rejects_untrusted_paths_and_ids(profiles, profile_id):
    with pytest.raises(ProviderActionError):
        profiles.manager.open(profile_id, confirm=True)
    profiles.launch.assert_not_called()


def test_open_requires_registered_id_and_existing_directory(profiles):
    with pytest.raises(ProviderActionError, match="saved list"):
        profiles.manager.open("a" * 32, confirm=True)
    entry = profiles.manager.create("Work", confirm=True)["profile"]
    (profiles.manager.root / "profiles" / entry["id"] / "desktop").rmdir()
    with pytest.raises(ProviderActionError, match="missing"):
        profiles.manager.open(entry["id"], confirm=True)
    profiles.launch.assert_not_called()


@pytest.mark.skipif(os.name == "nt", reason="POSIX profile link safety")
@pytest.mark.parametrize("component", ["root", "registry", "profiles", "profile", "desktop", "claude-code"])
def test_profile_links_are_refused(profiles, tmp_path, component):
    entry = profiles.manager.create("Work", confirm=True)["profile"]
    base = profiles.manager.root
    paths = {
        "root": base, "registry": base / "profiles.json", "profiles": base / "profiles",
        "profile": base / "profiles" / entry["id"],
        "desktop": base / "profiles" / entry["id"] / "desktop",
        "claude-code": base / "profiles" / entry["id"] / "claude-code",
    }
    path = paths[component]
    destination = tmp_path / "original"
    path.rename(destination)
    path.symlink_to(destination, target_is_directory=destination.is_dir())
    with pytest.raises(ProviderActionError, match="link"):
        profiles.manager.open(entry["id"], confirm=True)
    profiles.launch.assert_not_called()


def test_launch_uses_only_registered_paths_and_sanitizes_ambient_credentials(profiles, monkeypatch):
    entry = profiles.manager.create('Work " $(touch nope)', confirm=True)["profile"]
    for name in ("CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_USER_DATA_DIR", "CLAUDE_CDP_AUTH", "ANTHROPIC_API_KEY",
                 "ELECTRON_RUN_AS_NODE", "NODE_OPTIONS", "LD_PRELOAD", "SSLKEYLOGFILE"):
        monkeypatch.setenv(name, "must-not-reach-child")
    result = profiles.manager.open(entry["id"], confirm=True)
    args, kwargs = profiles.launch.call_args
    base = profiles.manager.root / "profiles" / entry["id"]
    assert args == ([str(profiles.executable), f"--user-data-dir={base / 'desktop'}"],)
    assert kwargs["env"]["CLAUDE_CONFIG_DIR"] == str(base / "claude-code")
    assert "must-not-reach-child" not in kwargs["env"].values()
    assert kwargs["start_new_session"] and kwargs["close_fds"]
    assert kwargs["stdout"] == kwargs["stderr"] == kwargs["stdin"] == subprocess.DEVNULL
    assert result["ok"] and "launch requested" in result["message"]
    assert not (profiles.manager.root / "active").exists()


def test_usual_profile_has_no_override_or_cli_environment(profiles, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/some-cli-profile")
    monkeypatch.setenv("CLAUDE_USER_DATA_DIR", "/another-profile")
    profiles.manager.open("default", confirm=True)
    args, kwargs = profiles.launch.call_args
    assert args == ([str(profiles.executable)],)
    assert not any(key.startswith("CLAUDE_") for key in kwargs["env"])
    assert not (profiles.manager.root / "profiles.json").exists()


@pytest.mark.parametrize("target", ["default", "saved"])
def test_running_app_blocks_launch_without_stopping_it(profiles, monkeypatch, target):
    entry = profiles.manager.create("Work", confirm=True)["profile"]
    monkeypatch.setattr(cd, "running", lambda: True)
    assert profiles.manager.status()["running"] is True
    with pytest.raises(ProviderActionError, match="Fully quit"):
        profiles.manager.open(entry["id"] if target == "saved" else target, confirm=True)
    profiles.launch.assert_not_called()


def test_process_detection_failure_blocks_launch(profiles, monkeypatch):
    monkeypatch.setattr(cd, "running", Mock(side_effect=ProviderActionError("Unable to check")))
    assert not profiles.manager.status()["available"]
    with pytest.raises(ProviderActionError, match="Unable"):
        profiles.manager.open("default", confirm=True)
    profiles.launch.assert_not_called()


@pytest.mark.parametrize("exit_code", [0, 1])
def test_early_exit_never_reports_a_switch(profiles, exit_code):
    profiles.launch.return_value.wait.side_effect = None
    profiles.launch.return_value.wait.return_value = exit_code
    with pytest.raises(ProviderActionError, match="No account switch"):
        profiles.manager.open("default", confirm=True)


def test_startup_error_does_not_expose_diagnostic(profiles):
    profiles.launch.side_effect = OSError("private diagnostic")
    with pytest.raises(ProviderActionError, match="could not be launched") as error:
        profiles.manager.open("default", confirm=True)
    assert "private diagnostic" not in str(error.value)


def test_unsupported_platform_never_runs_or_creates_files(profiles, monkeypatch):
    monkeypatch.setattr(cd, "sys", SimpleNamespace(platform="win32"))
    assert not profiles.manager.status()["supported"]
    with pytest.raises(ProviderActionError, match="macOS and Linux"):
        profiles.manager.create("Work", confirm=True)
    with pytest.raises(ProviderActionError, match="macOS and Linux"):
        profiles.manager.open("default", confirm=True)
    assert not profiles.manager.root.exists()
    profiles.launch.assert_not_called()


def test_missing_installation_is_actionable(profiles, monkeypatch):
    monkeypatch.setattr(cd, "installed_executable", lambda: None)
    assert not profiles.manager.status()["installed"]
    with pytest.raises(ProviderActionError, match="Install the official"):
        profiles.manager.open("default", confirm=True)
    profiles.launch.assert_not_called()


def test_filesystem_errors_are_actionable_without_private_paths(profiles, monkeypatch):
    monkeypatch.setattr(profiles.manager, "_directory", Mock(side_effect=PermissionError("private-path")))
    with pytest.raises(ProviderActionError, match="folder permissions") as error:
        profiles.manager.create("Work", confirm=True)
    assert "private-path" not in str(error.value)
    profiles.launch.assert_not_called()


def test_unreadable_installation_does_not_break_status(monkeypatch):
    monkeypatch.setattr(cd, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(Path, "is_file", Mock(side_effect=PermissionError("private-path")))
    assert cd.installed_executable() is None


@pytest.mark.parametrize("platform,expected", [("darwin", "/Applications/Claude.app/Contents/MacOS/Claude"),
                                             ("linux", "/usr/bin/claude-desktop"), ("win32", None)])
def test_install_detection_never_uses_claude_code_cli(monkeypatch, platform, expected):
    monkeypatch.setattr(cd, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(Path, "is_file", lambda _: True)
    monkeypatch.setattr(cd.os, "access", lambda *_: True)
    result = cd.installed_executable()
    assert (str(result).replace("\\", "/") if result else None) == expected


@pytest.mark.parametrize("command,expected", [
    ("/Applications/Claude.app/Contents/MacOS/Claude", True),
    ("/Users/test/Applications/Claude.app/Contents/MacOS/Claude", True),
    ("/some folder/Claude.app/Contents/MacOS/Claude", True),
    ("claude-desktop", True), ("/usr/lib/claude-desktop/claude-desktop", True),
    ("claude", False), ("node", False), ("/usr/bin/claude", False),
])
def test_running_app_detection_does_not_confuse_cli_or_other_users(monkeypatch, command, expected):
    monkeypatch.setattr(cd.os, "getuid", lambda: 1000, raising=False)
    scan = Mock(return_value=SimpleNamespace(stdout=f"1000 {command}\n1001 /Applications/Claude.app/Contents/MacOS/Claude\n"))
    monkeypatch.setattr(cd.subprocess, "run", scan)
    assert cd.running() is expected
    assert scan.call_args.args[0] == ["/bin/ps", "-axo", "uid=,comm="]
    assert scan.call_args.kwargs["timeout"] == 3


@pytest.mark.parametrize("output", ["", "bad output", "1000", "1000 node\ninvalid", None])
def test_unreliable_process_listing_fails_closed(monkeypatch, output):
    monkeypatch.setattr(cd.os, "getuid", lambda: 1000, raising=False)
    monkeypatch.setattr(cd.subprocess, "run", Mock(return_value=SimpleNamespace(stdout=output)))
    with pytest.raises(ProviderActionError, match="Unable to check"):
        cd.running()


@pytest.mark.parametrize("route,payload", [
    ("create", {"name": "Work", "confirm": True}),
    ("open", {"profileId": "default", "confirm": True}),
])
def test_http_actions_require_header_token_and_exact_fields(web, profiles, route, payload):
    web.state.claude_desktop = profiles.manager
    path = f"/api/claude-desktop/{route}"
    assert request(web, path, payload, token=False)[0] == 403
    assert request(web, path + "?token=" + web.token, payload, token=False)[0] == 403
    assert request(web, path, {**payload, "path": "/arbitrary"})[0] == 400
    assert request(web, path, {**payload, "confirm": 1})[0] == 400
    assert request(web, path, {key: value for key, value in payload.items() if key != "confirm"})[0] == 400
    profiles.launch.assert_not_called()
    code, result, _ = request(web, path, payload)
    assert code == 200 and result["ok"]
    assert web.state._claude.calls == []
    assert web.state._codex.calls == []


def test_desktop_status_does_not_reuse_provider_cache(profiles, monkeypatch):
    state = DashboardState()
    state.claude_desktop = profiles.manager
    try:
        assert state.get()["claudeDesktop"]["profiles"] == []
        entry = profiles.manager.create("Work", confirm=True)["profile"]
        monkeypatch.setattr(cd, "running", lambda: True)
        result = state.get()["claudeDesktop"]
        assert result["profiles"] == [entry] and result["running"] is True
    finally:
        state.close()
