"""Tests for the `cswap codex` command surface."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from claude_swap.codex import cli as codex_cli
from claude_swap.codex import switcher as switcher_mod
from claude_swap.codex.auth_file import write_auth
from claude_swap.codex.identity import OPENAI_AUTH_CLAIM
from claude_swap.codex.processes import CodexProcess
from claude_swap.codex.store import CodexAccountStore
from claude_swap.codex.switcher import CodexSwitcher


def auth_for(account_id="acct-1", email="one@example.com", refresh="rt-v1") -> dict:
    claims = {
        "email": email,
        OPENAI_AUTH_CLAIM: {"chatgpt_account_id": account_id, "chatgpt_plan_type": "pro"},
    }
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return {
        "auth_mode": "chatgpt",
        "tokens": {
            "id_token": f"h.{payload}.s",
            "access_token": f"at-{refresh}",
            "refresh_token": refresh,
            "account_id": account_id,
        },
    }


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    home = tmp_path / "codexhome"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setattr(switcher_mod, "running_codex_processes", lambda: [])
    store = CodexAccountStore(root=tmp_path / "backup")
    monkeypatch.setattr(codex_cli, "CodexSwitcher", lambda: CodexSwitcher(store))

    class Env:
        auth_path = home / "auth.json"

        def __init__(self):
            self.store = store
            self.switcher = CodexSwitcher(store)

        def login_as(self, **kw):
            write_auth(auth_for(**kw), self.auth_path)

    return Env()


def run(argv):
    codex_cli.codex_command(argv)


class TestStatus:
    def test_logged_out(self, env, capsys):
        run([])
        assert "not logged in" in capsys.readouterr().out

    def test_unmanaged_login_says_so_and_offers_the_fix(self, env, capsys):
        env.login_as()
        run(["status"])
        out = capsys.readouterr().out
        assert "one@example.com" in out
        assert "not managed" in out
        assert "cswap codex add" in out

    def test_managed_login_shows_its_slot(self, env, capsys):
        env.login_as()
        run(["add"])
        capsys.readouterr()
        run(["status"])
        assert "slot 1" in capsys.readouterr().out


class TestList:
    def test_empty(self, env, capsys):
        run(["list"])
        assert "No Codex accounts" in capsys.readouterr().out

    def test_marks_the_active_account(self, env, capsys):
        env.login_as(account_id="acct-1", email="one@example.com")
        run(["add"])
        env.login_as(account_id="acct-2", email="two@example.com")
        run(["add"])
        capsys.readouterr()
        run(["ls"])
        out = capsys.readouterr().out
        assert "one@example.com" in out and "two@example.com" in out
        assert "* = active" in out


class TestAddAndRemove:
    def test_add_reports_the_slot(self, env, capsys):
        env.login_as()
        run(["add", "--alias", "work"])
        assert "slot 1" in capsys.readouterr().out
        assert env.store.get("1").alias == "work"

    def test_add_without_a_login_exits_nonzero(self, env, capsys):
        with pytest.raises(SystemExit) as exc:
            run(["add"])
        assert exc.value.code == 1

    def test_remove(self, env, capsys):
        env.login_as()
        run(["add"])
        capsys.readouterr()
        run(["rm", "1"])
        assert "Removed" in capsys.readouterr().out
        assert env.store.accounts() == {}


class TestSwitch:
    @pytest.fixture
    def two(self, env, capsys):
        env.login_as(account_id="acct-1", email="one@example.com")
        run(["add"])
        env.login_as(account_id="acct-2", email="two@example.com")
        run(["add"])
        capsys.readouterr()
        return env

    def test_reports_the_switch(self, two, capsys):
        run(["switch", "1"])
        assert "Switched to" in capsys.readouterr().out

    def test_mentions_the_saved_tokens_when_syncing_back(self, two, capsys):
        run(["switch", "1"])
        assert "Saved" in capsys.readouterr().out

    def test_restart_notice_appears_when_codex_is_running(self, two, capsys, monkeypatch):
        """The notice is not decoration: until that process restarts, Codex is
        still authenticating as the PREVIOUS account, so printing a bare
        'Switched' would be false."""
        monkeypatch.setattr(
            switcher_mod,
            "running_codex_processes",
            lambda: [CodexProcess(77, "/bin/codex", "codex", tty="pts/1")],
        )
        run(["switch", "1"])
        out = capsys.readouterr().out
        assert "Restart Codex" in out
        assert "pts/1" in out

    def test_no_restart_notice_when_nothing_is_running(self, two, capsys):
        run(["switch", "1"])
        assert "Restart Codex" not in capsys.readouterr().out

    def test_unmanaged_login_blocks_the_switch(self, two, capsys):
        write_auth(auth_for(account_id="acct-x", email="x@e.com"), two.auth_path)
        with pytest.raises(SystemExit) as exc:
            run(["switch", "1"])
        assert exc.value.code == 1
        assert "not managed" in capsys.readouterr().err

    def test_force_overrides(self, two, capsys):
        write_auth(auth_for(account_id="acct-x", email="x@e.com"), two.auth_path)
        run(["switch", "1", "--force"])
        assert "Switched to" in capsys.readouterr().out


class TestJsonOutput:
    @pytest.mark.parametrize(
        "argv",
        [["status", "--json"], ["--json", "status"]],
        ids=["flag-after", "flag-before"],
    )
    def test_json_works_on_either_side_of_the_subcommand(self, env, capsys, argv):
        # `codex status --json` is what people type; a flag defined only on the
        # top-level parser makes that an "unrecognized arguments" error.
        env.login_as()
        run(argv)
        assert json.loads(capsys.readouterr().out)["loggedIn"] is True

    def test_list_json_shape(self, env, capsys):
        env.login_as()
        run(["add"])
        capsys.readouterr()
        run(["list", "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert payload["activeNumber"] == "1"
        assert payload["accounts"][0]["email"] == "one@example.com"

    def test_errors_are_json_when_asked(self, env, capsys):
        with pytest.raises(SystemExit):
            run(["switch", "99", "--json"])
        assert "error" in json.loads(capsys.readouterr().out)

    def test_switch_json_reports_restart(self, env, capsys, monkeypatch):
        env.login_as(account_id="acct-1")
        run(["add"])
        env.login_as(account_id="acct-2", email="two@e.com")
        run(["add"])
        capsys.readouterr()
        monkeypatch.setattr(
            switcher_mod,
            "running_codex_processes",
            lambda: [CodexProcess(5, "/bin/codex", "codex", tty="?")],
        )
        run(["switch", "1", "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert payload["restartRequired"] is True
        assert payload["processes"][0]["pid"] == 5


class TestAlias:
    def test_set_and_use(self, env, capsys):
        env.login_as()
        run(["add"])
        capsys.readouterr()
        run(["alias", "1", "personal"])
        assert "Set alias" in capsys.readouterr().out
        run(["status"])  # resolvable by the alias now
        assert env.switcher.resolve("personal").number == "1"

    def test_unset(self, env, capsys):
        env.login_as()
        run(["add", "--alias", "work"])
        capsys.readouterr()
        run(["alias", "1", "--unset"])
        assert "Removed alias" in capsys.readouterr().out
        assert env.store.get("1").alias == ""

    def test_alias_without_a_name_is_a_usage_error(self, env):
        with pytest.raises(SystemExit) as exc:
            run(["alias", "1"])
        assert exc.value.code == 2  # argparse usage error
