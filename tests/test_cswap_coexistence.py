import os
import plistlib
import subprocess
import sys
import tomllib
from pathlib import Path

from agents_switcher import launch_agent


def test_distribution_exports_only_its_own_console_command():
    metadata = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    project = metadata["project"]
    assert project["name"] == "agents-switcher"
    assert project["scripts"] == {"agent-switch": "agents_switcher.cli:main"}
    assert metadata["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["src/agents_switcher"]


def test_imports_do_not_claim_or_depend_on_upstreams_namespace(tmp_path):
    source = Path(__file__).parents[1] / "src"
    assert not (source / "claude_swap").exists()
    upstream = tmp_path / "modules" / "claude_swap"
    upstream.mkdir(parents=True)
    upstream.joinpath("__init__.py").write_text("owner = 'synthetic-upstream'\n")
    environment = {
        name: value for name, value in os.environ.items()
        if name.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "LANG", "LC_ALL", "TEMP", "TMP"}
    }
    for name in ("HOME", "USERPROFILE", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
                 "XDG_STATE_HOME", "APPDATA", "LOCALAPPDATA", "CLAUDE_CONFIG_DIR", "CODEX_HOME"):
        directory = tmp_path / name.lower()
        directory.mkdir()
        environment[name] = str(directory)
    environment["PYTHON_KEYRING_BACKEND"] = "keyring.backends.null.Keyring"
    for order in ("import agents_switcher; import claude_swap", "import claude_swap; import agents_switcher"):
        script = (
            "import sys; from pathlib import Path; "
            f"sys.path[:0] = {[str(upstream.parent), str(source)]!r}; "
            f"{order}; "
            "from agents_switcher import cli, desktop, launch_agent; "
            "assert claude_swap.owner == 'synthetic-upstream'; "
            "assert agents_switcher is not claude_swap; "
            "assert agents_switcher.ClaudeAccountSwitcher.__module__ == 'agents_switcher.switcher'; "
            f"assert Path(claude_swap.__file__).parent == Path({str(upstream)!r}); "
            "assert 'claude_swap.switcher' not in sys.modules; "
            "print('independent namespaces')"
        )
        result = subprocess.run(
            [sys.executable, "-I", "-c", script], env=environment, cwd=tmp_path,
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "independent namespaces"


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
