import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import pytest


SHA = "a" * 40


@pytest.fixture
def installer():
    path = Path(__file__).resolve().parents[1] / "desktop/scripts/smoke_installers.py"
    spec = importlib.util.spec_from_file_location("smoke_installers", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("run_id,sha", [("../other", SHA), ("0", SHA), ("42", "main"), ("42", "A" * 40)])
def test_invalid_release_inputs_never_reach_github(installer, monkeypatch, run_id, sha):
    request = Mock()
    monkeypatch.setattr(installer.subprocess, "check_output", request)
    with pytest.raises(ValueError, match="run ID"):
        installer.validate_run(run_id, sha)
    request.assert_not_called()


@pytest.mark.parametrize("field,value", [
    ("head_sha", "b" * 40), ("head_branch", "feature"), ("path", ".github/workflows/desktop.yml"),
    ("event", "pull_request"), ("status", "in_progress"), ("conclusion", "failure"),
])
def test_only_successful_main_release_runs_are_accepted(installer, monkeypatch, field, value):
    data = {"head_sha": SHA, "head_branch": "main", "path": ".github/workflows/release.yml",
            "event": "workflow_dispatch", "status": "completed", "conclusion": "success"}
    request = Mock(return_value=json.dumps(data))
    monkeypatch.setattr(installer.subprocess, "check_output", request)
    installer.validate_run("42", SHA)
    assert request.call_args.args[0] == ["gh", "api", "repos/Jingyi-Jia/agents-switcher/actions/runs/42"]
    assert request.call_args.kwargs["timeout"] == 30
    data[field] = value
    request.return_value = json.dumps(data)
    with pytest.raises(ValueError, match="reviewed-main"):
        installer.validate_run("42", SHA)


@pytest.mark.parametrize("confirmation,actions,runner,target,arch", [
    (False, "true", "github-hosted", "mac", "arm64"),
    (1, "true", "github-hosted", "mac", "arm64"),
    (True, "false", "github-hosted", "mac", "arm64"),
    (True, "true", "self-hosted", "mac", "arm64"),
    (True, "true", "github-hosted", "win", "arm64"),
    (True, "true", "github-hosted", "mac", "x64"),
])
def test_native_guard_refuses_personal_or_mismatched_machines(installer, monkeypatch, confirmation, actions, runner, target, arch):
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    monkeypatch.setattr(installer.platform, "machine", lambda: "arm64")
    monkeypatch.setenv("GITHUB_ACTIONS", actions)
    monkeypatch.setenv("RUNNER_ENVIRONMENT", runner)
    with pytest.raises(ValueError, match="disposable GitHub-hosted"):
        installer.require_disposable(target, arch, confirmation)


@pytest.mark.parametrize("native,machine,target,arch", [
    ("darwin", "arm64", "mac", "arm64"), ("darwin", "x86_64", "mac", "x64"),
    ("win32", "AMD64", "win", "x64"),
])
def test_matching_disposable_native_runners_are_accepted(installer, monkeypatch, native, machine, target, arch):
    monkeypatch.setattr(installer.sys, "platform", native)
    monkeypatch.setattr(installer.platform, "machine", lambda: machine)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    installer.require_disposable(target, arch, True)


def test_failed_attestation_prevents_any_installer_execution(installer, monkeypatch, tmp_path):
    monkeypatch.setattr(installer, "require_disposable", lambda *args: None)
    verify = Mock(side_effect=ValueError("provenance mismatch"))
    monkeypatch.setattr(installer.runpy, "run_path", lambda path: {
        "source_version": lambda: "1.2.4", "verify_group": verify,
    })
    mac, windows = Mock(), Mock()
    monkeypatch.setattr(installer, "install_mac", mac)
    monkeypatch.setattr(installer, "install_windows", windows)
    with pytest.raises(ValueError, match="provenance"):
        installer.verify_installers(tmp_path, "win", "x64", SHA, True)
    verify.assert_called_once_with(tmp_path.resolve(), "win", "x64", "1.2.4", "community", SHA)
    mac.assert_not_called()
    windows.assert_not_called()


@pytest.mark.parametrize("target,arch", [("mac", "arm64"), ("mac", "x64"), ("win", "x64")])
def test_verified_group_routes_to_the_native_installer(installer, monkeypatch, tmp_path, target, arch):
    monkeypatch.setattr(installer, "require_disposable", lambda *args: None)
    verified = []
    monkeypatch.setattr(installer.runpy, "run_path", lambda path: {
        "source_version": lambda: "1.2.4", "verify_group": lambda *args: verified.append(args),
    })
    installed = []

    def install(*args):
        assert verified == [(tmp_path.resolve(), target, arch, "1.2.4", "community", SHA)]
        installed.append(args)

    monkeypatch.setattr(installer, "install_mac", install if target == "mac" else Mock(side_effect=AssertionError))
    monkeypatch.setattr(installer, "install_windows", install if target == "win" else Mock(side_effect=AssertionError))
    installer.verify_installers(tmp_path, target, arch, SHA, True)
    assert len(installed) == 1 and installed[0][0] == tmp_path.resolve()
    assert not installed[0][-1].exists()


def test_nsis_command_preserves_unquoted_destination_with_spaces(installer, monkeypatch, tmp_path):
    work = tmp_path / "test working directory"
    work.mkdir()
    artifacts = tmp_path / "verified artifacts"
    artifacts.mkdir()
    destination = work / "Installed Agent Switch"
    commands = []
    environment = {"HOME": str(work / "home")}
    monkeypatch.setattr(installer.runpy, "run_path", lambda path: {"isolated_environment": lambda root: environment})

    def run(command, **kwargs):
        commands.append(command)
        assert kwargs["check"] and kwargs["env"] == environment
        if len(commands) == 1:
            destination.mkdir()
            (destination / "Agent Switch.exe").touch()
            (destination / "Uninstall Agent Switch.exe").touch()
        else:
            (destination / "Agent Switch.exe").unlink()

    monkeypatch.setattr(installer.subprocess, "run", run)
    smoke = Mock()
    monkeypatch.setattr(installer, "smoke", smoke)
    installer.install_windows(artifacts, "1.2.4", work)
    assert commands == [
        f'"{artifacts / "Agent-Switch-1.2.4-win-x64.exe"}" /S /currentuser /D={destination}',
        f'"{destination / "Uninstall Agent Switch.exe"}" /S _?={destination}',
    ]
    smoke.assert_called_once_with(destination, "win", "x64", "1.2.4", work)


@pytest.mark.parametrize("fail_launch", [False, True])
def test_mac_installs_both_formats_and_always_cleans_owned_bundle(installer, monkeypatch, tmp_path, fail_launch):
    destination = tmp_path / "Applications/Agent Switch.app"
    monkeypatch.setattr(installer, "Path", lambda value: destination if value == "/Applications/Agent Switch.app" else Path(value))
    work, artifacts = tmp_path / "work", tmp_path / "artifacts"
    work.mkdir()
    artifacts.mkdir()
    commands, launched = [], []

    def run(command, **kwargs):
        commands.append(command)
        assert kwargs["check"] and kwargs["timeout"] > 0
        if command[:2] == ["hdiutil", "attach"]:
            (Path(command[command.index("-mountpoint") + 1]) / "Agent Switch.app").mkdir()
        elif command[:3] == ["ditto", "-x", "-k"]:
            (Path(command[-1]) / "Agent Switch.app").mkdir()
        elif command[0] == "ditto":
            assert command[-1] == str(destination)
            destination.mkdir(parents=True)

    def smoke(application, target, arch, version, case):
        assert application == destination and application.is_dir()
        assert (target, arch, version) == ("mac", "arm64", "1.2.4")
        launched.append(case.name)
        if fail_launch:
            raise RuntimeError("launch failed")

    monkeypatch.setattr(installer.subprocess, "run", run)
    monkeypatch.setattr(installer, "smoke", smoke)
    if fail_launch:
        with pytest.raises(RuntimeError, match="launch failed"):
            installer.install_mac(artifacts, "arm64", "1.2.4", work)
        assert launched == ["dmg"]
    else:
        installer.install_mac(artifacts, "arm64", "1.2.4", work)
        assert launched == ["dmg", "zip"]
    assert not destination.exists()
    assert sum(command[:2] == ["hdiutil", "detach"] for command in commands) == 1


def test_mac_never_removes_a_preexisting_application(installer, monkeypatch, tmp_path):
    destination = tmp_path / "Agent Switch.app"
    destination.mkdir()
    monkeypatch.setattr(installer, "Path", lambda value: destination)
    run = Mock()
    monkeypatch.setattr(installer.subprocess, "run", run)
    with pytest.raises(ValueError, match="existing Applications"):
        installer.install_mac(tmp_path, "arm64", "1.2.4", tmp_path)
    assert destination.exists()
    run.assert_not_called()
