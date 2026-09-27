import plistlib
import subprocess
import tomllib
from pathlib import Path

from claude_swap import launch_agent


def test_distribution_exports_only_its_own_console_command():
    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())["project"]
    assert project["name"] == "agents-switcher"
    assert project["scripts"] == {"agent-switch": "claude_swap.cli:main"}


def test_launch_agent_never_selects_an_upstream_cswap_executable(tmp_path, monkeypatch):
    upstream = tmp_path / "cswap"
    upstream.write_text("synthetic upstream launcher")
    own = tmp_path / "agent-switch"
    own.write_text("synthetic fork launcher")
    monkeypatch.setattr(launch_agent.sys, "argv", [str(upstream)])
    lookups = []

    def which(name):
        lookups.append(name)
        return str(own) if name == "agent-switch" else str(upstream)

    monkeypatch.setattr(launch_agent.shutil, "which", which)
    assert launch_agent.resolve_program() == [str(own)]
    assert lookups == ["agent-switch"]


def test_install_and_uninstall_leave_upstream_launch_agent_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(launch_agent.sys, "platform", "darwin")
    legacy_label = "com.cswap.menubar"
    assert launch_agent.LABEL == "io.github.jingyi-jia.agent-switch.menubar"
    legacy = launch_agent.plist_path(legacy_label, tmp_path)
    legacy.parent.mkdir(parents=True)
    original = plistlib.dumps({"Label": legacy_label, "ProgramArguments": ["cswap", "menubar"]})
    legacy.write_bytes(original)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1 if command[1] == "print" else 0, "", "")

    monkeypatch.setattr(launch_agent.subprocess, "run", run)
    installed = launch_agent.install(home=tmp_path, uid=501, program=["/synthetic/agent-switch"])
    own = Path(installed["plist"])
    assert own.exists() and own != legacy
    launch_agent.uninstall(home=tmp_path, uid=501)
    assert not own.exists()
    assert legacy.read_bytes() == original
    assert all(legacy_label not in argument for command in calls for argument in command)
