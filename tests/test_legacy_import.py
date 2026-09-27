"""Copy-only historical import, isolated from native stores and providers."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents_switcher import legacy_import as module, macos_keychain
from agents_switcher.codex.identity import OPENAI_AUTH_CLAIM
from agents_switcher.credentials import SECURITY_SERVICE
from agents_switcher.exceptions import MigrationError
from agents_switcher.legacy_import import LegacyImport
from agents_switcher.migrations import LEGACY_KEYRING_SERVICE, LEGACY_SECURITY_SERVICE
from agents_switcher.models import Platform
from agents_switcher.paths import get_backup_root, get_legacy_backup_root, get_legacy_xdg_backup_root

_NATIVE_CHECK = module._check_quiescent

def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def codex_auth(account_id="codex-id", marker="saved"):
    claims = {"email": "codex@example.test", OPENAI_AUTH_CLAIM: {"chatgpt_account_id": account_id}}
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return {"tokens": {"id_token": f"header.{payload}.signature", "access_token": f"access-{marker}",
                       "refresh_token": f"refresh-{marker}", "account_id": account_id}, "future": marker}


def seed(root):
    claude = {"claudeAiOauth": {"accessToken": "synthetic-access", "refreshToken": "synthetic-refresh", "expiresAt": 9999999999999}}
    row = {"email": "claude@example.test", "uuid": "claude-id", "organizationUuid": "org-id", "alias": "work", "disabled": True}
    config = {"oauthAccount": {"emailAddress": row["email"], "accountUuid": row["uuid"], "organizationUuid": row["organizationUuid"]}}
    write(root / "sequence.json", {"accounts": {"1": row}, "sequence": [1], "activeAccountNumber": 1})
    write(root / "configs/.claude-config-1-claude@example.test.json", config)
    path = root / "credentials/.creds-1-claude@example.test.enc"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(base64.b64encode(json.dumps(claude).encode()))
    write(root / "codex/accounts.json", {"version": 1, "activeAccountNumber": "2", "accounts": {
        "2": {"email": "codex@example.test", "accountId": "codex-id", "alias": "personal", "disabled": True}}})
    path = root / "codex/credentials/2.json"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(base64.b64encode(json.dumps(codex_auth()).encode()))
    write(root / "settings.json", {"version": 1, "ui": {"theme": "dark"}, "autoswitch": {"threshold": 80.0}})
    write(root / "ui-preferences.json", {"version": 1, "theme": "light", "profileNoticeVersion": 1})
    write(root / "menubar_settings.json", {"show_account_name": False, "auto_switch_enabled": True})
    return claude, config


def tree(root):
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.fixture(autouse=True)
def isolated_processes(monkeypatch):
    monkeypatch.setattr(module, "_check_quiescent", lambda: None)
    monkeypatch.setattr(Platform, "detect", staticmethod(lambda: Platform.LINUX))
    monkeypatch.setattr(module.oauth, "fetch_oauth_profile", lambda *a, **k: pytest.fail("provider request"))
    monkeypatch.setattr(module.oauth, "try_refresh_oauth_credentials", lambda *a, **k: pytest.fail("token refresh"))


@pytest.fixture
def stores():
    source = get_legacy_backup_root()
    credentials, config = seed(source)
    return source, get_backup_root(), credentials, config


def test_status_is_sanitized_read_only_and_never_reads_credentials(stores, monkeypatch):
    source, destination, _, _ = stores
    before = tree(source)
    original = module._read

    def read(path, **kwargs):
        assert path.name in {"sequence.json", "accounts.json", "legacy-import.json"}
        return original(path, **kwargs)

    monkeypatch.setattr(module, "_read", read)
    monkeypatch.setattr(macos_keychain, "get_password", lambda *a: pytest.fail("status read Keychain"))
    status = LegacyImport().status()
    assert status["destination"] == str(destination)
    assert status["canImport"] and not status["imported"]
    assert status["sources"] == [{"id": "legacy", "label": "Historical home store", "counts": {"claude": 1, "codex": 1}}]
    assert "example.test" not in json.dumps(status)
    assert not destination.exists()
    monkeypatch.setattr(module, "_read", original)
    assert tree(source) == before


def test_fresh_status_never_creates_roots():
    assert LegacyImport().status() == {"destination": str(get_backup_root()), "imported": False,
                                    "canImport": False, "sources": [], "warnings": []}
    assert not get_backup_root().exists()


def test_copy_preserves_accounts_preferences_and_source(stores):
    source, destination, _, _ = stores
    before = tree(source)
    result = LegacyImport().import_accounts("legacy", confirm=True)
    assert result["ok"] and result["counts"] == {"claude": 1, "codex": 1}
    assert tree(source) == before
    assert json.loads((destination / "sequence.json").read_bytes())["accounts"]["1"]["alias"] == "work"
    assert json.loads((destination / "sequence.json").read_bytes())["accounts"]["1"]["disabled"] is True
    assert json.loads((destination / "codex/accounts.json").read_bytes())["accounts"]["2"]["alias"] == "personal"
    assert json.loads((destination / "codex/accounts.json").read_bytes())["accounts"]["2"]["disabled"] is True
    assert json.loads((destination / "settings.json").read_bytes())["autoswitch"]["threshold"] == 80
    assert json.loads((destination / "ui-preferences.json").read_bytes())["theme"] == "light"
    assert json.loads((destination / "menubar_settings.json").read_bytes())["auto_switch_enabled"] is False
    assert not (Path.home() / ".claude.json").exists()
    assert not (Path.home() / ".codex/auth.json").exists()
    assert LegacyImport().status()["imported"] is True
    assert LegacyImport().status()["canImport"] is False
    assert not list(destination.glob(".legacy-import-*"))
    if os.name != "nt":
        for path in destination.rglob("*"):
            assert path.stat().st_mode & 0o777 == (0o700 if path.is_dir() else 0o600)


@pytest.mark.parametrize("confirm", [False, None, 1, "true", [], {}])
def test_exact_consent_required(stores, confirm):
    with pytest.raises(MigrationError, match="Confirm"):
        LegacyImport().import_accounts("legacy", confirm=confirm)
    assert not stores[1].exists()


@pytest.mark.parametrize("source", ["", "both", "../legacy", "/tmp/source", None, [], {}])
def test_source_is_a_fixed_id(stores, source):
    with pytest.raises(MigrationError, match="source ID"):
        LegacyImport().import_accounts(source, confirm=True)


def test_multiple_sources_are_selected_not_merged(stores):
    seed(get_legacy_xdg_backup_root())
    importer = LegacyImport()
    assert len(importer.status()["sources"]) == 2
    assert "Multiple" in " ".join(importer.status()["warnings"])
    assert importer.import_accounts("xdg", confirm=True)["counts"] == {"claude": 1, "codex": 1}
    assert get_legacy_xdg_backup_root().exists() and stores[0].exists()


def test_caches_empty_directories_and_existing_preferences_are_preserved(stores):
    _, destination, _, _ = stores
    write(destination / "cache/usage.json", {"saved": "cache"})
    (destination / "agents-switcher.log").write_text("existing log")
    for relative in ("configs", "credentials", "codex/credentials"):
        (destination / relative).mkdir(parents=True, exist_ok=True)
    write(destination / "ui-preferences.json", {"version": 1, "theme": "dark", "profileNoticeVersion": 0})
    before = tree(destination)
    result = LegacyImport().import_accounts("legacy", confirm=True)
    assert result["ok"]
    assert all((destination / name).read_bytes() == raw for name, raw in before.items())
    assert "Existing ui-preferences.json" in " ".join(result["warnings"])


def test_browser_runtime_record_is_never_read_or_changed(stores, monkeypatch):
    _, destination, _, _ = stores
    destination.mkdir(parents=True)
    runtime = destination / "web-url"
    runtime.write_text("private-runtime-record", encoding="utf-8")
    original = module._read

    def read(path, **kwargs):
        assert path != runtime
        return original(path, **kwargs)

    monkeypatch.setattr(module, "_read", read)
    assert LegacyImport().status()["canImport"] is True
    assert LegacyImport().import_accounts("legacy", confirm=True)["ok"]
    assert runtime.read_text(encoding="utf-8") == "private-runtime-record"


@pytest.mark.parametrize("name", ["sequence.json", "credentials/orphan.enc", "codex/accounts.json", "codex/credentials/orphan.json", ".legacy-import-abandoned/file"])
def test_destination_conflicts_never_overwrite(stores, name):
    destination = stores[1]
    write(destination / name, {"keep": True})
    before = tree(destination)
    assert LegacyImport().status()["canImport"] is False
    with pytest.raises(MigrationError, match="destination"):
        LegacyImport().import_accounts("legacy", confirm=True)
    assert tree(destination) == before


@pytest.mark.parametrize("name", ["sessions/private", "claude-desktop/profiles/private", "cache/usage.json", "claude-swap.log", "mappings.json", ".lock"])
def test_excluded_data_is_not_copied(stores, name):
    source, destination, _, _ = stores
    write(source / name, {"excluded": "sensitive"})
    result = LegacyImport().import_accounts("legacy", confirm=True)
    assert "not imported" in " ".join(result["warnings"])
    assert not (destination / name).exists()
    assert (source / name).exists()


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_identity_mismatch_rolls_back_before_writes(stores, provider):
    source, destination, _, config = stores
    if provider == "claude":
        config["oauthAccount"]["accountUuid"] = "different"
        write(source / "configs/.claude-config-1-claude@example.test.json", config)
    else:
        (source / "codex/credentials/2.json").write_bytes(base64.b64encode(json.dumps(codex_auth("different")).encode()))
    with pytest.raises(MigrationError, match="identity"):
        LegacyImport().import_accounts("legacy", confirm=True)
    assert not tree(destination)


@pytest.mark.parametrize("name,raw", [
    ("sequence.json", b"{"),
    ("sequence.json", b'{"accounts":{},"accounts":{},"sequence":[]}'),
    ("configs/.claude-config-1-claude@example.test.json", b"[]"),
    ("credentials/.creds-1-claude@example.test.enc", b"invalid-base64"),
    ("codex/accounts.json", b'{"version":2,"accounts":{}}'),
    ("codex/credentials/2.json", b"invalid-base64"),
    ("settings.json", b'{"autoswitch":{"threshold":"secret-value"}}'),
])
def test_corrupt_data_fails_without_leaking_values(stores, name, raw):
    source, destination, _, _ = stores
    (source / name).write_bytes(raw)
    before = tree(source)
    with pytest.raises(MigrationError) as error:
        LegacyImport().import_accounts("legacy", confirm=True)
    assert "secret-value" not in str(error.value)
    assert not tree(destination)
    assert tree(source) == before


def test_unreadable_source_is_not_empty(stores, monkeypatch):
    original = module._read

    def read(path, **kwargs):
        if path == stores[0] / "codex/accounts.json":
            raise PermissionError("private exception body")
        return original(path, **kwargs)

    monkeypatch.setattr(module, "_read", read)
    assert "error" in LegacyImport().status()["sources"][0]
    with pytest.raises(MigrationError) as error:
        LegacyImport().import_accounts("legacy", confirm=True)
    assert "private exception body" not in str(error.value)


@pytest.mark.parametrize("relative", ["sequence.json", "configs", "credentials/.creds-1-claude@example.test.enc", "codex/credentials"])
def test_linked_source_is_rejected(stores, relative, tmp_path):
    source, destination, _, _ = stores
    path = source / relative
    real = tmp_path / "linked-target"
    path.rename(real)
    try:
        path.symlink_to(real, target_is_directory=real.is_dir())
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(MigrationError):
        LegacyImport().import_accounts("legacy", confirm=True)
    assert not tree(destination)


def test_linked_destination_ancestor_is_rejected(stores, tmp_path):
    destination = stores[1]
    destination.parent.mkdir(parents=True, exist_ok=True)
    real = tmp_path / "external"
    real.mkdir()
    try:
        destination.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(MigrationError):
        LegacyImport().import_accounts("legacy", confirm=True)
    assert not list(real.iterdir())


def test_overlapping_destination_does_not_modify_the_source(stores):
    source = stores[0]
    before = tree(source)
    with pytest.raises(MigrationError, match="overlaps"):
        LegacyImport(source / "agents-switcher").import_accounts("legacy", confirm=True)
    assert tree(source) == before
    assert not (source / "agents-switcher").exists()


@pytest.mark.parametrize("service", [LEGACY_SECURITY_SERVICE, LEGACY_KEYRING_SERVICE])
def test_macos_copy_preserves_both_old_services(stores, monkeypatch, block_real_keychain, service):
    source, destination, credentials, _ = stores
    monkeypatch.setattr(Platform, "detect", staticmethod(lambda: Platform.MACOS))
    (source / "credentials/.creds-1-claude@example.test.enc").unlink()
    username = "account-1-claude@example.test"
    raw = json.dumps(credentials)
    block_real_keychain.set_password(service, username, raw)
    before = dict(block_real_keychain.data)
    result = LegacyImport(destination).import_accounts("legacy", confirm=True)
    assert result["ok"]
    assert all(block_real_keychain.data[key] == value for key, value in before.items())
    assert block_real_keychain.get_password(SECURITY_SERVICE, username) == raw
    assert not (destination / "credentials/.creds-1-claude@example.test.enc").exists()


def test_keychain_denial_is_failure_not_empty(stores, monkeypatch):
    source, destination, _, _ = stores
    monkeypatch.setattr(Platform, "detect", staticmethod(lambda: Platform.MACOS))
    (source / "credentials/.creds-1-claude@example.test.enc").unlink()
    monkeypatch.setattr(macos_keychain, "get_password", lambda *a: (_ for _ in ()).throw(macos_keychain.KeychainError("private")))
    with pytest.raises(MigrationError, match="denied"):
        LegacyImport(destination).import_accounts("legacy", confirm=True)
    assert not tree(destination)


def test_existing_destination_keychain_is_never_overwritten(stores, monkeypatch, block_real_keychain):
    source, destination, _, _ = stores
    monkeypatch.setattr(Platform, "detect", staticmethod(lambda: Platform.MACOS))
    block_real_keychain.set_password(SECURITY_SERVICE, "account-1-claude@example.test", "keep")
    before = dict(block_real_keychain.data)
    with pytest.raises(MigrationError, match="destination Keychain"):
        LegacyImport(destination).import_accounts("legacy", confirm=True)
    assert block_real_keychain.data == before
    assert not tree(destination)


@pytest.mark.parametrize("phase", ["stage", "publish"])
def test_partial_file_failure_rolls_back_only_created_data(stores, monkeypatch, phase):
    source, destination, _, _ = stores
    before = tree(source)
    write(destination / "cache/keep.json", {"keep": True})
    if phase == "stage":
        original = module._write_new

        def write_fail(path, raw):
            original(path, raw)
            if path.name == "2.json":
                raise OSError("write failed")

        monkeypatch.setattr(module, "_write_new", write_fail)
    else:
        original = module.os.link

        def publish_fail(*args, **kwargs):
            original(*args, **kwargs)
            if Path(args[1]).name == "accounts.json":
                raise OSError("publish failed")

        monkeypatch.setattr(module.os, "link", publish_fail)
    with pytest.raises(MigrationError):
        LegacyImport().import_accounts("legacy", confirm=True)
    assert tree(destination) == {"cache/keep.json": b'{"keep": true}'}
    assert tree(source) == before


def test_keychain_write_failure_rolls_back_created_entry(stores, monkeypatch, block_real_keychain):
    _, destination, _, _ = stores
    monkeypatch.setattr(Platform, "detect", staticmethod(lambda: Platform.MACOS))
    before = dict(block_real_keychain.data)
    original = macos_keychain.set_password

    def write_fail(*args):
        original(*args)
        raise macos_keychain.KeychainError("failed after write")

    monkeypatch.setattr(macos_keychain, "set_password", write_fail)
    with pytest.raises(MigrationError):
        LegacyImport(destination).import_accounts("legacy", confirm=True)
    assert block_real_keychain.data == before
    assert not tree(destination)


def test_source_changes_are_detected_before_publication(stores, monkeypatch):
    source, destination, _, _ = stores
    calls = 0

    def check():
        nonlocal calls
        calls += 1
        if calls == 2:
            (source / "settings.json").write_text("{}")

    monkeypatch.setattr(module, "_check_quiescent", check)
    with pytest.raises(MigrationError, match="changed"):
        LegacyImport().import_accounts("legacy", confirm=True)
    assert not tree(destination)


def test_repeat_import_refuses_without_changes(stores):
    LegacyImport().import_accounts("legacy", confirm=True)
    before = tree(stores[1])
    with pytest.raises(MigrationError, match="already imported"):
        LegacyImport().import_accounts("legacy", confirm=True)
    assert tree(stores[1]) == before


def test_latest_identity_matched_live_credentials_are_copied_not_replaced(stores):
    _, destination, credentials, config = stores
    credentials["claudeAiOauth"]["accessToken"] = "fresh-access"
    write(Path.home() / ".claude.json", config)
    write(Path.home() / ".claude/.credentials.json", credentials)
    live = codex_auth(marker="live")
    write(Path.home() / ".codex/auth.json", live)
    before = {path: path.read_bytes() for path in (Path.home() / ".claude.json", Path.home() / ".claude/.credentials.json", Path.home() / ".codex/auth.json")}
    LegacyImport().import_accounts("legacy", confirm=True)
    assert json.loads(base64.b64decode((destination / "credentials/.creds-1-claude@example.test.enc").read_bytes())) == credentials
    assert json.loads(base64.b64decode((destination / "codex/credentials/2.json").read_bytes())) == live
    assert all(path.read_bytes() == raw for path, raw in before.items())


def test_foreign_live_lineage_is_not_misfiled(stores):
    source, destination, credentials, config = stores
    credentials["claudeAiOauth"]["refreshToken"] = "different-account"
    write(Path.home() / ".claude.json", config)
    write(Path.home() / ".claude/.credentials.json", credentials)
    write(Path.home() / ".codex/auth.json", codex_auth(account_id="foreign"))
    result = LegacyImport().import_accounts("legacy", confirm=True)
    assert "different token lineage" in " ".join(result["warnings"])
    for relative in ("credentials/.creds-1-claude@example.test.enc", "codex/credentials/2.json"):
        assert json.loads(base64.b64decode((source / relative).read_bytes())) == json.loads(base64.b64decode((destination / relative).read_bytes()))


def test_source_directory_lock_refuses_import(stores):
    (stores[0] / ".lock").mkdir()
    with pytest.raises(MigrationError, match="locked"):
        LegacyImport().import_accounts("legacy", confirm=True)


def test_import_holds_both_destination_mutation_locks(stores, monkeypatch):
    original = module._write_new

    def write_checked(path, raw):
        assert (stores[1] / ".lock").is_dir()
        assert (stores[1] / "codex/.lock").is_dir()
        original(path, raw)

    monkeypatch.setattr(module, "_write_new", write_checked)
    LegacyImport().import_accounts("legacy", confirm=True)


def test_concurrent_destination_mutation_blocks_import(stores, monkeypatch):
    original = module.FileLock
    monkeypatch.setattr(module, "FileLock", lambda path: original(path, timeout=0.03))
    with original(stores[1] / ".lock"):
        with pytest.raises(MigrationError):
            LegacyImport().import_accounts("legacy", confirm=True)
        assert (stores[1] / ".lock").is_dir()
        assert not (stores[1] / "sequence.json").exists()


def test_destination_race_preserves_external_file(stores, monkeypatch):
    original = module._write_new
    destination = stores[1]

    def write_with_race(path, raw):
        original(path, raw)
        if path.name == "sequence.json":
            write(destination / "sequence.json", {"external": "keep"})

    monkeypatch.setattr(module, "_write_new", write_with_race)
    with pytest.raises(MigrationError, match="not overwritten"):
        LegacyImport().import_accounts("legacy", confirm=True)
    assert tree(destination) == {"sequence.json": b'{"external": "keep"}'}


def test_keychain_source_change_rolls_back_new_credentials(stores, monkeypatch, block_real_keychain):
    source, destination, credentials, _ = stores
    monkeypatch.setattr(Platform, "detect", staticmethod(lambda: Platform.MACOS))
    (source / "credentials/.creds-1-claude@example.test.enc").unlink()
    username = "account-1-claude@example.test"
    block_real_keychain.set_password(LEGACY_SECURITY_SERVICE, username, json.dumps(credentials))
    original = macos_keychain.set_password

    def write_with_source_change(service, account, value):
        original(service, account, value)
        if service == SECURITY_SERVICE:
            original(LEGACY_SECURITY_SERVICE, username, "externally changed")

    monkeypatch.setattr(macos_keychain, "set_password", write_with_source_change)
    with pytest.raises(MigrationError, match="changed"):
        LegacyImport(destination).import_accounts("legacy", confirm=True)
    assert block_real_keychain.get_password(LEGACY_SECURITY_SERVICE, username) == "externally changed"
    assert block_real_keychain.get_password(SECURITY_SERVICE, username) is None
    assert not tree(destination)


@pytest.mark.parametrize("executable,arguments", [
    ("/usr/bin/claude", ("claude",)),
    ("/usr/bin/codex", ("codex",)),
    ("/usr/bin/codex-aarch64-apple-darwin", ("codex",)),
    ("/usr/bin/node", ("node", "--flag", "--flag2", "--flag3", "/vendor/codex.mjs")),
    ("/home/example/.local/share/claude/versions/2.1.200", ()),
    ("/usr/bin/python", ("python", "-m", "claude_swap.cli", "auto")),
    ("/usr/bin/python", ("python", "/bin/cswap", "auto")),
    ("/Applications/Claude.app/Contents/MacOS/Claude", ("Claude",)),
])
def test_native_process_interlock_blocks_tools(monkeypatch, executable, arguments):
    process = SimpleNamespace(pid=os.getpid() + 1, executable=executable, arguments=arguments)
    monkeypatch.setattr(module.codex_desktop, "_posix_processes", lambda: [process])
    monkeypatch.setattr(module, "_windows_processes", lambda: [process])
    with pytest.raises(MigrationError, match="running"):
        _NATIVE_CHECK()


def test_failed_native_process_scan_blocks_import(monkeypatch):
    monkeypatch.setattr(module.codex_desktop, "_posix_processes", lambda: (_ for _ in ()).throw(ValueError("private")))
    monkeypatch.setattr(module, "_windows_processes", lambda: (_ for _ in ()).throw(ValueError("private")))
    with pytest.raises(MigrationError, match="reliably"):
        _NATIVE_CHECK()


def test_python_switcher_process_arguments_are_inspected(monkeypatch):
    process = SimpleNamespace(pid=os.getpid() + 1, executable="/usr/bin/python3.12", arguments=())
    monkeypatch.setattr(module.sys, "platform", "linux")
    monkeypatch.setattr(module.codex_desktop, "_posix_processes", lambda: [process])
    monkeypatch.setattr(module.codex_desktop, "_read_linux_arguments", lambda pid: ("/usr/bin/python3.12", ("python3.12", "-m", "claude_swap", "auto")))
    with pytest.raises(MigrationError, match="running"):
        _NATIVE_CHECK()


@pytest.mark.parametrize("arguments", [
    ("python", "/usr/bin/pipx", "run", "agent-switch", "--help"),
    ("python", "-m", "pipx", "run", "agent-switch", "--help"),
])
def test_python_launchers_are_not_account_managers(monkeypatch, arguments):
    process = SimpleNamespace(pid=os.getpid() + 1, executable="/usr/bin/python3.12", arguments=arguments)
    monkeypatch.setattr(module.codex_desktop, "_posix_processes", lambda: [process])
    monkeypatch.setattr(module, "_windows_processes", lambda: [process])
    _NATIVE_CHECK()


def test_windows_process_scan_covers_claude_and_python_tools(monkeypatch):
    rows = [{"Pid": 42, "Executable": "C:\\Python\\python.exe", "Command": 'python.exe -m claude_swap auto'},
            {"Pid": 43, "Executable": "C:\\Apps\\Claude.exe", "Command": '"C:\\Apps\\Claude.exe"'}]

    def run(command, **kwargs):
        script = command[-1]
        assert "claude.*" in script and "python.*" in script and "cswap" in script
        return SimpleNamespace(returncode=0, stdout=json.dumps({"Complete": True, "Processes": rows}))

    monkeypatch.setattr(module.subprocess, "run", run)
    processes = module._windows_processes()
    assert processes[0].arguments == ("python.exe", "-m", "claude_swap", "auto")
    assert processes[1].executable == "C:\\Apps\\Claude.exe"


def test_windows_legacy_keyring_is_copied_not_deleted(stores, monkeypatch):
    import sys

    source, destination, credential, _ = stores
    monkeypatch.setattr(Platform, "detect", staticmethod(lambda: Platform.WINDOWS))
    (source / "credentials/.creds-1-claude@example.test.enc").unlink()
    keyring = sys.modules["keyring"]
    keyring.set_password(LEGACY_KEYRING_SERVICE, "account-1-claude@example.test", json.dumps(credential))
    assert LegacyImport(destination).import_accounts("legacy", confirm=True)["ok"]
    assert json.loads(keyring.get_password(LEGACY_KEYRING_SERVICE, "account-1-claude@example.test")) == credential
    assert json.loads(base64.b64decode((destination / "credentials/.creds-1-claude@example.test.enc").read_bytes())) == credential
