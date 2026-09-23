"""The ``agent-switch codex`` command surface.

Kept in its own module rather than added to ``cli.py`` so the upstream file
takes a three-line dispatch hook and nothing else -- this fork tracks a
fast-moving base, and a large diff in its busiest file is a merge conflict every
release.

The one piece of UX that is not cosmetic is the restart notice. A Codex switch
is genuinely incomplete while a ``codex`` process is running: it loaded
``auth.json`` once and its 401-recovery reload refuses to cross account ids, so
the old account keeps serving until that process restarts. Printing a plain
"Switched" there would be false, so the restart requirement is reported as
prominently as the switch itself.
"""

from __future__ import annotations

import argparse
import json
import sys

from claude_swap.codex.autoswitch import Action, AutoSettings, run_once
from claude_swap.codex.switcher import CodexSwitcher
from claude_swap.codex.usage import CodexUsage
from claude_swap.exceptions import ClaudeSwitchError
from claude_swap.printer import accent, bolded, dimmed, error, muted, yellowed


def _cmd() -> str:
    """The command name the user invoked, for hints that tell them what to run."""
    from claude_swap.cli import _prog_name

    return _prog_name()


def _account_json(account) -> dict:
    return {
        "number": account.number,
        "email": account.email,
        "accountId": account.account_id,
        "plan": account.plan,
        "alias": account.alias,
        "disabled": account.disabled,
        "added": account.added,
    }


def _print_status(switcher: CodexSwitcher, as_json: bool) -> None:
    status = switcher.status()
    if as_json:
        print(json.dumps({
            "loggedIn": status.logged_in,
            "managed": status.is_managed,
            "email": status.identity.email if status.identity else None,
            "plan": status.identity.plan if status.identity else None,
            "activeNumber": status.active_number,
            "account": _account_json(status.account) if status.account else None,
        }, indent=2))
        return

    if not status.logged_in:
        print(dimmed("Codex is not logged in.") + "  Run 'codex login' to sign in.")
        return
    label = status.identity.display_label if status.identity else "unknown account"
    if status.account:
        print(f"{accent('Codex')}: {label} {muted(f'(slot {status.account.number})')}")
    else:
        print(f"{accent('Codex')}: {label} {yellowed('(not managed)')}")
        print(dimmed(f"  Run '{_cmd()} codex add' to manage this account."))


def _print_list(switcher: CodexSwitcher, as_json: bool) -> None:
    accounts = switcher.list_accounts()
    active = switcher.store.active_number()
    if as_json:
        print(json.dumps({
            "activeNumber": active,
            "accounts": [_account_json(a) for a in accounts],
        }, indent=2))
        return

    if not accounts:
        print(dimmed("No Codex accounts are managed yet."))
        print(dimmed(f"  Log in with 'codex login', then run '{_cmd()} codex add'."))
        return
    print(bolded("Codex accounts:"))
    for account in accounts:
        marker = accent(" *") if account.number == active else "  "
        alias = muted(f" ({account.alias})") if account.alias else ""
        plan = muted(f" [{account.plan}]") if account.plan else ""
        print(f"{marker} {account.number}: {account.email or account.account_id}{alias}{plan}")
    if active:
        print(dimmed("\n  * = active"))


def _report_switch(result, as_json: bool) -> None:
    if as_json:
        print(json.dumps({
            "switched": True,
            "account": _account_json(result.account),
            "previous": _account_json(result.previous) if result.previous else None,
            "syncedBack": result.synced_back,
            "restartRequired": result.restart_required,
            "processes": [
                {"pid": p.pid, "description": p.describe} for p in result.processes
            ],
        }, indent=2))
        return

    print(f"{accent('Switched to')} {result.account.display_label} "
          f"{muted(f'(slot {result.account.number})')}")
    if result.synced_back and result.previous:
        print(dimmed(f"  Saved {result.previous.display_label}'s current tokens first."))

    if not result.restart_required:
        return
    # Not a warning about tidiness: until these restart, Codex is still
    # authenticating as the PREVIOUS account.
    print()
    print(yellowed("  Restart Codex to use the new account."))
    print(dimmed("  Codex reads auth.json once at startup, so these are still "
                 "signed in as the old account:"))
    for process in result.processes:
        print(dimmed(f"    - {process.describe}"))



def _format_reset(usage: CodexUsage) -> str:
    """How long until the binding window frees up, in human terms."""
    window = usage.binding_window
    seconds = window.reset_after_seconds if window else None
    if not seconds or seconds <= 0:
        return ""
    if seconds >= 86400:
        return f"resets in {seconds // 86400}d {(seconds % 86400) // 3600}h"
    if seconds >= 3600:
        return f"resets in {seconds // 3600}h {(seconds % 3600) // 60}m"
    return f"resets in {max(1, seconds // 60)}m"


def _usage_json(usage: CodexUsage) -> dict:
    return {
        "plan": usage.plan,
        "allowed": usage.allowed,
        "limitReached": usage.limit_reached,
        "usable": usage.usable,
        "onCredits": usage.on_credits,
        "autoSwitchEligible": usage.auto_switch_eligible,
        "bindingPercent": usage.binding_percent,
        "summary": usage.summary,
        "windows": [
            {
                "usedPercent": w.used_percent,
                "label": w.label,
                "windowSeconds": w.window_seconds,
                "resetAt": w.reset_at,
                "resetAfterSeconds": w.reset_after_seconds,
            }
            for w in usage.windows
        ],
        "credits": None if usage.credits is None else {
            "hasCredits": usage.credits.has_credits,
            "unlimited": usage.credits.unlimited,
            "balance": usage.credits.balance,
            "overageLimitReached": usage.credits.overage_limit_reached,
            "approxLocalMessages": list(usage.credits.approx_local_messages),
            "approxCloudMessages": list(usage.credits.approx_cloud_messages),
        },
        "spendControlReached": usage.spend_control_reached,
        "reachedType": usage.reached_type,
        "resetCreditsAvailable": usage.reset_credits_available,
        "modelAvailability": usage.model_availability,
        "featureLimits": [
            {"name": f.name, "limitReached": f.limit_reached,
             "windows": [{"usedPercent": w.used_percent, "label": w.label}
                         for w in f.windows]}
            for f in usage.feature_limits
        ],
        "fetchedAt": usage.fetched_at,
    }


def _usage_line(account, usage: CodexUsage) -> str:
    """One account's quota, coloured by whether it can actually serve requests."""
    percent = usage.binding_percent
    body = usage.summary
    # An account on credits still WORKS, so it is not an error; but it is not
    # spare capacity either, and colouring it like a healthy account would
    # invite reaching for it.
    if not usage.auto_switch_eligible or (percent is not None and percent >= 80):
        body = yellowed(body)
    else:
        body = accent(body)
    reset = _format_reset(usage)
    extra = []
    if reset and not usage.usable:
        extra.append(reset)
    if usage.reset_credits_available:
        extra.append(f"{usage.reset_credits_available} reset credit(s)")
    unavailable = [m for m, ok in usage.model_availability.items() if not ok]
    if unavailable:
        extra.append("unavailable: " + ", ".join(sorted(unavailable)))
    tail = muted("  " + " · ".join(extra)) if extra else ""
    return f"{body}{tail}"


def _print_usage(switcher: CodexSwitcher, target: str | None, as_json: bool) -> None:
    if target:
        accounts = [switcher.resolve(target)]
        results = {accounts[0].number: switcher.usage_for(accounts[0].number)}
    else:
        accounts = switcher.list_accounts()
        results = switcher.usage_all()

    if as_json:
        print(json.dumps({
            "accounts": [
                {
                    **_account_json(a),
                    **({"error": str(results[a.number])}
                       if isinstance(results.get(a.number), Exception)
                       else {"usage": _usage_json(results[a.number])}),
                }
                for a in accounts if a.number in results
            ],
        }, indent=2))
        return

    if not accounts:
        print(dimmed("No Codex accounts are managed yet."))
        return
    active = switcher.store.active_number()
    print(bolded("Codex usage:"))
    for account in accounts:
        result = results.get(account.number)
        marker = accent(" *") if account.number == active else "  "
        name = account.email or account.account_id
        if isinstance(result, Exception):
            print(f"{marker} {account.number}: {name}  {yellowed(str(result))}")
        elif result is not None:
            print(f"{marker} {account.number}: {name}  {_usage_line(account, result)}")



def _thousands(value) -> str:
    return f"{int(value):,}" if isinstance(value, (int, float)) else "-"


def _print_stats(switcher: CodexSwitcher, target: str | None, as_json: bool) -> None:
    account = switcher.resolve(target) if target else None
    if account is None:
        accounts = switcher.list_accounts()
        if not accounts:
            print(dimmed("No Codex accounts are managed yet."))
            return
        account = accounts[0]

    stats = switcher.stats_for(account.number)
    resets = switcher.reset_credits_for(account.number)

    if as_json:
        print(json.dumps({
            "account": _account_json(account),
            "stats": {
                "displayName": stats.display_name,
                "username": stats.username,
                "lifetimeTokens": stats.lifetime_tokens,
                "peakDailyTokens": stats.peak_daily_tokens,
                "longestTurnSeconds": stats.longest_turn_seconds,
                "currentStreakDays": stats.current_streak_days,
                "longestStreakDays": stats.longest_streak_days,
                "fastModePercent": stats.fast_mode_percent,
                "topReasoningEffort": stats.top_reasoning_effort,
                "topReasoningEffortPercent": stats.top_reasoning_effort_percent,
                "totalThreads": stats.total_threads,
                "totalSkillsUsed": stats.total_skills_used,
                "uniqueSkillsUsed": stats.unique_skills_used,
                "topInvocations": [
                    {"kind": i.kind, "name": i.name, "usageCount": i.usage_count}
                    for i in stats.top_invocations
                ],
                "daily": [{"date": b.start_date, "tokens": b.tokens} for b in stats.daily],
                "weekly": [{"date": b.start_date, "tokens": b.tokens} for b in stats.weekly],
                "statsAsOf": stats.stats_as_of,
            },
            "resetCredits": {
                "availableCount": resets.available_count,
                "totalEarnedCount": resets.total_earned_count,
                "purchaseEligible": resets.purchase_eligible,
                "nextExpiry": resets.next_expiry,
                "credits": [
                    {"id": c.identifier, "type": c.reset_type, "status": c.status,
                     "expiresAt": c.expires_at, "title": c.title,
                     "available": c.is_available}
                    for c in resets.credits
                ],
            },
        }, indent=2))
        return

    name = account.email or account.account_id
    print(bolded(f"Codex stats — {name}") + muted(f"  (slot {account.number})"))
    if stats.stats_as_of:
        print(dimmed(f"  as of {stats.stats_as_of}"))
    print()
    print(f"  lifetime tokens   {accent(_thousands(stats.lifetime_tokens))}")
    print(f"  peak day          {_thousands(stats.peak_daily_tokens)}")
    if stats.longest_turn_seconds:
        print(f"  longest turn      {stats.longest_turn_seconds // 3600}h "
              f"{(stats.longest_turn_seconds % 3600) // 60}m")
    print(f"  streak            {stats.current_streak_days} day(s) "
          + muted(f"(best {stats.longest_streak_days})"))
    print(f"  threads           {_thousands(stats.total_threads)}")
    print(f"  skills            {_thousands(stats.total_skills_used)} uses "
          + muted(f"({stats.unique_skills_used} unique)"))
    if stats.fast_mode_percent is not None:
        print(f"  fast mode         {stats.fast_mode_percent:.0f}%")
    if stats.top_reasoning_effort:
        pct = stats.top_reasoning_effort_percent
        suffix = muted(f" ({pct:.0f}%)") if pct is not None else ""
        print(f"  reasoning effort  {stats.top_reasoning_effort}{suffix}")

    if stats.top_invocations:
        print()
        print(bolded("  Most used:"))
        for item in stats.top_invocations:
            print(f"    {item.usage_count:>5}  {item.name} {muted(f'({item.kind})')}")

    print()
    # Named "credits" like the billing balance, but a different thing entirely:
    # these clear an exhausted window rather than billing past it.
    if resets.available_count:
        line = f"  reset credits     {accent(str(resets.available_count))} available"
        if resets.next_expiry:
            line += muted(f" (next expires {resets.next_expiry})")
        print(line)
    else:
        extra = " · purchasable" if resets.purchase_eligible else ""
        print(dimmed(f"  reset credits     none available"
                     f" (earned {resets.total_earned_count} all time){extra}"))



#: Exit codes for `codex auto --once`, so cron and shell scripts can branch.
#: Mirrors the Claude-side `auto --once` contract.
_AUTO_EXIT = {
    Action.SWITCH: 0,
    Action.HOLD: 2,
    Action.COOLDOWN: 2,
    Action.NOTIFY: 3,
    Action.ALL_EXHAUSTED: 3,
    Action.NO_TARGET: 3,
    Action.NO_ACCOUNTS: 3,
}


def _decision_json(decision) -> dict:
    return {
        "action": decision.action.value,
        "reason": decision.reason,
        "switched": decision.action is Action.SWITCH,
        "restartRequired": decision.action is Action.NOTIFY,
        "active": None if decision.active is None
        else _account_json(decision.active.account),
        "target": None if decision.target is None
        else _account_json(decision.target.account),
        "processes": [{"pid": p.pid, "description": p.describe}
                      for p in decision.processes],
        "soonestResetAt": decision.soonest_reset_at,
    }


def _report_decision(decision, *, dry_run: bool) -> None:
    prefix = dimmed("[dry run] ") if dry_run else ""
    if decision.action is Action.SWITCH:
        verb = "Would switch" if dry_run else "Switched"
        print(f"{prefix}{accent(verb)} to {decision.target.account.display_label}"
              f"  {muted(decision.reason)}")
    elif decision.action is Action.NOTIFY:
        # The one case where the loop deliberately does not act.
        print(f"{prefix}{yellowed('Switch needed, but Codex is running.')}")
        print(dimmed(f"  {decision.reason}"))
        for process in decision.processes:
            print(dimmed(f"    - {process.describe}"))
        print(dimmed(f"  Run '{_cmd()} codex switch "
                     f"{decision.target.account.number}' after closing them."))
    elif decision.action is Action.ALL_EXHAUSTED:
        print(yellowed("All Codex accounts are out of included quota."))
        print(dimmed(f"  {decision.reason}"))
        if decision.soonest_reset_at:
            import datetime

            when = datetime.datetime.fromtimestamp(decision.soonest_reset_at)
            print(dimmed(f"  Soonest reset: {when:%Y-%m-%d %H:%M}"))
    elif decision.action in (Action.NO_TARGET, Action.NO_ACCOUNTS):
        print(yellowed(decision.reason))
    else:
        print(dimmed(f"{prefix}No change — {decision.reason}"))


def _auto_command(switcher, args) -> None:
    # Start from the shared autoswitch settings so this loop honours whatever
    # the user configured for the Claude side, then let explicit flags win.
    base = AutoSettings.from_shared(switcher.store.root.parent)
    settings = AutoSettings(
        threshold=base.threshold if args.threshold is None else args.threshold,
        hysteresis_pct=(
            base.hysteresis_pct if args.hysteresis is None else args.hysteresis
        ),
        cooldown_seconds=(
            base.cooldown_seconds if args.cooldown is None else args.cooldown
        ),
        interval_seconds=(
            base.interval_seconds if args.interval is None else args.interval
        ),
    )
    if args.once:
        decision = run_once(switcher, settings=settings, dry_run=args.dry_run)
        if args.json:
            print(json.dumps(_decision_json(decision), indent=2))
        else:
            _report_decision(decision, dry_run=args.dry_run)
        sys.exit(_AUTO_EXIT.get(decision.action, 3))

    import time

    print(dimmed(f"Watching Codex accounts (switch at {settings.threshold:.0f}%, "
                 f"every {settings.interval_seconds:.0f}s). Ctrl-C to stop."))
    while True:
        decision = run_once(switcher, settings=settings, dry_run=args.dry_run)
        if args.json:
            print(json.dumps(_decision_json(decision)), flush=True)
        elif decision.action is not Action.HOLD:
            _report_decision(decision, dry_run=args.dry_run)
        time.sleep(settings.interval_seconds)


def codex_command(argv: list[str]) -> None:
    """Handle ``<prog> codex <subcommand>``."""
    # Imported here, not at module level: cli.py pulls this module in inside
    # main(), so a top-level import back into it would be a cycle waiting for
    # the first person to import either one the other way round.
    from claude_swap.cli import _prog_name

    prog = _prog_name()
    parser = argparse.ArgumentParser(
        prog=f"{prog} codex",
        description="Manage and switch between multiple Codex CLI accounts.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  cswap codex                       # show the current Codex login
  cswap codex add --alias work      # manage the account you're logged in as
  cswap codex list
  cswap codex switch 2
  cswap codex switch work

A Codex switch only takes effect for processes started afterwards: Codex reads
auth.json once at startup and will not adopt a different account mid-run.
        """,
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable output")

    # --json must work on EITHER side of the subcommand: `codex status --json`
    # is what people type, and a flag defined only on the top-level parser makes
    # that an "unrecognized arguments" error. SUPPRESS is what keeps the two
    # copies from fighting -- without it the subparser's own default would
    # overwrite a --json given before the subcommand, silently turning it off.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Machine-readable output",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", parents=[common],
                   help="Show the current Codex login (the default)")
    sub.add_parser("list", aliases=["ls"], parents=[common],
                   help="List managed Codex accounts")
    sub.add_parser("tui", help="Open the Codex accounts dashboard")

    p_add = sub.add_parser("add", parents=[common],
                           help="Manage the account Codex is logged in as")
    p_add.add_argument("--alias", default="", metavar="NAME", help="Short name for it")

    p_switch = sub.add_parser("switch", parents=[common],
                              help="Make a managed account the live login")
    p_switch.add_argument("account", metavar="NUM|EMAIL|ALIAS")
    p_switch.add_argument(
        "--force",
        action="store_true",
        help="Proceed even if the current login is unmanaged (discards it)",
    )

    p_remove = sub.add_parser("remove", aliases=["rm"], parents=[common],
                              help="Stop managing an account")
    p_remove.add_argument("account", metavar="NUM|EMAIL|ALIAS")

    for command in ("enable", "disable"):
        p_state = sub.add_parser(command, parents=[common],
                                 help=f"{command.capitalize()} automatic rotation for an account")
        p_state.add_argument("account", metavar="NUM|EMAIL|ALIAS")

    p_usage = sub.add_parser("usage", parents=[common],
                             help="Show quota for managed accounts")
    p_usage.add_argument("account", nargs="?", metavar="NUM|EMAIL|ALIAS",
                         help="One account; omit for all")

    p_auto = sub.add_parser("auto", parents=[common],
                            help="Switch automatically as accounts run low")
    p_auto.add_argument("--once", action="store_true",
                        help="Check once and exit (for cron); sets the exit code")
    p_auto.add_argument("--dry-run", action="store_true",
                        help="Report what would happen without switching")
    p_auto.add_argument("--threshold", type=float, default=None, metavar="PCT",
                        help="Override autoswitch.threshold for this run")
    p_auto.add_argument("--hysteresis", type=float, default=None, metavar="PCT",
                        help="Override autoswitch.hysteresisPct for this run")
    p_auto.add_argument("--cooldown", type=float, default=None, metavar="SECONDS",
                        help="Override autoswitch.cooldownSeconds for this run")
    p_auto.add_argument("--interval", type=float, default=None, metavar="SECONDS",
                        help="Override autoswitch.intervalSeconds for this run")

    p_stats = sub.add_parser("stats", parents=[common],
                             help="Lifetime activity and reset credits")
    p_stats.add_argument("account", nargs="?", metavar="NUM|EMAIL|ALIAS",
                         help="Which account (default: the first)")

    p_alias = sub.add_parser("alias", parents=[common],
                             help="Set or clear an account's alias")
    p_alias.add_argument("account", metavar="NUM|EMAIL")
    p_alias.add_argument("name", nargs="?", metavar="NAME")
    p_alias.add_argument("--unset", action="store_true", help="Remove the alias")

    args = parser.parse_args(argv)
    if args.command == "alias" and not args.unset and not args.name:
        parser.error("NAME is required (or pass --unset to remove the alias)")
    if args.command == "tui":
        from claude_swap.tui import run

        sys.exit(run(None, start="codex"))

    try:
        switcher = CodexSwitcher()
        command = args.command or "status"

        if command == "status":
            _print_status(switcher, args.json)
        elif command in ("list", "ls"):
            _print_list(switcher, args.json)
        elif command == "add":
            account = switcher.add_current(alias=args.alias)
            if args.json:
                print(json.dumps({"added": _account_json(account)}, indent=2))
            else:
                print(f"{accent('Now managing')} {account.display_label} "
                      f"{muted(f'as slot {account.number}')}")
        elif command == "auto":
            _auto_command(switcher, args)
        elif command == "stats":
            _print_stats(switcher, args.account, args.json)
        elif command == "usage":
            _print_usage(switcher, args.account, args.json)
        elif command == "switch":
            _report_switch(switcher.switch_to(args.account, force=args.force), args.json)
        elif command in ("remove", "rm"):
            removed = switcher.remove_account(args.account)
            if args.json:
                print(json.dumps({"removed": _account_json(removed)}, indent=2))
            else:
                print(f"{accent('Removed')} {removed.display_label} "
                      f"{muted(f'(slot {removed.number})')}")
        elif command in ("enable", "disable"):
            updated = switcher.set_account_disabled(args.account, command == "disable")
            if args.json:
                print(json.dumps({"account": _account_json(updated)}, indent=2))
            else:
                print(f"{accent(command.capitalize() + 'd')} {updated.display_label}")
        elif command == "alias":
            updated = switcher.set_alias(args.account, "" if args.unset else args.name)
            if args.json:
                print(json.dumps({"account": _account_json(updated)}, indent=2))
            elif args.unset:
                print(f"{accent('Removed alias')} for slot {updated.number}")
            else:
                print(f"{accent('Set alias')} '{updated.alias}' for slot {updated.number}")
    except (ClaudeSwitchError, ValueError) as e:
        if args.json:
            print(json.dumps({"error": str(e)}, indent=2))
        else:
            error(f"Error: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print(f"\n{dimmed('Operation cancelled')}")
        sys.exit(130)
