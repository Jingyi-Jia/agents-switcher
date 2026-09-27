"""Explicit, copy-only import of historical account storage."""

from __future__ import annotations

import argparse
import json

from agents_switcher.providers import safe_error


def storage_command(argv: list[str]) -> None:
    from agents_switcher.legacy_import import LegacyImport

    parser = argparse.ArgumentParser(
        prog="agent-switch storage",
        description="Inspect Agent Switch's independent storage or explicitly copy older saved accounts.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    status = commands.add_parser("status", help="show the independent destination and available historical sources")
    status.add_argument("--json", action="store_true", help="emit storage status as JSON")
    importer = commands.add_parser("import", help="copy a historical store without moving or deleting it")
    importer.add_argument("--source", required=True, choices=("legacy", "xdg"))
    importer.add_argument(
        "--confirm", action="store_true",
        help="confirm other switchers and provider clients are stopped, and consent to copying saved credentials",
    )
    importer.add_argument("--json", action="store_true", help="emit the import result as JSON")
    args = parser.parse_args(argv)
    try:
        storage = LegacyImport()
        result = storage.status() if args.command == "status" else storage.import_accounts(args.source, confirm=args.confirm)
    except Exception as error:
        message = safe_error(error)
        print(json.dumps({"ok": False, "error": message}) if args.json else message)
        raise SystemExit(1) from None
    if args.json:
        print(json.dumps(result))
    elif args.command == "status":
        print(f"Agent Switch storage: {result['destination']}")
        for source in result.get("sources", []):
            print(f"  {source['id']}: {source['label']}")
        if result.get("canImport"):
            print("Stop other switchers and provider clients, then use:")
            print("  agent-switch storage import --source SOURCE_ID --confirm")
        elif result.get("imported"):
            print("Historical accounts have already been imported; the original store was left intact.")
        else:
            print("No import is currently available. Existing Agent Switch accounts will not be overwritten.")
    else:
        print(result.get("message", "Import completed. The original store was left intact."))
    if not args.json:
        for warning in result.get("warnings", []):
            print(f"Warning: {warning}")
    raise SystemExit(0 if result.get("ok", True) else 1)
