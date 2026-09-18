"""Automatic Codex account switching.

The decision is a pure function (:func:`decide`) over a snapshot, and the loop
around it only gathers inputs and applies the outcome. That split exists because
the policy is where the subtle rules live, and a policy that can only be tested
by standing up network calls and a process table does not get tested properly.

THREE RULES SHAPE IT, AND NONE IS OBVIOUS FROM "SWITCH WHEN FULL":

1. A PAID-CREDITS ACCOUNT IS NOT CAPACITY. Past its included quota, an account
   keeps working by billing credits per request. That is a decision for the
   user, not a background loop, so such an account is never switched TO and
   never counts when asking "is there anywhere better to go" -- the second half
   matters as much as the first, since otherwise the loop believes it has
   somewhere to land and reports a healthy state it cannot act on.
2. SWITCHING CANNOT INTERRUPT A RUNNING CODEX. A swap is invisible to a live
   process (it read auth.json once, and its reload refuses to cross account
   ids), so "switch" while Codex is running would mean killing someone's
   session to make the switch real. Instead the loop switches when nothing is
   running and NOTIFIES when something is, which is the honest division: it
   acts when acting is free and tells you when it is not.
3. HYSTERESIS IS NOT OPTIONAL. Two accounts hovering near the threshold will
   ping-pong forever without a margin, and every switch of a Codex account
   costs a restart. The margin is on HEADROOM (quota left), so it reads the
   same way the decision does.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

from claude_swap.codex.processes import CodexProcess, running_codex_processes
from claude_swap.codex.store import CodexAccount
from claude_swap.codex.usage import CodexUsage
from claude_swap.settings import load_settings

_logger = logging.getLogger("claude-swap")

#: Switch once the active account's binding window passes this utilisation.
DEFAULT_THRESHOLD_PCT = 80.0
#: A candidate must have at least this many more points of headroom than the
#: active account before it is worth a switch -- and a Codex switch costs a
#: restart, so the margin is deliberately larger than a Claude one would be.
DEFAULT_HYSTERESIS_PCT = 10.0
#: Floor between switches, so a flapping quota reading cannot thrash accounts.
DEFAULT_COOLDOWN_S = 600.0
#: How often to poll when nothing needs doing.
DEFAULT_INTERVAL_S = 300.0


class Action(str, Enum):
    """What the loop concluded."""

    HOLD = "hold"                    # active account is fine
    SWITCH = "switch"                # switch now; nothing is running
    NOTIFY = "notify"                # should switch, but Codex is running
    NO_TARGET = "no_target"          # needs a switch, nowhere better to go
    ALL_EXHAUSTED = "all_exhausted"  # nothing has included quota left
    COOLDOWN = "cooldown"            # would switch, but too soon after the last
    NO_ACCOUNTS = "no_accounts"


@dataclass(frozen=True)
class AccountState:
    """One account's quota at decision time, or why it could not be read."""

    account: CodexAccount
    usage: CodexUsage | None = None
    error: str | None = None

    @property
    def eligible(self) -> bool:
        """Whether an automatic switch may land here.

        An account whose quota could not be read is NOT eligible: switching
        onto an unknown state could land on an exhausted account and force a
        second restart, and the loop has no way to tell.
        """
        return self.usage is not None and self.usage.auto_switch_eligible

    @property
    def headroom(self) -> float | None:
        """Percentage points of included quota left, or ``None`` if unknown."""
        if self.usage is None:
            return None
        percent = self.usage.binding_percent
        return None if percent is None else max(0.0, 100.0 - float(percent))

    @property
    def next_reset_at(self) -> int | None:
        return self.usage.next_reset_at if self.usage else None


@dataclass(frozen=True)
class AutoSettings:
    """Tunables for the loop."""

    threshold: float = DEFAULT_THRESHOLD_PCT
    hysteresis_pct: float = DEFAULT_HYSTERESIS_PCT
    cooldown_seconds: float = DEFAULT_COOLDOWN_S
    interval_seconds: float = DEFAULT_INTERVAL_S

    @classmethod
    def from_shared(cls, backup_root) -> "AutoSettings":
        """Read the SAME ``autoswitch`` settings the Claude side uses.

        One section, both providers: a user who sets a threshold means it for
        their accounts, not for one tool's half of them, and a separate
        ``codex`` section would silently leave the Codex loop on defaults after
        they configured the thing they could see. ``agent-switch config set
        autoswitch.threshold 85`` now moves both.

        The knobs that have no Codex meaning (strategy, model, API-key
        inclusion) are simply not read -- the Codex target rule is fixed:
        most included headroom, never a credits account.
        """
        shared = load_settings(backup_root)
        return cls(
            threshold=shared.threshold,
            hysteresis_pct=shared.hysteresis_pct,
            cooldown_seconds=shared.cooldown_seconds,
            interval_seconds=shared.interval_seconds,
        )


@dataclass(frozen=True)
class AutoDecision:
    """What to do, and why — the 'why' is reported, never just the 'what'."""

    action: Action
    reason: str
    target: AccountState | None = None
    active: AccountState | None = None
    processes: tuple[CodexProcess, ...] = ()
    soonest_reset_at: int | None = None

    @property
    def should_switch(self) -> bool:
        return self.action is Action.SWITCH


def _needs_replacing(state: AccountState | None, threshold: float) -> bool:
    """Whether the active account should be moved off.

    Unknown counts as "yes". An account whose quota cannot be read may be
    exhausted, and holding on it risks sitting on a dead account indefinitely;
    the cost of the alternative is one unnecessary switch.
    """
    if state is None or state.usage is None:
        return True
    if not state.usage.usable:
        return True
    # On credits means the included quota is gone. It still serves requests, but
    # the user is paying for each one, so the loop moves off it if it can.
    if state.usage.on_credits:
        return True
    percent = state.usage.binding_percent
    return percent is not None and float(percent) >= threshold


def decide(
    states: list[AccountState],
    *,
    active_number: str | None,
    settings: AutoSettings,
    processes: tuple[CodexProcess, ...] = (),
    now: float = 0.0,
    last_switch_at: float | None = None,
) -> AutoDecision:
    """Choose what the auto-switcher should do. Pure: no I/O, no clock."""
    if not states:
        return AutoDecision(Action.NO_ACCOUNTS, "no Codex accounts are managed")

    by_number = {s.account.number: s for s in states}
    active = by_number.get(active_number) if active_number else None

    if not _needs_replacing(active, settings.threshold):
        return AutoDecision(
            Action.HOLD,
            f"active account has {active.headroom:.0f}% headroom",
            active=active,
        )

    candidates = [
        s for s in states
        if s.eligible and not s.account.disabled and s.account.number != active_number
    ]
    if not candidates:
        # Distinguish "everything is spent" from "nothing was readable": the
        # first is a wait, the second is a problem to investigate.
        resets = [s.next_reset_at for s in states if s.next_reset_at]
        readable = any(s.usage is not None for s in states)
        return AutoDecision(
            Action.ALL_EXHAUSTED if readable else Action.NO_TARGET,
            "no account has included quota left"
            if readable
            else "no account's quota could be read",
            active=active,
            soonest_reset_at=min(resets) if resets else None,
        )

    best = max(candidates, key=lambda s: s.headroom or 0.0)
    active_headroom = active.headroom if active else 0.0
    if active_headroom is None:
        active_headroom = 0.0
    best_headroom = best.headroom or 0.0

    # The margin applies only when the active account is still a going concern.
    # An unusable or credits-billing account has nothing to defend, and holding
    # on it because a candidate missed the margin would be the worst outcome.
    active_is_viable = bool(
        active and active.usage and active.usage.auto_switch_eligible
    )
    if active_is_viable and best_headroom < active_headroom + settings.hysteresis_pct:
        return AutoDecision(
            Action.HOLD,
            f"best alternative ({best_headroom:.0f}% headroom) does not beat the "
            f"active account ({active_headroom:.0f}%) by the "
            f"{settings.hysteresis_pct:.0f} point margin",
            target=best,
            active=active,
        )

    if last_switch_at is not None:
        elapsed = now - last_switch_at
        if elapsed < settings.cooldown_seconds:
            return AutoDecision(
                Action.COOLDOWN,
                f"switched {elapsed:.0f}s ago; waiting out the "
                f"{settings.cooldown_seconds:.0f}s cooldown",
                target=best,
                active=active,
            )

    if processes:
        # Switching now would be a lie: a running Codex keeps serving the old
        # account regardless of what lands on disk.
        return AutoDecision(
            Action.NOTIFY,
            f"{best.account.display_label} has {best_headroom:.0f}% headroom, but "
            f"{len(processes)} Codex process(es) are running — switching would "
            "not reach them",
            target=best,
            active=active,
            processes=processes,
        )

    return AutoDecision(
        Action.SWITCH,
        f"moving to {best.account.display_label} ({best_headroom:.0f}% headroom)",
        target=best,
        active=active,
    )


def collect_states(switcher) -> list[AccountState]:
    """Read every managed account's quota into decision inputs.

    A failed read becomes an ``error`` state rather than an exception: one
    unreachable account must not stop the loop from moving off an exhausted one.
    """
    states: list[AccountState] = []
    accounts = {a.number: a for a in switcher.list_accounts()}
    for number, result in switcher.usage_all().items():
        account = accounts.get(number)
        if account is None:
            continue
        if isinstance(result, Exception):
            states.append(AccountState(account=account, error=str(result)))
        else:
            states.append(AccountState(account=account, usage=result))
    return states


# -- applying a decision ------------------------------------------------

STATE_FILENAME = "autoswitch_state.json"


def _state_path(switcher) -> "Path":  # noqa: F821 - Path imported lazily below
    from pathlib import Path

    return Path(switcher.store.root) / STATE_FILENAME


def read_last_switch_at(switcher) -> float | None:
    """When this loop last switched, for the cooldown.

    Persisted rather than held in memory because the cooldown must survive
    ``--once`` invocations from cron, which is the shape most people run.
    """
    import json

    try:
        data = json.loads(_state_path(switcher).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = data.get("lastSwitchAt") if isinstance(data, dict) else None
    return float(value) if isinstance(value, (int, float)) else None


def write_last_switch_at(switcher, when: float) -> None:
    """Record a switch for the cooldown. Best effort: a failure here must not
    undo a switch that already happened."""
    import json

    path = _state_path(switcher)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"lastSwitchAt": when}, indent=2), encoding="utf-8"
        )
    except OSError as e:
        _logger.warning("Could not record Codex auto-switch time: %s", e)


def run_once(
    switcher,
    *,
    settings: AutoSettings | None = None,
    now: float | None = None,
    dry_run: bool = False,
) -> AutoDecision:
    """Evaluate once and act on the result.

    Returns the decision either way, including under ``dry_run`` -- which
    reports exactly what would have happened without performing the switch, so
    the policy can be observed before it is trusted.
    """
    import time

    settings = settings or AutoSettings()
    now = time.time() if now is None else now

    states = collect_states(switcher)
    decision = decide(
        states,
        active_number=switcher.store.active_number(),
        settings=settings,
        processes=tuple(running_codex_processes()),
        now=now,
        last_switch_at=read_last_switch_at(switcher),
    )

    if decision.should_switch and decision.target is not None and not dry_run:
        switcher.switch_to(decision.target.account.number)
        write_last_switch_at(switcher, now)
        _logger.info(
            "Codex auto-switched to slot %s", decision.target.account.number
        )
    return decision
