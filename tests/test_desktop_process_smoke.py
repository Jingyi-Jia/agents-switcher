from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from claude_swap import claude_desktop
from claude_swap.codex.desktop import CodexDesktop
from claude_swap.providers import ProviderActionError
from tests.test_tls import packaging_script


@pytest.fixture(autouse=True)
def codex_scan(monkeypatch):
    scan = Mock(return_value={"available": True, "running": False})
    monkeypatch.setattr(CodexDesktop, "status", scan)
    return scan


@pytest.mark.parametrize("running", [False, True])
def test_frozen_process_probe_checks_without_launching(monkeypatch, packaging_script, capsys, running, codex_scan):
    entry = packaging_script("backend_entry")
    scan = Mock(return_value=running)
    normal = Mock()
    monkeypatch.setattr(claude_desktop, "running", scan)
    monkeypatch.setattr(entry.runpy, "run_module", normal)
    monkeypatch.setattr(entry.sys, "argv", ["helper", "--smoke-processes"])
    monkeypatch.setattr(entry.sys, "platform", "linux")
    assert entry.main() == 0
    scan.assert_called_once_with()
    normal.assert_not_called()
    codex_scan.assert_called_once_with()
    assert capsys.readouterr().out == "Frozen helper process smoke passed\n"


def test_frozen_process_probe_fails_without_private_diagnostics(monkeypatch, packaging_script, capsys):
    entry = packaging_script("backend_entry")
    monkeypatch.setattr(claude_desktop, "running", Mock(side_effect=ProviderActionError("private-data")))
    monkeypatch.setattr(entry.sys, "argv", ["helper", "--smoke-processes"])
    monkeypatch.setattr(entry.sys, "platform", "linux")
    assert entry.main() == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "Frozen helper process smoke failed\n"


@pytest.mark.parametrize("status", [
    {"available": False, "running": None}, {"available": True, "running": None},
    {"available": 1, "running": False}, {"available": True, "running": 0}, {},
])
def test_frozen_probe_rejects_unknown_codex_status(monkeypatch, packaging_script, capsys, codex_scan, status):
    entry = packaging_script("backend_entry")
    monkeypatch.setattr(claude_desktop, "running", Mock(return_value=False))
    codex_scan.return_value = status
    monkeypatch.setattr(entry.sys, "argv", ["helper", "--smoke-processes"])
    assert entry.main() == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "Frozen helper process smoke failed\n"


def test_frozen_probe_checks_codex_on_windows_without_unsupported_claude_scan(monkeypatch, packaging_script, codex_scan):
    entry = packaging_script("backend_entry")
    claude_scan = Mock(side_effect=AssertionError("unsupported provider scan"))
    monkeypatch.setattr(claude_desktop, "running", claude_scan)
    monkeypatch.setattr(entry.sys, "platform", "win32")
    monkeypatch.setattr(entry.sys, "argv", ["helper", "--smoke-processes"])
    assert entry.main() == 0
    codex_scan.assert_called_once_with()
    claude_scan.assert_not_called()


def test_packaged_probe_is_isolated_and_bounded(monkeypatch, packaging_script, capsys):
    smoke = packaging_script("smoke_backend")
    run = Mock(return_value=SimpleNamespace(stdout="Frozen helper process smoke passed\n"))
    monkeypatch.setattr(smoke.subprocess, "run", run)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "private-data")
    smoke.check_processes(Path("helper"))
    assert run.call_args.args[0] == ["helper", "--smoke-processes"]
    options = run.call_args.kwargs
    assert options["timeout"] == 30 and options["check"] is True
    assert options["env"]["HOME"] == str(options["cwd"] / "home")
    assert "ANTHROPIC_API_KEY" not in options["env"]
    assert capsys.readouterr().out == "Frozen helper process smoke passed\n"


def test_packaged_probe_rejects_missing_confirmation(monkeypatch, packaging_script):
    smoke = packaging_script("smoke_backend")
    monkeypatch.setattr(smoke.subprocess, "run", Mock(return_value=SimpleNamespace(stdout="private-data")))
    with pytest.raises(RuntimeError, match="did not confirm") as error:
        smoke.check_processes(Path("helper"))
    assert "private-data" not in str(error.value)


def test_packaged_probe_hides_subprocess_failure_output(monkeypatch, packaging_script):
    smoke = packaging_script("smoke_backend")
    monkeypatch.setattr(smoke.subprocess, "run", Mock(side_effect=smoke.subprocess.CalledProcessError(
        1, "private-command", output="private-data", stderr="private-data",
    )))
    with pytest.raises(RuntimeError, match="^Frozen helper process smoke failed$"):
        smoke.check_processes(Path("helper"))


@pytest.mark.parametrize("platform", ["darwin", "linux", "win32", "freebsd"])
@pytest.mark.parametrize("enabled", [False, True])
def test_process_probe_is_explicit_and_only_runs_on_supported_platforms(monkeypatch, packaging_script, tmp_path, platform, enabled):
    smoke = packaging_script("smoke_backend")
    executable = tmp_path / "helper"
    executable.touch()
    scan, exercise = Mock(), Mock()
    monkeypatch.setattr(smoke, "check_processes", scan)
    monkeypatch.setattr(smoke, "exercise", exercise)
    monkeypatch.setattr(smoke, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr("sys.argv", [
        "smoke_backend.py", "--executable", str(executable), "--disposable-runner",
        *(["--check-processes"] if enabled else []),
    ])
    smoke.main()
    if enabled and platform in {"darwin", "linux", "win32"}:
        scan.assert_called_once_with(executable)
    else:
        scan.assert_not_called()
    assert exercise.call_count == 2
