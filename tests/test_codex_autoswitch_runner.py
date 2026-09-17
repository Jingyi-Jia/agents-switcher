"""Tests for applying an auto-switch decision: cooldown state, dry run, CLI."""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import pytest

from claude_swap.codex import autoswitch as auto_mod
from claude_swap.codex import cli as codex_cli
from claude_swap.codex import switcher as switcher_mod
from claude_swap.codex.auth_file import read_auth, write_auth
from claude_swap.codex.autoswitch import (
    Action,
    AutoSettings,
    collect_states,
    read_last_switch_at,
    run_once,
    write_last_switch_at,
)
from claude_swap.codex.identity import OPENAI_AUTH_CLAIM
from claude_swap.codex.processes import CodexProcess
from claude_swap.codex.store import CodexAccountStore
from claude_swap.codex.switcher import CodexSwitcher
from claude_swap.codex.usage import CodexCredits, CodexUsage, CodexWindow, UsageError


def auth_for(account_id, email):
    claims = {"email": email,
              OPENAI_AUTH_CLAIM: {"chatgpt_account_id": account_id,
                                  "chatgpt_plan_type": "pro"}}
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return {"auth_mode": "chatgpt", "tokens": {
        "id_token": f"h.{payload}.s",
        "access_token": base64.urlsafe_b64encode(b"x").decode(),
        "refresh_token": f"rt-{account_id}", "account_id": account_id}}


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    home = tmp_path / "codexhome"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setattr(switcher_mod, "running_codex_processes", lambda: [])
    monkeypatch.setattr(auto_mod, "running_codex_processes", lambda: [], raising=False)

    store = CodexAccountStore(root=tmp_path / "backup")
    switcher = CodexSwitcher(store)
    monkeypatch.setattr(codex_cli, "CodexSwitcher", lambda: switcher)

    class Env:
        auth_path = home / "auth.json"

        def __init__(self):
            self.store, self.switcher = store, switcher
            self.usage: dict[str, object] = {}

        def add(self, account_id, email):
            write_auth(auth_for(account_id, email), self.auth_path)
            return self.switcher.add_current()

        def activate(self, number, account_id, email):
            """Make a slot active in BOTH places.

            The roster's active number and the live auth.json are separate
            facts; setting only the first leaves Codex logged in as someone
            else, and the switcher correctly refuses on the mismatch.
            """
            write_auth(auth_for(account_id, email), self.auth_path)
            self.store.set_active(number)

        def set_usage(self, mapping, mp):
            self.usage = mapping
            mp.setattr(CodexSwitcher, "usage_all", lambda self: dict(mapping))

        def no_processes(self, mp):
            mp.setattr(auto_mod, "running_codex_processes", lambda: [])

        def processes(self, mp, procs):
            mp.setattr(auto_mod, "running_codex_processes", lambda: procs)

    return Env()


def healthy(used):
    return CodexUsage(allowed=True, windows=(CodexWindow(used, 604800),))


def credits_only():
    return CodexUsage(allowed=False, limit_reached=True,
                      credits=CodexCredits(has_credits=True),
                      windows=(CodexWindow(100, 604800, reset_at=5000),))


class TestCollectStates:
    def test_failures_become_states_not_exceptions(self, env, monkeypatch):
        env.add("acct-1", "one@e.com")
        env.add("acct-2", "two@e.com")
        env.set_usage({"1": UsageError("down"), "2": healthy(10)}, monkeypatch)

        states = {s.account.number: s for s in collect_states(env.switcher)}
        assert states["1"].error == "down" and states["1"].eligible is False
        assert states["2"].eligible is True


class TestCooldownState:
    def test_round_trips(self, env):
        assert read_last_switch_at(env.switcher) is None
        write_last_switch_at(env.switcher, 1234.5)
        assert read_last_switch_at(env.switcher) == 1234.5

    def test_a_corrupt_state_file_is_ignored(self, env):
        env.store.root.mkdir(parents=True, exist_ok=True)
        (env.store.root / auto_mod.STATE_FILENAME).write_text("{not json")
        # A cooldown that cannot be read must not block switching forever.
        assert read_last_switch_at(env.switcher) is None


class TestRunOnce:
    def test_switches_and_records_the_time(self, env, monkeypatch):
        env.add("acct-1", "one@e.com")
        env.add("acct-2", "two@e.com")
        env.activate("1", "acct-1", "one@e.com")
        env.set_usage({"1": healthy(95), "2": healthy(5)}, monkeypatch)
        env.no_processes(monkeypatch)

        decision = run_once(env.switcher, settings=AutoSettings(), now=1000.0)
        assert decision.action is Action.SWITCH
        assert env.store.active_number() == "2"
        assert read_last_switch_at(env.switcher) == 1000.0
        assert read_auth(env.auth_path)["tokens"]["account_id"] == "acct-2"

    def test_dry_run_decides_but_does_not_act(self, env, monkeypatch):
        env.add("acct-1", "one@e.com")
        env.add("acct-2", "two@e.com")
        env.activate("1", "acct-1", "one@e.com")
        env.set_usage({"1": healthy(95), "2": healthy(5)}, monkeypatch)
        env.no_processes(monkeypatch)

        decision = run_once(env.switcher, settings=AutoSettings(), now=1000.0,
                            dry_run=True)
        assert decision.action is Action.SWITCH   # the decision is real
        assert env.store.active_number() == "1"   # the effect is not
        assert read_last_switch_at(env.switcher) is None

    def test_a_running_codex_prevents_the_switch(self, env, monkeypatch):
        env.add("acct-1", "one@e.com")
        env.add("acct-2", "two@e.com")
        env.activate("1", "acct-1", "one@e.com")
        env.set_usage({"1": healthy(95), "2": healthy(5)}, monkeypatch)
        env.processes(monkeypatch, [CodexProcess(7, "/bin/codex", "codex", tty="pts/1")])

        decision = run_once(env.switcher, settings=AutoSettings(), now=1000.0)
        assert decision.action is Action.NOTIFY
        assert env.store.active_number() == "1"

    def test_never_switches_onto_a_credits_account(self, env, monkeypatch):
        env.add("acct-1", "one@e.com")
        env.add("acct-2", "two@e.com")
        env.activate("1", "acct-1", "one@e.com")
        env.set_usage({"1": healthy(95), "2": credits_only()}, monkeypatch)
        env.no_processes(monkeypatch)

        decision = run_once(env.switcher, settings=AutoSettings(), now=1000.0)
        assert decision.action is Action.ALL_EXHAUSTED
        assert env.store.active_number() == "1"


class TestAutoCli:
    def _args(self, **kw):
        base = dict(once=True, dry_run=False, threshold=80.0, hysteresis=10.0,
                    cooldown=600.0, interval=300.0, json=False)
        base.update(kw)
        return type("Args", (), base)()

    def test_exit_code_zero_when_it_switches(self, env, monkeypatch, capsys):
        env.add("acct-1", "one@e.com")
        env.add("acct-2", "two@e.com")
        env.activate("1", "acct-1", "one@e.com")
        env.set_usage({"1": healthy(95), "2": healthy(5)}, monkeypatch)
        env.no_processes(monkeypatch)

        with pytest.raises(SystemExit) as exc:
            codex_cli._auto_command(env.switcher, self._args())
        assert exc.value.code == 0
        assert "Switched" in capsys.readouterr().out

    def test_exit_code_two_when_nothing_to_do(self, env, monkeypatch, capsys):
        env.add("acct-1", "one@e.com")
        env.activate("1", "acct-1", "one@e.com")
        env.set_usage({"1": healthy(10)}, monkeypatch)
        env.no_processes(monkeypatch)

        with pytest.raises(SystemExit) as exc:
            codex_cli._auto_command(env.switcher, self._args())
        assert exc.value.code == 2

    def test_exit_code_three_when_blocked_by_a_running_codex(self, env, monkeypatch, capsys):
        env.add("acct-1", "one@e.com")
        env.add("acct-2", "two@e.com")
        env.activate("1", "acct-1", "one@e.com")
        env.set_usage({"1": healthy(95), "2": healthy(5)}, monkeypatch)
        env.processes(monkeypatch, [CodexProcess(7, "/bin/codex", "codex", tty="pts/1")])

        with pytest.raises(SystemExit) as exc:
            codex_cli._auto_command(env.switcher, self._args())
        assert exc.value.code == 3
        out = capsys.readouterr().out
        assert "Codex is running" in out
        assert "codex switch 2" in out  # tells the user how to finish it

    def test_dry_run_is_labelled(self, env, monkeypatch, capsys):
        env.add("acct-1", "one@e.com")
        env.add("acct-2", "two@e.com")
        env.activate("1", "acct-1", "one@e.com")
        env.set_usage({"1": healthy(95), "2": healthy(5)}, monkeypatch)
        env.no_processes(monkeypatch)

        with pytest.raises(SystemExit):
            codex_cli._auto_command(env.switcher, self._args(dry_run=True))
        assert "Would switch" in capsys.readouterr().out

    def test_json_output(self, env, monkeypatch, capsys):
        env.add("acct-1", "one@e.com")
        env.add("acct-2", "two@e.com")
        env.activate("1", "acct-1", "one@e.com")
        env.set_usage({"1": healthy(95), "2": healthy(5)}, monkeypatch)
        env.no_processes(monkeypatch)

        with pytest.raises(SystemExit):
            codex_cli._auto_command(env.switcher, self._args(json=True))
        payload = json.loads(capsys.readouterr().out)
        assert payload["action"] == "switch" and payload["switched"] is True
