"""The ``cswap codex`` command surface.

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

from claude_swap.codex.switcher import CodexSwitcher
from claude_swap.exceptions import ClaudeSwitchError
from claude_swap.printer import accent, bolded, dimmed, error, muted, yellowed


def _account_json(account) -> dict:
    return {
        "number": account.number,
        "email": account.email,
        "accountId": account.account_id,
        "plan": account.plan,
        "alias": account.alias,
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
        print(dimmed("  Run 'cswap codex add' to manage this account."))


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
        print(dimmed("  Log in with 'codex login', then run 'cswap codex add'."))
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


def codex_command(argv: list[str]) -> None:
    """Handle ``cswap codex <subcommand>``."""
    parser = argparse.ArgumentParser(
        prog="cswap codex",
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

    p_alias = sub.add_parser("alias", parents=[common],
                             help="Set or clear an account's alias")
    p_alias.add_argument("account", metavar="NUM|EMAIL")
    p_alias.add_argument("name", nargs="?", metavar="NAME")
    p_alias.add_argument("--unset", action="store_true", help="Remove the alias")

    args = parser.parse_args(argv)
    if args.command == "alias" and not args.unset and not args.name:
        parser.error("NAME is required (or pass --unset to remove the alias)")

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
        elif command == "switch":
            _report_switch(switcher.switch_to(args.account, force=args.force), args.json)
        elif command in ("remove", "rm"):
            removed = switcher.remove_account(args.account)
            if args.json:
                print(json.dumps({"removed": _account_json(removed)}, indent=2))
            else:
                print(f"{accent('Removed')} {removed.display_label} "
                      f"{muted(f'(slot {removed.number})')}")
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
