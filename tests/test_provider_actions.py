"""Shared actions exercise real provider policies without using real credentials."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from claude_swap.codex.autoswitch import Action, AutoDecision
from claude_swap.codex.identity import CodexIdentity
from claude_swap.codex.store import CodexAccount, CodexAccountStore
from claude_swap.codex.switcher import CodexStatus, CodexSwitcher, SwitchResult
from claude_swap.codex.usage import CodexCredits, CodexUsage, CodexWindow
from claude_swap.providers import MAX_EVENTS, ProviderActionError, ProviderActions
from claude_swap.settings import SETTING_SPECS


def quota(percent, *, credits=False):
    return CodexUsage(
        allowed=not credits, limit_reached=credits,
        windows=(CodexWindow(percent, 18000),),
        credits=CodexCredits(has_credits=True) if credits else None,
    )


class Codex:
    def __init__(self, root):
        self.accounts = [
            CodexAccount(number=str(n), email=f"{n}@example.com", account_id=f"acct-{n}")
            for n in (1, 2, 3)
        ]
        self.active = "1"
        self.store = SimpleNamespace(root=root, active_number=lambda: self.active)
        self.usage = {"1": quota(95), "2": quota(20), "3": quota(30)}
        self.calls = []

    def list_accounts(self):
        return list(self.accounts)

    def status(self):
        account = next((a for a in self.accounts if a.number == self.active), None)
        return CodexStatus(
            bool(account), account.identity if account else None,
            account, account.number if account else None,
        )

    def usage_all(self):
        return self.usage

    def switch_to(self, number):
        self.calls.append(("switch", number))
        self.active = number
        return SwitchResult(self.accounts[int(number) - 1], None, False)

    def add_current(self, *, refresh_existing=False):
        assert refresh_existing
        self.calls.append(("add",))
        return self.accounts[0]

    def set_account_disabled(self, number, disabled):
        self.calls.append(("disabled", number, disabled))

    def remove_account(self, number):
        self.calls.append(("remove", number))


class Claude:
    def __init__(self):
        self.calls = []
        self.accounts = []
        self.result = {"switched": True, "reason": "switched", "message": "Switched account"}

    def switch_to(self, number, **kwargs):
        self.calls.append(("switch", number, kwargs))
        return self.result

    def switch(self, **kwargs):
        self.calls.append(("best", kwargs))
        return self.result

    def add_account(self, **kwargs):
        self.calls.append(("add", kwargs))

    def accounts_snapshot(self, **kwargs):
        return SimpleNamespace(accounts=self.accounts, active_number=None)

    def add_account_from_token(self, token, **kwargs):
        self.calls.append(("token", token, kwargs))

    def set_account_disabled(self, number, disabled):
        self.calls.append(("disabled", number, disabled))

    def remove_account(self, number, **kwargs):
        self.calls.append(("remove", number, kwargs))


@pytest.fixture
def actions(tmp_path, monkeypatch):
    from claude_swap.codex import autoswitch

    monkeypatch.setattr(autoswitch, "running_codex_processes", list)
    result = ProviderActions(claude=Claude(), codex=Codex(tmp_path / "codex"))
    yield result
    result.close()


def watch_event(controller, monkeypatch, kind):
    ready = threading.Event()
    original = controller._event

    def record(provider, message, event_kind, at=None):
        original(provider, message, event_kind, at)
        if event_kind == kind:
            ready.set()

    monkeypatch.setattr(controller, "_event", record)
    return ready


def test_explicit_registry_and_capabilities(actions):
    assert actions.capabilities("claude") == ["switch", "add", "remove", "disable", "switch-best", "auto", "token"]
    assert actions.capabilities("codex") == ["switch", "add", "remove", "disable", "switch-best", "auto"]
    assert "separate sign-in" in actions.switch_notice("claude")
    assert "Code tab" in actions.switch_notice("claude")
    with pytest.raises(ProviderActionError, match="unknown provider"):
        actions.switch("other", "1")
    with pytest.raises(ProviderActionError, match="does not support token"):
        actions.add_token("codex", "test-token")


@pytest.mark.parametrize("reason,switched,ok", [
    ("switched", True, True), ("already-active", False, True),
    ("no-viable-target", False, False), ("blocked", False, False),
])
def test_claude_uses_structured_switch_outcome(actions, reason, switched, ok):
    claude = actions._switchers["claude"]
    claude.result = {"switched": switched, "reason": reason, "message": "provider result"}
    result = actions.switch("claude", 2)
    assert result["ok"] is ok
    assert result["switched"] is switched
    assert result["message"] == "provider result"
    assert claude.calls == [("switch", "2", {"json_output": True})]


def test_claude_missing_switch_result_is_not_success(actions):
    actions._switchers["claude"].result = None
    with pytest.raises(ProviderActionError, match="outcome"):
        actions.switch("claude", "1")


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_shared_actions_preserve_provider_safety_flags(actions, provider):
    actions.add_current(provider)
    actions.set_disabled(provider, 1, True)
    actions.set_disabled(provider, 1, False)
    with pytest.raises(ProviderActionError, match="confirm"):
        actions.remove(provider, 1)
    actions.remove(provider, 1, confirm=True)
    calls = actions._switchers[provider].calls
    assert ("disabled", "1", True) in calls
    assert ("disabled", "1", False) in calls
    if provider == "claude":
        assert ("add", {"assume_yes": True}) in calls
        assert ("remove", "1", {"assume_yes": True}) in calls
    else:
        assert ("remove", "1") in calls


def test_best_uses_claude_strategy(actions):
    actions.switch_best("claude")
    assert actions._switchers["claude"].calls == [("best", {"strategy": "best", "json_output": True})]


def test_codex_best_excludes_disabled_and_paid_credits(actions):
    codex = actions._switchers["codex"]
    codex.accounts[1] = replace(codex.accounts[1], disabled=True)
    codex.usage["3"] = quota(100, credits=True)
    result = actions.switch_best("codex")
    assert not result["switched"]
    assert codex.calls == []
    codex.accounts[1] = replace(codex.accounts[1], disabled=False)
    assert actions.switch_best("codex")["switched"]
    assert codex.calls == [("switch", "2")]


def test_codex_disable_updates_only_roster(tmp_path):
    store = CodexAccountStore(tmp_path / "codex")
    with store.lock():
        account = store.add(CodexIdentity("one@example.com", "acct-1", "pro"), {"tokens": {"test": "unchanged"}})
        store.set_active(account.number)
    before = store.read_credentials(account.number)
    switcher = CodexSwitcher(store)
    assert switcher.set_account_disabled("1", True).disabled
    assert store.accounts()["1"].disabled
    assert store.active_number() == "1"
    assert store.read_credentials("1") == before
    assert not switcher.set_account_disabled("1", False).disabled


def test_codex_add_current_refreshes_live_rotation_without_changing_slot_metadata(tmp_path, monkeypatch):
    from claude_swap.codex import switcher as switcher_module
    from tests.test_codex_switcher import auth_for, id_token

    store = CodexAccountStore(tmp_path / "codex")
    old = auth_for(email="old@example.com", refresh="old")
    with store.lock():
        original = store.add(CodexIdentity("old@example.com", "acct-1", "pro"), old, alias="work")
        original = replace(original, disabled=True)
        store.update(original)
    live = auth_for(email="new@example.com", refresh="rotated")
    live["tokens"]["id_token"] = id_token("acct-1", "new@example.com", "team")
    monkeypatch.setattr(switcher_module, "read_auth", lambda: live)
    actions = ProviderActions(codex=CodexSwitcher(store))
    try:
        assert actions.add_current("codex")["ok"]
        saved = store.accounts()[original.number]
        assert saved.email == "new@example.com"
        assert saved.plan == "team"
        assert saved.alias == original.alias
        assert saved.disabled == original.disabled
        assert saved.added == original.added
        assert len(store.accounts()) == 1
        assert store.read_credentials(original.number) == live
        assert store.active_number() == original.number
    finally:
        actions.close()


@pytest.mark.parametrize("slot,email", [(1, "other@example.com"), (None, "one@example.com"), (2, "one@example.com")])
def test_token_replacement_and_migration_require_confirmation(actions, slot, email):
    claude = actions._switchers["claude"]
    claude.accounts = [SimpleNamespace(number="1", email="one@example.com")]
    with pytest.raises(ProviderActionError, match="confirm"):
        actions.add_token("claude", "test-token", email, slot)
    assert claude.calls == []
    actions.add_token("claude", "test-token", email, slot, confirm=True)
    assert claude.calls[0] == ("token", "test-token", {"email": email, "slot": slot, "assume_yes": True})


@pytest.mark.parametrize("token", ["", " ", "-", " - ", "has whitespace", "bad\x00token", "x" * 16385])
def test_token_rejects_interactive_and_unbounded_inputs(actions, token):
    with pytest.raises(ProviderActionError):
        actions.add_token("claude", token)
    assert actions._switchers["claude"].calls == []


def test_token_error_never_echoes_submitted_value(actions):
    token = "private-test-sentinel"
    actions._switchers["claude"].add_account_from_token = Mock(side_effect=RuntimeError(token))
    with pytest.raises(ProviderActionError) as caught:
        actions.add_token("claude", token)
    assert token not in str(caught.value)


@pytest.mark.parametrize("threshold", [True, False, 49.9, 100, "90", float("nan"), float("inf")])
def test_auto_threshold_validation_uses_settings_range(actions, threshold):
    with pytest.raises(ProviderActionError, match="threshold"):
        actions.auto.configure("codex", "dry-run", threshold=threshold)


def test_auto_confirmation_is_strict(actions):
    for confirm in (False, 1, "true", None):
        with pytest.raises(ProviderActionError, match="confirm"):
            actions.auto.configure("codex", "live", confirm=confirm)
    assert actions.auto.status("codex")["mode"] == "stopped"


def test_codex_dry_run_uses_real_policy_without_switch_or_cooldown(actions, monkeypatch):
    ready = watch_event(actions.auto, monkeypatch, "switch")
    actions.auto.configure("codex", "dry-run", threshold=85)
    assert ready.wait(2)
    actions.auto.configure("codex", "stopped")
    codex = actions._switchers["codex"]
    assert codex.calls == []
    assert not (codex.store.root / "autoswitch_state.json").exists()
    assert actions.auto.status("codex")["threshold"] == 85
    assert actions.auto.status("claude")["threshold"] == 90


def test_codex_live_preserves_running_process_guard(actions, monkeypatch):
    from claude_swap.codex import autoswitch

    monkeypatch.setattr(autoswitch, "running_codex_processes", lambda: [object()])
    ready = watch_event(actions.auto, monkeypatch, "notify")
    actions.auto.configure("codex", "live", confirm=True)
    assert ready.wait(2)
    actions.auto.configure("codex", "stopped")
    assert actions._switchers["codex"].calls == []


def test_codex_live_switches_and_persists_existing_cooldown(actions, monkeypatch):
    ready = watch_event(actions.auto, monkeypatch, "switch")
    actions.auto.configure("codex", "live", confirm=True)
    assert ready.wait(2)
    actions.auto.configure("codex", "stopped")
    codex = actions._switchers["codex"]
    assert codex.calls == [("switch", "2")]
    assert json.loads((codex.store.root / "autoswitch_state.json").read_text())["lastSwitchAt"] > 0


def test_real_claude_engine_dry_run_and_adaptive_delay(temp_home, monkeypatch):
    from claude_swap.autoswitch import AutoSwitchEngine
    from tests.test_autoswitch import EngineHarness, _entry_for, _usage

    harness = EngineHarness(temp_home)
    harness.seed(1, "one@example.com")
    harness.seed(2, "two@example.com")
    harness.make_live("one@example.com", 1)
    entries = {"1": _entry_for(_usage(95), time.time()), "2": _entry_for(_usage(20), time.time())}
    monkeypatch.setattr(harness.switcher, "usage_entries_by_account", lambda **kwargs: entries)
    switch = Mock(side_effect=AssertionError("dry-run must not switch"))
    monkeypatch.setattr(harness.switcher, "switch_to", switch)
    adaptive = Mock(wraps=AutoSwitchEngine._next_delay)
    monkeypatch.setattr(AutoSwitchEngine, "_next_delay", lambda self, outcome: adaptive(self, outcome))
    actions = ProviderActions(claude=harness.switcher)
    try:
        ready = watch_event(actions.auto, monkeypatch, "switch")
        actions.auto.configure("claude", "dry-run", threshold=85)
        assert ready.wait(2)
        actions.auto.configure("claude", "stopped")
        switch.assert_not_called()
        adaptive.assert_called_once()
        assert not (harness.switcher.backup_dir / "autoswitch_state.json").exists()
        assert harness.switcher._poll_inputs_override is None
        assert any("[dry-run] would switch" in event["message"] for event in actions.auto.status("claude")["events"])
    finally:
        actions.close()


def test_stop_joins_inflight_tick_before_returning(actions, monkeypatch):
    from claude_swap.codex import autoswitch

    entered, release, stopped = threading.Event(), threading.Event(), threading.Event()

    def blocking(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return AutoDecision(Action.HOLD, "holding")

    monkeypatch.setattr(autoswitch, "run_once", blocking)
    actions.auto.configure("codex", "live", confirm=True)
    assert entered.wait(2)
    worker = actions.auto._workers["codex"][0]
    stopper = threading.Thread(target=lambda: (actions.auto.configure("codex", "stopped"), stopped.set()))
    stopper.start()
    try:
        assert not stopped.wait(0.05)
    finally:
        release.set()
        stopper.join(3)
    assert stopped.is_set()
    assert not worker.is_alive()


def test_auto_events_bounded_and_snapshot_is_independent(actions):
    for i in range(MAX_EVENTS + 20):
        actions.auto._event("codex", str(i), "test")
    state = actions.auto.status("codex")
    assert len(state["events"]) == MAX_EVENTS
    assert state["events"][0]["message"] == "20"
    state["events"][0]["message"] = "changed"
    assert actions.auto.status("codex")["events"][0]["message"] == "20"
    assert "browser tab does not stop" in state["sessionNotice"]
    assert "independently started CLI" in state["sessionNotice"]


def test_threshold_is_session_only_and_closed_actions_refuse(tmp_path):
    first = ProviderActions(codex=Codex(tmp_path / "codex"))
    threshold = SETTING_SPECS["autoswitch.threshold"].hi
    first.auto.configure("codex", "stopped", threshold=threshold)
    first.close()
    second = ProviderActions(codex=Codex(tmp_path / "codex"))
    try:
        assert second.auto.status("codex")["threshold"] == 90
        with pytest.raises(ProviderActionError, match="closed"):
            first.switch("codex", 2)
        with pytest.raises(ProviderActionError, match="closed"):
            first.auto.configure("codex", "dry-run")
        assert not (tmp_path / "settings.json").exists()
    finally:
        second.close()
