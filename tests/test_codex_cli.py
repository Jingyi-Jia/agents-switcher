"""Tests for the `cswap codex` command surface."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents_switcher.codex import cli as codex_cli
from agents_switcher.codex import switcher as switcher_mod
from agents_switcher.codex.auth_file import write_auth
from agents_switcher.codex.enrollment import LOGIN_ARGUMENTS
from agents_switcher.codex.identity import OPENAI_AUTH_CLAIM
from agents_switcher.codex.processes import CodexProcess
from agents_switcher.codex.store import CodexAccountStore
from agents_switcher.codex.switcher import CodexSwitcher


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
        assert "codex add" in out

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


class TestIsolatedLogin:
    @pytest.fixture
    def official(self, env, monkeypatch):
        calls = []
        revoked = []
        credentials = auth_for(account_id="acct-2", email="two@example.com", refresh="new-login")
        monkeypatch.setattr(codex_cli.shutil, "which", lambda name: "/installed/codex")

        def child(command, *, env, check):
            assert command == ["/installed/codex", *LOGIN_ARGUMENTS]
            assert check is False
            home = Path(env["CODEX_HOME"])
            old_auth = home / "auth.json"
            if old_auth.exists():
                revoked.append(json.loads(old_auth.read_text())["tokens"]["refresh_token"])
            assert (home / "config.toml").read_text() == 'cli_auth_credentials_store = "file"\n'
            calls.append((command, home))
            write_auth(credentials, old_auth)
            return SimpleNamespace(returncode=0)

        monkeypatch.setattr(codex_cli.subprocess, "run", child)
        return SimpleNamespace(calls=calls, revoked=revoked, credentials=credentials)

    def test_login_saves_a_second_slot_without_revoking_or_activating(self, env, official, capsys):
        env.login_as()
        run(["add", "--alias", "first"])
        live = env.auth_path.read_bytes()
        first = env.store.read_credentials("1")
        capsys.readouterr()
        run(["login", "--alias", "second"])
        output = capsys.readouterr().out
        assert "Saved Codex login" in output
        assert "--use-saved-login" in output
        assert "new-login" not in output
        assert env.auth_path.read_bytes() == live
        assert env.store.active_number() == "1"
        assert env.store.get("2").alias == "second"
        assert env.store.read_credentials("2") == official.credentials
        assert env.store.read_credentials("1") == first
        assert not official.revoked
        assert len(official.calls) == 1
        assert not official.calls[0][1].exists()

    def test_activate_is_explicit(self, env, official, capsys):
        env.login_as()
        run(["add"])
        capsys.readouterr()
        run(["login", "--activate"])
        assert "Switched to" in capsys.readouterr().out
        assert json.loads(env.auth_path.read_text()) == official.credentials
        assert env.store.active_number() == "2"
        assert not official.revoked

    def test_repair_preserves_saved_slot_metadata_and_activates_same_account(self, env, official, capsys):
        env.login_as()
        run(["add", "--alias", "personal"])
        env.switcher.set_account_disabled("1", True)
        official.credentials.update(auth_for(refresh="fresh-repair"))
        capsys.readouterr()
        run(["login", "--account", "1", "--activate"])
        assert "Switched to" in capsys.readouterr().out
        assert len(env.store.accounts()) == 1
        assert env.store.get("1").alias == "personal"
        assert env.store.get("1").disabled is True
        assert json.loads(env.auth_path.read_text()) == official.credentials
        assert not official.revoked

    def test_save_only_repair_can_be_activated_by_a_later_explicit_switch(self, env, official, capsys):
        env.login_as()
        run(["add"])
        official.credentials.update(auth_for(refresh="fresh-repair"))
        run(["login", "--account", "1"])
        capsys.readouterr()
        with pytest.raises(SystemExit) as error:
            run(["switch", "1"])
        assert error.value.code == 1
        run(["switch", "1", "--use-saved-login"])
        assert json.loads(env.auth_path.read_text()) == official.credentials

    def test_repeated_explicit_switch_keeps_the_native_rotation(self, env, official, capsys, monkeypatch):
        env.login_as()
        run(["add"])
        official.credentials.update(auth_for(refresh="fresh-repair"))
        run(["login", "--account", "1", "--activate"])
        assert env.switcher._pending_imports() == {}
        env.login_as(refresh="rotated-after-activation")
        rotated = json.loads(env.auth_path.read_text())

        def forbidden(tokens):
            pytest.fail("Repeated activation must not refresh provider tokens")

        monkeypatch.setattr(switcher_mod, "refresh_tokens", forbidden)
        run(["switch", "1", "--use-saved-login"])
        assert json.loads(env.auth_path.read_text()) == rotated
        assert env.store.read_credentials("1") == rotated

    @pytest.mark.parametrize("argv", [["login", "--json"], ["--json", "login"]])
    def test_json_is_rejected_before_any_spawn(self, env, official, argv, capsys):
        with pytest.raises(SystemExit) as error:
            run(argv)
        assert error.value.code == 2
        assert "interactive" in capsys.readouterr().err
        assert not official.calls

    def test_repair_alias_is_not_accepted(self, env, official, capsys):
        with pytest.raises(SystemExit) as error:
            run(["login", "--account", "1", "--alias", "replacement"])
        assert error.value.code == 2
        assert not official.calls

    @pytest.mark.parametrize("outcome", ["failure", "cancel", "launch-error"])
    def test_failed_login_cleans_only_its_home_and_preserves_accounts(self, env, official, capsys, monkeypatch, outcome):
        env.login_as()
        run(["add"])
        live = env.auth_path.read_bytes()
        saved = env.store.read_credentials("1")
        homes = []

        def child(command, *, env, check):
            home = Path(env["CODEX_HOME"])
            homes.append(home)
            write_auth(official.credentials, home / "auth.json")
            if outcome == "cancel":
                raise KeyboardInterrupt
            if outcome == "launch-error":
                raise OSError("secret-subprocess-payload")
            return SimpleNamespace(returncode=1)

        monkeypatch.setattr(codex_cli.subprocess, "run", child)
        capsys.readouterr()
        with pytest.raises(SystemExit) as error:
            run(["login"])
        assert error.value.code == (130 if outcome == "cancel" else 1)
        output = capsys.readouterr()
        assert "secret-subprocess-payload" not in output.out + output.err
        assert env.auth_path.read_bytes() == live
        assert env.store.read_credentials("1") == saved
        assert len(env.store.accounts()) == 1
        assert env.store.active_number() == "1"
        assert len(homes) == 1 and not homes[0].exists()

    def test_running_clients_block_activation_but_keep_the_saved_login(self, env, official, capsys, monkeypatch):
        env.login_as()
        run(["add"])
        live = env.auth_path.read_bytes()
        monkeypatch.setattr(switcher_mod, "running_codex_processes", lambda: [object()])
        capsys.readouterr()
        with pytest.raises(SystemExit) as error:
            run(["login", "--activate"])
        assert error.value.code == 1
        output = capsys.readouterr()
        assert "Saved Codex login" in output.out
        assert "Quit Codex" in output.err
        assert env.auth_path.read_bytes() == live
        assert env.store.read_credentials("2") == official.credentials
        assert env.store.active_number() == "1"

    def test_missing_cli_never_prepares_or_spawns(self, env, official, capsys, monkeypatch):
        monkeypatch.setattr(codex_cli.shutil, "which", lambda name: None)
        with pytest.raises(SystemExit) as error:
            run(["login"])
        assert error.value.code == 1
        assert "official Codex CLI" in capsys.readouterr().err
        assert not official.calls
