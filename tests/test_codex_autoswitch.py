"""Tests for the Codex auto-switch policy.

`decide` is pure, so every rule is exercised directly rather than through the
network and a process table.
"""

from __future__ import annotations

import pytest

from claude_swap.codex.autoswitch import (
    AccountState,
    Action,
    AutoSettings,
    decide,
)
from claude_swap.codex.processes import CodexProcess
from claude_swap.codex.store import CodexAccount
from claude_swap.codex.usage import CodexCredits, CodexUsage, CodexWindow

SETTINGS = AutoSettings(threshold=80.0, hysteresis_pct=10.0, cooldown_seconds=600.0)
RUNNING = (CodexProcess(1, "/bin/codex", "codex", tty="pts/0"),)


def account(number: str, disabled: bool = False) -> CodexAccount:
    return CodexAccount(
        number=number, email=f"a{number}@example.com",
        account_id=f"acct-{number}", disabled=disabled,
    )


def healthy(number: str, used: int, disabled: bool = False) -> AccountState:
    return AccountState(
        account=account(number, disabled),
        usage=CodexUsage(allowed=True, windows=(CodexWindow(used, 604800),)),
    )


def on_credits(number: str, reset_at: int | None = None) -> AccountState:
    """Included quota spent, still working by billing credits per request."""
    return AccountState(
        account=account(number),
        usage=CodexUsage(
            allowed=False,
            limit_reached=True,
            credits=CodexCredits(has_credits=True, balance="100"),
            windows=(CodexWindow(100, 604800, reset_at=reset_at),),
        ),
    )


def dead(number: str, reset_at: int | None = None) -> AccountState:
    return AccountState(
        account=account(number),
        usage=CodexUsage(
            allowed=False, limit_reached=True,
            credits=CodexCredits(has_credits=False),
            windows=(CodexWindow(100, 604800, reset_at=reset_at),),
        ),
    )


def unreadable(number: str) -> AccountState:
    return AccountState(account=account(number), error="network unreachable")


class TestHolding:
    def test_no_accounts(self):
        assert decide([], active_number=None, settings=SETTINGS).action is Action.NO_ACCOUNTS

    def test_a_healthy_active_account_is_left_alone(self):
        d = decide([healthy("1", 20), healthy("2", 5)],
                   active_number="1", settings=SETTINGS)
        assert d.action is Action.HOLD

    def test_below_the_threshold_is_not_switched_even_with_a_better_option(self):
        d = decide([healthy("1", 79), healthy("2", 0)],
                   active_number="1", settings=SETTINGS)
        assert d.action is Action.HOLD


class TestSwitching:
    def test_switches_when_the_active_account_crosses_the_threshold(self):
        d = decide([healthy("1", 85), healthy("2", 10)],
                   active_number="1", settings=SETTINGS)
        assert d.action is Action.SWITCH
        assert d.target.account.number == "2"

    def test_picks_the_account_with_the_most_headroom(self):
        d = decide([healthy("1", 90), healthy("2", 60), healthy("3", 5)],
                   active_number="1", settings=SETTINGS)
        assert d.target.account.number == "3"

    def test_switches_off_a_dead_account(self):
        d = decide([dead("1"), healthy("2", 10)], active_number="1", settings=SETTINGS)
        assert d.action is Action.SWITCH

    def test_disabled_accounts_are_never_targets(self):
        d = decide([healthy("1", 90), healthy("2", 0, disabled=True)],
                   active_number="1", settings=SETTINGS)
        assert d.action is Action.ALL_EXHAUSTED

    def test_an_unreadable_account_is_never_a_target(self):
        # Landing on an unknown state could mean an exhausted account and a
        # second restart, and the loop cannot tell.
        d = decide([healthy("1", 90), unreadable("2")],
                   active_number="1", settings=SETTINGS)
        assert d.action is Action.ALL_EXHAUSTED


class TestPaidCreditsAreNotCapacity:
    """The rule the user set: paying per request is their call, never a
    background loop's."""

    def test_never_switches_onto_an_account_running_on_credits(self):
        d = decide([healthy("1", 95), on_credits("2")],
                   active_number="1", settings=SETTINGS)
        assert d.action is not Action.SWITCH
        assert d.action is Action.ALL_EXHAUSTED

    def test_a_credits_account_does_not_count_as_somewhere_to_go(self):
        # The second half of the rule: if it counted as capacity the loop would
        # report a healthy state it cannot act on.
        d = decide([dead("1"), on_credits("2"), on_credits("3")],
                   active_number="1", settings=SETTINGS)
        assert d.action is Action.ALL_EXHAUSTED
        assert d.target is None

    def test_moves_off_an_account_that_has_started_billing_credits(self):
        # It still works, but every request now costs money.
        d = decide([on_credits("1"), healthy("2", 10)],
                   active_number="1", settings=SETTINGS)
        assert d.action is Action.SWITCH
        assert d.target.account.number == "2"

    def test_a_healthy_account_holding_credits_is_still_a_target(self):
        # Having credits is not the same as running on them.
        state = AccountState(
            account=account("2"),
            usage=CodexUsage(allowed=True, credits=CodexCredits(has_credits=True),
                             windows=(CodexWindow(5, 604800),)),
        )
        d = decide([healthy("1", 95), state], active_number="1", settings=SETTINGS)
        assert d.action is Action.SWITCH
        assert d.target.account.number == "2"


class TestRunningCodexBlocksTheSwitch:
    def test_notifies_instead_of_switching(self):
        """A swap is invisible to a live process, so 'switched' would be false
        and switching anyway would mean killing the session to make it true."""
        d = decide([healthy("1", 95), healthy("2", 5)],
                   active_number="1", settings=SETTINGS, processes=RUNNING)
        assert d.action is Action.NOTIFY
        assert d.target.account.number == "2"
        assert d.processes == RUNNING

    def test_a_held_account_is_not_reported_as_needing_a_restart(self):
        d = decide([healthy("1", 10)], active_number="1",
                   settings=SETTINGS, processes=RUNNING)
        assert d.action is Action.HOLD


class TestHysteresis:
    def test_a_marginal_improvement_is_not_worth_a_restart(self):
        # 85% used -> 15 headroom; 78% used -> 22 headroom; margin is 10.
        d = decide([healthy("1", 85), healthy("2", 78)],
                   active_number="1", settings=SETTINGS)
        assert d.action is Action.HOLD
        assert "margin" in d.reason

    def test_a_clear_improvement_is_taken(self):
        d = decide([healthy("1", 85), healthy("2", 50)],
                   active_number="1", settings=SETTINGS)
        assert d.action is Action.SWITCH

    def test_the_margin_does_not_protect_an_unusable_account(self):
        # A dead account has nothing to defend; holding on it to honour a
        # margin would be the worst possible outcome.
        d = decide([dead("1"), healthy("2", 99)], active_number="1", settings=SETTINGS)
        assert d.action is Action.SWITCH

    def test_the_margin_does_not_protect_a_credits_account(self):
        d = decide([on_credits("1"), healthy("2", 99)],
                   active_number="1", settings=SETTINGS)
        assert d.action is Action.SWITCH


class TestCooldown:
    def test_a_recent_switch_blocks_another(self):
        d = decide([healthy("1", 95), healthy("2", 5)], active_number="1",
                   settings=SETTINGS, now=1000.0, last_switch_at=800.0)
        assert d.action is Action.COOLDOWN

    def test_an_elapsed_cooldown_allows_a_switch(self):
        d = decide([healthy("1", 95), healthy("2", 5)], active_number="1",
                   settings=SETTINGS, now=2000.0, last_switch_at=800.0)
        assert d.action is Action.SWITCH

    def test_no_prior_switch_is_not_a_cooldown(self):
        d = decide([healthy("1", 95), healthy("2", 5)], active_number="1",
                   settings=SETTINGS, now=1000.0, last_switch_at=None)
        assert d.action is Action.SWITCH


class TestExhaustion:
    def test_reports_the_soonest_reset(self):
        d = decide([dead("1", reset_at=900), dead("2", reset_at=500)],
                   active_number="1", settings=SETTINGS)
        assert d.action is Action.ALL_EXHAUSTED
        assert d.soonest_reset_at == 500

    def test_nothing_readable_is_distinguished_from_nothing_left(self):
        # A wait versus a problem to investigate.
        d = decide([unreadable("1"), unreadable("2")],
                   active_number="1", settings=SETTINGS)
        assert d.action is Action.NO_TARGET
        assert "could be read" in d.reason


class TestActiveAccountEdgeCases:
    def test_an_unreadable_active_account_is_replaced(self):
        d = decide([unreadable("1"), healthy("2", 10)],
                   active_number="1", settings=SETTINGS)
        assert d.action is Action.SWITCH

    def test_no_active_account_still_picks_one(self):
        d = decide([healthy("1", 10), healthy("2", 50)],
                   active_number=None, settings=SETTINGS)
        assert d.action is Action.SWITCH
        assert d.target.account.number == "1"

    def test_an_active_number_that_is_not_managed_is_treated_as_absent(self):
        d = decide([healthy("1", 10)], active_number="9", settings=SETTINGS)
        assert d.action is Action.SWITCH
