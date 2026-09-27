"""Tests for update_check module."""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from agents_switcher.update_check import (
    CACHE_TTL,
    LATEST_RELEASE_URL,
    MAX_RESPONSE_BYTES,
    _NoRedirect,
    _detect_install_method,
    check_for_update,
    run_self_upgrade,
)


def _make_github_opener(version: str) -> MagicMock:
    url = "https://github.com/Jingyi-Jia/agents-switcher/releases"
    wheel = f"agents_switcher-{version}-py3-none-any.whl"
    data = json.dumps({"tag_name": f"v{version}", "draft": False, "prerelease": False,
                       "html_url": f"{url}/tag/v{version}", "assets": [{
                           "name": wheel, "state": "uploaded", "size": 100,
                           "browser_download_url": f"{url}/download/v{version}/{wheel}",
                       }]}).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = data
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    opener = MagicMock()
    opener.open.return_value = mock_resp
    return opener


def _write_cache(path, version, timestamp=None):
    """Write a cache file in the shared cache format."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "timestamp": timestamp if timestamp is not None else time.time(),
        "data": version,
    }))


class TestCheckForUpdate:
    @patch("agents_switcher.update_check.urllib.request.build_opener")
    def test_newer_version_available(self, mock_build_opener, tmp_path, monkeypatch):
        monkeypatch.setattr("agents_switcher.update_check.CACHE_PATH", tmp_path / "cache.json")
        mock_build_opener.return_value = _make_github_opener("0.4.0")

        result = check_for_update("0.3.2")

        assert result is not None
        assert "0.4.0" in result
        assert "0.3.2" in result

    @patch("agents_switcher.update_check.urllib.request.build_opener")
    def test_already_on_latest(self, mock_build_opener, tmp_path, monkeypatch):
        monkeypatch.setattr("agents_switcher.update_check.CACHE_PATH", tmp_path / "cache.json")
        mock_build_opener.return_value = _make_github_opener("0.3.2")

        result = check_for_update("0.3.2")

        assert result is None

    @patch("agents_switcher.update_check.urllib.request.build_opener", side_effect=OSError("network error"))
    def test_network_error_returns_none_and_caches(self, mock_build_opener, tmp_path, monkeypatch):
        cache_path = tmp_path / "cache.json"
        monkeypatch.setattr("agents_switcher.update_check.CACHE_PATH", cache_path)

        result = check_for_update("0.3.2")

        assert result is None
        assert cache_path.exists()
        cache = json.loads(cache_path.read_text())
        assert cache["data"] is None

    @patch("agents_switcher.update_check.urllib.request.build_opener")
    def test_fresh_error_cache_skips_network(self, mock_build_opener, tmp_path, monkeypatch):
        cache_path = tmp_path / "cache.json"
        _write_cache(cache_path, None)
        monkeypatch.setattr("agents_switcher.update_check.CACHE_PATH", cache_path)

        result = check_for_update("0.3.2")

        mock_build_opener.assert_not_called()
        assert result is None

    def test_fresh_cache_no_network(self, tmp_path, monkeypatch):
        cache_path = tmp_path / "cache.json"
        _write_cache(cache_path, "0.5.0")
        monkeypatch.setattr("agents_switcher.update_check.CACHE_PATH", cache_path)

        with patch("agents_switcher.update_check.urllib.request.build_opener") as mock_build_opener:
            result = check_for_update("0.3.2")
            mock_build_opener.assert_not_called()

        assert result is not None
        assert "0.5.0" in result

    @patch("agents_switcher.update_check.urllib.request.build_opener")
    def test_stale_cache_fetches_from_github(self, mock_build_opener, tmp_path, monkeypatch):
        cache_path = tmp_path / "cache.json"
        _write_cache(cache_path, "0.3.0", timestamp=time.time() - CACHE_TTL - 1)
        monkeypatch.setattr("agents_switcher.update_check.CACHE_PATH", cache_path)
        mock_build_opener.return_value = _make_github_opener("0.4.0")

        result = check_for_update("0.3.2")

        mock_build_opener.assert_called_once()
        assert result is not None
        assert "0.4.0" in result


class TestDetectInstallMethod:
    def _set_prefix(self, monkeypatch, prefix: str) -> None:
        monkeypatch.setattr("agents_switcher.update_check.sys.prefix", prefix)
        # Clear env vars by default so path-based detection runs in isolation.
        monkeypatch.delenv("UV_TOOL_DIR", raising=False)
        monkeypatch.delenv("PIPX_HOME", raising=False)

    def test_uv_tool_default_path(self, monkeypatch):
        self._set_prefix(monkeypatch, "/home/me/.local/share/uv/tools/claude-swap")
        assert _detect_install_method() == "uv"

    def test_pipx_default_path(self, monkeypatch):
        self._set_prefix(monkeypatch, "/home/me/.local/pipx/venvs/claude-swap")
        assert _detect_install_method() == "pipx"

    def test_non_adjacent_uv_tools_does_not_match(self, monkeypatch):
        # Both segments present but not adjacent — must not false-positive.
        self._set_prefix(monkeypatch, "/home/me/projects/uv/some-tools/.venv")
        assert _detect_install_method() is None

    def test_non_adjacent_pipx_venvs_does_not_match(self, monkeypatch):
        self._set_prefix(monkeypatch, "/home/me/repos/pipx-clone/venvs-of-mine/.venv")
        assert _detect_install_method() is None

    def test_source_checkout_returns_none(self, monkeypatch):
        self._set_prefix(monkeypatch, "/home/me/code/claude-swap/.venv")
        assert _detect_install_method() is None

    def test_mixed_case_path_detected(self, monkeypatch):
        # Lowercasing should make matching case-insensitive (e.g. Windows).
        self._set_prefix(monkeypatch, "/Home/Me/.local/share/UV/Tools/claude-swap")
        assert _detect_install_method() == "uv"

    def test_uv_tool_dir_env_with_prefix_under_it(self, monkeypatch, tmp_path):
        custom_root = tmp_path / "uv-tools"
        prefix = custom_root / "claude-swap"
        monkeypatch.setattr("agents_switcher.update_check.sys.prefix", str(prefix))
        monkeypatch.setenv("UV_TOOL_DIR", str(custom_root))
        monkeypatch.delenv("PIPX_HOME", raising=False)
        assert _detect_install_method() == "uv"

    def test_uv_tool_dir_env_set_but_prefix_elsewhere(self, monkeypatch, tmp_path):
        custom_root = tmp_path / "uv-tools"
        # Prefix lives somewhere else entirely — env var alone must not trigger.
        monkeypatch.setattr(
            "agents_switcher.update_check.sys.prefix", str(tmp_path / "some-project" / ".venv")
        )
        monkeypatch.setenv("UV_TOOL_DIR", str(custom_root))
        monkeypatch.delenv("PIPX_HOME", raising=False)
        assert _detect_install_method() is None

    def test_pipx_home_env_with_prefix_under_it(self, monkeypatch, tmp_path):
        custom_root = tmp_path / "pipx-home"
        prefix = custom_root / "venvs" / "claude-swap"
        monkeypatch.setattr("agents_switcher.update_check.sys.prefix", str(prefix))
        monkeypatch.setenv("PIPX_HOME", str(custom_root))
        monkeypatch.delenv("UV_TOOL_DIR", raising=False)
        assert _detect_install_method() == "pipx"


class TestCheckForUpdateMessage:
    @patch("agents_switcher.update_check.sys.platform", "linux")
    @patch("agents_switcher.update_check.urllib.request.build_opener")
    def test_detected_method_non_windows_suggests_agent_switch_upgrade(
        self, mock_build_opener, tmp_path, monkeypatch
    ):
        # uv/pipx on macOS/Linux: agent-switch upgrade actually upgrades, so advertise it.
        monkeypatch.setattr("agents_switcher.update_check.CACHE_PATH", tmp_path / "cache.json")
        monkeypatch.setattr("agents_switcher.update_check._detect_install_method", lambda: "uv")
        mock_build_opener.return_value = _make_github_opener("0.4.0")

        result = check_for_update("0.3.2")

        assert result is not None
        assert "agent-switch upgrade" in result
        assert "uv tool upgrade" not in result

    @patch("agents_switcher.update_check.sys.platform", "win32")
    @patch("agents_switcher.update_check.urllib.request.build_opener")
    def test_detected_method_windows_suggests_direct_command(
        self, mock_build_opener, tmp_path, monkeypatch
    ):
        # Windows: agent-switch upgrade only prints, so point at the real command.
        monkeypatch.setattr("agents_switcher.update_check.CACHE_PATH", tmp_path / "cache.json")
        monkeypatch.setattr("agents_switcher.update_check._detect_install_method", lambda: "pipx")
        mock_build_opener.return_value = _make_github_opener("0.4.0")

        result = check_for_update("0.3.2")

        assert result is not None
        assert "pipx upgrade agents-switcher" in result
        assert "agent-switch upgrade" not in result

    @patch("agents_switcher.update_check.urllib.request.build_opener")
    def test_unknown_method_suggests_agent_switch_instructions(
        self, mock_build_opener, tmp_path, monkeypatch
    ):
        # Unknown install method: agent-switch upgrade can only show instructions.
        monkeypatch.setattr("agents_switcher.update_check.CACHE_PATH", tmp_path / "cache.json")
        monkeypatch.setattr("agents_switcher.update_check._detect_install_method", lambda: None)
        mock_build_opener.return_value = _make_github_opener("0.4.0")

        result = check_for_update("0.3.2")

        assert result is not None
        assert "agent-switch upgrade` for upgrade instructions" in result
        assert "uv tool upgrade" not in result
        assert "pipx upgrade" not in result


@patch("agents_switcher.update_check.sys.platform", "linux")
class TestRunSelfUpgrade:
    @patch("agents_switcher.update_check.subprocess.run")
    @patch("agents_switcher.update_check._detect_install_method", return_value="uv")
    def test_uv_invokes_uv_tool_upgrade(self, mock_detect, mock_run):
        mock_run.return_value = MagicMock(returncode=0)

        assert run_self_upgrade() == 0
        mock_run.assert_called_once_with(
            ["uv", "tool", "upgrade", "agents-switcher"], check=False
        )

    @patch("agents_switcher.update_check.subprocess.run")
    @patch("agents_switcher.update_check._detect_install_method", return_value="pipx")
    def test_pipx_invokes_pipx_upgrade(self, mock_detect, mock_run):
        mock_run.return_value = MagicMock(returncode=0)

        assert run_self_upgrade() == 0
        mock_run.assert_called_once_with(
            ["pipx", "upgrade", "agents-switcher"], check=False
        )

    @patch("agents_switcher.update_check.subprocess.run")
    @patch("agents_switcher.update_check._detect_install_method", return_value="uv")
    def test_propagates_nonzero_exit_code(self, mock_detect, mock_run):
        mock_run.return_value = MagicMock(returncode=2)

        assert run_self_upgrade() == 2

    @patch("agents_switcher.update_check.subprocess.run")
    @patch("agents_switcher.update_check._detect_install_method", return_value=None)
    def test_unknown_method_returns_1_and_prints_instructions(
        self, mock_detect, mock_run, capsys
    ):
        assert run_self_upgrade() == 1
        mock_run.assert_not_called()
        err = capsys.readouterr().err
        assert "uv tool upgrade agents-switcher" in err
        assert "pipx upgrade agents-switcher" in err
        assert "pip install --upgrade git+https://github.com/Jingyi-Jia/agents-switcher.git" in err

    @patch(
        "agents_switcher.update_check.subprocess.run", side_effect=FileNotFoundError
    )
    @patch("agents_switcher.update_check._detect_install_method", return_value="uv")
    def test_filenotfound_returns_1(self, mock_detect, mock_run, capsys):
        assert run_self_upgrade() == 1
        err = capsys.readouterr().err
        assert "PATH" in err


@patch("agents_switcher.update_check.sys.platform", "win32")
class TestRunSelfUpgradeWindows:
    """On Windows the running .exe is locked, so we never upgrade in place --
    we print the command for the user to run themselves and exit 1."""

    @patch("agents_switcher.update_check.subprocess.run")
    @patch("agents_switcher.update_check._detect_install_method", return_value="uv")
    def test_uv_prints_command_and_does_not_run(self, mock_detect, mock_run, capsys):
        assert run_self_upgrade() == 1
        mock_run.assert_not_called()
        out = capsys.readouterr().out
        assert "uv tool upgrade agents-switcher" in out

    @patch("agents_switcher.update_check.subprocess.run")
    @patch("agents_switcher.update_check._detect_install_method", return_value="pipx")
    def test_pipx_prints_command_and_does_not_run(self, mock_detect, mock_run, capsys):
        assert run_self_upgrade() == 1
        mock_run.assert_not_called()
        out = capsys.readouterr().out
        assert "pipx upgrade agents-switcher" in out

    @patch("agents_switcher.update_check.subprocess.run")
    @patch("agents_switcher.update_check._detect_install_method", return_value=None)
    def test_unknown_method_hits_generic_fallback(self, mock_detect, mock_run, capsys):
        assert run_self_upgrade() == 1
        mock_run.assert_not_called()
        err = capsys.readouterr().err
        assert "uv tool upgrade agents-switcher" in err
        assert "pipx upgrade agents-switcher" in err
        assert "pip install --upgrade git+https://github.com/Jingyi-Jia/agents-switcher.git" in err


def test_release_check_uses_only_our_public_repository_without_ambient_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr("agents_switcher.update_check.CACHE_PATH", tmp_path / "cache.json")
    monkeypatch.setenv("GH_TOKEN", "synthetic-private-token")
    monkeypatch.setenv("GITHUB_TOKEN", "another-synthetic-token")
    opener = _make_github_opener("2.0.0")
    with patch("agents_switcher.update_check.urllib.request.build_opener", return_value=opener) as factory:
        assert "agents-switcher" in check_for_update("1.0.0")
    assert isinstance(factory.call_args.args[0], _NoRedirect)
    request = opener.open.call_args.args[0]
    assert request.full_url == LATEST_RELEASE_URL
    assert request.get_method() == "GET" and request.data is None
    assert dict(request.header_items()) == {
        "Accept": "application/vnd.github+json", "User-agent": "agents-switcher",
        "X-github-api-version": "2022-11-28",
    }
    opener.open.assert_called_once_with(request, timeout=2)
    opener.open.return_value.read.assert_called_once_with(MAX_RESPONSE_BYTES + 1)


@pytest.mark.parametrize("changes", [
    {"draft": True}, {"prerelease": True}, {"html_url": "https://foreign.example/v2.0.0"},
    {"tag_name": "2.0.0"}, {"tag_name": "v2.0.0-beta.1"}, {"tag_name": "v02.0.0"},
    {"tag_name": "v2.0.0+build"}, {"assets": []}, {"assets": None},
])
def test_untrusted_or_non_cli_releases_do_not_advertise_an_update(tmp_path, monkeypatch, changes):
    cache = tmp_path / "cache.json"
    monkeypatch.setattr("agents_switcher.update_check.CACHE_PATH", cache)
    opener = _make_github_opener("2.0.0")
    response = opener.open.return_value
    data = json.loads(response.read.return_value)
    data.update(changes)
    response.read.return_value = json.dumps(data).encode()
    with patch("agents_switcher.update_check.urllib.request.build_opener", return_value=opener):
        assert check_for_update("1.0.0") is None
    assert json.loads(cache.read_text())["data"] is None


@pytest.mark.parametrize("changes", [
    {"name": "claude_swap-2.0.0-py3-none-any.whl"}, {"state": "new"}, {"size": 0}, {"size": True},
    {"browser_download_url": "https://foreign.example/agents_switcher-2.0.0-py3-none-any.whl"},
])
def test_only_an_uploaded_cli_asset_from_the_same_release_is_eligible(tmp_path, monkeypatch, changes):
    monkeypatch.setattr("agents_switcher.update_check.CACHE_PATH", tmp_path / "cache.json")
    opener = _make_github_opener("2.0.0")
    response = opener.open.return_value
    data = json.loads(response.read.return_value)
    data["assets"][0].update(changes)
    response.read.return_value = json.dumps(data).encode()
    with patch("agents_switcher.update_check.urllib.request.build_opener", return_value=opener):
        assert check_for_update("1.0.0") is None


def test_oversized_release_response_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr("agents_switcher.update_check.CACHE_PATH", tmp_path / "cache.json")
    opener = _make_github_opener("2.0.0")
    opener.open.return_value.read.return_value = b" " * (MAX_RESPONSE_BYTES + 1)
    with patch("agents_switcher.update_check.urllib.request.build_opener", return_value=opener):
        assert check_for_update("1.0.0") is None


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_release_checks_do_not_follow_redirects(code):
    assert _NoRedirect().redirect_request(None, None, code, "redirect", {}, "https://foreign.example") is None


def test_upstream_update_cache_is_not_consumed_or_overwritten(tmp_path, monkeypatch):
    from agents_switcher import update_check

    assert update_check.CACHE_PATH.name == "agents-switcher-update.json"
    own_cache = tmp_path / update_check.CACHE_PATH.name
    upstream_cache = tmp_path / "update_check.json"
    _write_cache(upstream_cache, "999.0.0")
    original = upstream_cache.read_bytes()
    monkeypatch.setattr(update_check, "CACHE_PATH", own_cache)
    with patch("agents_switcher.update_check.urllib.request.build_opener", return_value=_make_github_opener("1.0.0")):
        assert check_for_update("1.0.0") is None
    assert upstream_cache.read_bytes() == original
    assert json.loads(own_cache.read_text())["data"] == "1.0.0"
