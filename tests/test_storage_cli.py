"""The storage command dispatches only explicit, consented imports."""

from __future__ import annotations

import json
import sys
from unittest.mock import Mock

import pytest

from agents_switcher.exceptions import MigrationError
from agents_switcher.storage_cli import storage_command


@pytest.fixture
def storage(monkeypatch):
    from agents_switcher import legacy_import

    store = Mock()
    store.status.return_value = {
        "destination": "/private/agents-switcher", "canImport": True, "imported": False,
        "sources": [{"id": "legacy", "label": "Previous account store"}], "warnings": [],
    }
    store.import_accounts.return_value = {"ok": True, "message": "Copied accounts without deleting the source.", "counts": {"claude": 1, "codex": 2}, "warnings": []}
    monkeypatch.setattr(legacy_import, "LegacyImport", lambda: store)
    return store


def run(argv):
    with pytest.raises(SystemExit) as exit:
        storage_command(argv)
    return exit.value.code


def test_status_json_reports_destination_without_importing(storage, capsys):
    assert run(["status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == storage.status.return_value
    storage.import_accounts.assert_not_called()


def test_human_status_explains_the_explicit_copy_command(storage, capsys):
    assert run(["status"]) == 0
    output = capsys.readouterr().out
    assert "/private/agents-switcher" in output
    assert "legacy: Previous account store" in output
    assert "Stop other switchers" in output
    assert "storage import --source SOURCE_ID --confirm" in output
    storage.import_accounts.assert_not_called()


def test_import_json_dispatches_the_selected_fixed_source_with_exact_consent(storage, capsys):
    assert run(["import", "--source", "legacy", "--confirm", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["counts"] == {"claude": 1, "codex": 2}
    storage.import_accounts.assert_called_once_with("legacy", confirm=True)
    storage.status.assert_not_called()


@pytest.mark.parametrize("argv", [["import"], ["import", "--source", "/outside", "--confirm"], ["import", "--source", "legacy", "--force"]])
def test_import_requires_a_fixed_source_and_has_no_overwrite_flag(storage, argv):
    assert run(argv) == 2
    storage.import_accounts.assert_not_called()


def test_failed_import_reports_nonzero_json_without_sensitive_error_details(storage, capsys):
    storage.import_accounts.side_effect = MigrationError("refresh_token=synthetic-sensitive-value")
    assert run(["import", "--source", "xdg", "--confirm", "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False
    assert "synthetic-sensitive-value" not in result["error"]


def test_main_dispatch_reaches_storage_without_constructing_a_provider(storage, monkeypatch, capsys):
    from agents_switcher import cli

    monkeypatch.setattr(sys, "argv", ["agent-switch", "storage", "status", "--json"])
    monkeypatch.setattr(cli, "ClaudeAccountSwitcher", Mock(side_effect=AssertionError("No provider construction")))
    with pytest.raises(SystemExit) as exit:
        cli.main()
    assert exit.value.code == 0
    assert json.loads(capsys.readouterr().out)["destination"] == "/private/agents-switcher"
    storage.import_accounts.assert_not_called()
