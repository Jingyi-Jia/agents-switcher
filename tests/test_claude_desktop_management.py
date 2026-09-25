from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

import pytest

from claude_swap import claude_desktop as cd
from claude_swap.providers import ProviderActionError
from tests.test_claude_desktop import profiles
from tests.test_web_actions import request, web


POSIX_DELETE = pytest.mark.skipif(os.name == "nt", reason="Profile deletion supports macOS and Linux only")


def test_legacy_registry_is_read_without_writes_and_migrates_on_update(profiles):
    manager = profiles.manager
    entry = manager.create("Work", confirm=True)["profile"]
    legacy = {"id": entry["id"], "name": entry["name"]}
    path = manager.root / "profiles.json"
    original = json.dumps({"version": 1, "profiles": [legacy]}).encode()
    path.write_bytes(original)

    assert manager.status()["profiles"] == [entry]
    manager.open(entry["id"], confirm=True)
    assert path.read_bytes() == original
    result = manager.update(entry["id"], "Office", "typed@example.com", confirm=True)
    assert result["profile"] == {**entry, "name": "Office", "emailLabel": "typed@example.com"}
    assert json.loads(path.read_bytes()) == {"version": 2, "profiles": [result["profile"]]}


def test_create_migrates_all_legacy_entries_without_inventing_email_labels(profiles):
    manager = profiles.manager
    first = manager.create("Work", confirm=True)["profile"]
    path = manager.root / "profiles.json"
    path.write_text(json.dumps({"version": 1, "profiles": [{"id": first["id"], "name": "Work"}]}))

    second = manager.create("Personal", emailLabel="personal@example.com", confirm=True)["profile"]
    assert manager.status()["profiles"] == [first, second]
    assert json.loads(path.read_text()) == {"version": 2, "profiles": [first, second]}


@pytest.mark.parametrize("version,entries", [
    (1, [{"id": "a" * 32, "name": "Work", "emailLabel": "typed@example.com"}]),
    (2, [{"id": "a" * 32, "name": "Work"}]),
    (2, [{"id": "a" * 32, "name": "Work", "emailLabel": "", "emailVerified": True}]),
    (2, [{"id": "a" * 32, "name": "Work", "emailLabel": "", "token": "private-value"}]),
    (2, [{"id": "a" * 32, "name": "Work", "emailLabel": None}]),
    (2, [{"id": "a" * 32, "name": "Work", "emailLabel": "typed@example.com\n"}]),
    (2, [{"id": "a" * 32, "name": "Work", "emailLabel": " typed@example.com"}]),
    (2, [{"id": "a" * 32, "name": "Work", "emailLabel": "not-an-email"}]),
    (2, [{"id": "default", "name": "Usual", "emailLabel": ""}]),
    (2, [{"id": "../../elsewhere", "name": "Work", "emailLabel": ""}]),
    (2, [{"id": "a" * 32, "name": "Work", "emailLabel": ""},
         {"id": "a" * 32, "name": "Office", "emailLabel": ""}]),
    (2, [{"id": "a" * 32, "name": "Work", "emailLabel": ""},
         {"id": "b" * 32, "name": "work", "emailLabel": ""}]),
    (3, []),
    (True, []),
])
def test_strict_registry_versions_refuse_malformed_metadata_without_writes(profiles, version, entries):
    manager = profiles.manager
    manager.root.mkdir()
    path = manager.root / "profiles.json"
    raw = json.dumps({"version": version, "profiles": entries}).encode()
    path.write_bytes(raw)
    status = manager.status()
    assert not status["canManage"] and not status["canDelete"]
    assert not status["canCreate"] and status["profiles"] == []
    assert "invalid" in status["error"] and "private-value" not in json.dumps(status)
    for action in (
        lambda: manager.create("New", confirm=True),
        lambda: manager.update("a" * 32, "New", "", confirm=True),
        lambda: manager.delete("a" * 32, confirm=True),
    ):
        with pytest.raises(ProviderActionError, match="invalid"):
            action()
        assert path.read_bytes() == raw


@pytest.mark.parametrize("raw", [
    b'{"version":2,"profiles":[{"id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","name":"Work","emailLabel":"","emailLabel":"private-value"}]}',
    b'{"version":2,"profiles":[],"token":"private-value"}',
    pytest.param(b" " * (cd._MAX_REGISTRY_BYTES + 1), id="oversized-v2"),
    pytest.param(b'{"version":1,"profiles":[]}' + b" " * 65536, id="oversized-v1"),
])
def test_registry_duplicate_unknown_keys_and_size_limits_stay_strict(profiles, raw):
    manager = profiles.manager
    manager.root.mkdir()
    path = manager.root / "profiles.json"
    path.write_bytes(raw)
    with pytest.raises(ProviderActionError, match="invalid"):
        manager.create("Work", confirm=True)
    assert path.read_bytes() == raw
    assert "private-value" not in json.dumps(manager.status())


@pytest.mark.parametrize("email_label", [
    None, True, 12, [], {}, "not-an-email", "@example.com", "typed@", "a@b@c", "typed @example.com",
    "typed@example.com\n", "\ttyped@example.com", "a\x00b@example.com", "a\u202eb@example.com",
    "a\ud800b@example.com", "x" * 319 + "@x",
])
def test_invalid_email_labels_never_create_or_change_a_registry(profiles, email_label):
    manager = profiles.manager
    with pytest.raises(ProviderActionError, match="email label"):
        manager.create("Work", emailLabel=email_label, confirm=True)
    assert not manager.root.exists()
    entry = manager.create("Work", confirm=True)["profile"]
    path = manager.root / "profiles.json"
    original = path.read_bytes()
    with pytest.raises(ProviderActionError, match="email label"):
        manager.update(entry["id"], "Office", email_label, confirm=True)
    assert path.read_bytes() == original


@pytest.mark.parametrize("name", ["\nWork", "Work\r", "Work\t", "Work\u202e", "x" * 65, True])
def test_invalid_names_are_rejected_on_create_and_update(profiles, name):
    manager = profiles.manager
    with pytest.raises(ProviderActionError, match="profile name"):
        manager.create(name, confirm=True)
    assert not manager.root.exists()
    entry = manager.create("Work", confirm=True)["profile"]
    original = (manager.root / "profiles.json").read_bytes()
    with pytest.raises(ProviderActionError, match="profile name"):
        manager.update(entry["id"], name, "", confirm=True)
    assert (manager.root / "profiles.json").read_bytes() == original


def test_labels_are_trimmed_unverified_and_can_be_cleared(profiles):
    manager = profiles.manager
    entry = manager.create(" Work ", emailLabel=" Typed+label@Example.com ", confirm=True)["profile"]
    assert entry["name"] == "Work" and entry["emailLabel"] == "Typed+label@Example.com"
    assert set(entry) == {"id", "name", "emailLabel"}
    second = manager.create("Other", emailLabel=entry["emailLabel"], confirm=True)["profile"]
    result = manager.update(entry["id"], " work ", " ", confirm=True)
    assert result["profile"] == {**entry, "name": "work", "emailLabel": ""}
    assert "user-entered" in result["message"] and "does not verify" in result["message"]
    assert manager.status()["profiles"] == [result["profile"], second]


def test_rename_rejects_duplicate_names_without_moving_profile_data(profiles):
    manager = profiles.manager
    first = manager.create("Work", confirm=True)["profile"]
    second = manager.create("Personal", confirm=True)["profile"]
    path = manager.root / "profiles.json"
    original = path.read_bytes()
    with pytest.raises(ProviderActionError, match="already"):
        manager.update(first["id"], "PERSONAL", "", confirm=True)
    assert path.read_bytes() == original
    assert manager.status()["profiles"] == [first, second]


def test_metadata_never_reads_profile_auth_or_infers_identity(profiles, monkeypatch):
    manager = profiles.manager
    entry = manager.create("Work", confirm=True)["profile"]
    directory = manager.root / "profiles" / entry["id"]
    cookies = directory / "desktop" / "Cookies"
    credentials = directory / "claude-code" / ".credentials.json"
    cookies.write_bytes(b"synthetic-session-data hidden-session@example.com")
    credentials.write_bytes(b"synthetic-cli-auth hidden-cli@example.com")
    before_inode = directory.stat().st_ino
    original_open = Path.open
    original_scan = os.scandir

    def guarded_open(path, *args, **kwargs):
        assert not path.is_relative_to(directory), "Profile contents must never be inspected for labels"
        return original_open(path, *args, **kwargs)

    def guarded_scan(path):
        if not isinstance(path, int):
            assert not Path(path).is_relative_to(directory), "Profile contents must never be scanned for labels"
        return original_scan(path)

    with monkeypatch.context() as guard:
        guard.setattr(Path, "open", guarded_open)
        guard.setattr(os, "scandir", guarded_scan)
        assert manager.status()["profiles"] == [entry]
        scan = Mock(side_effect=AssertionError("Renaming metadata must not inspect app processes"))
        guard.setattr(cd, "running", scan)
        guard.setattr(cd, "installed_executable", lambda: None)
        updated = manager.update(entry["id"], "Office", "typed@example.com", confirm=True)
        scan.assert_not_called()

    assert updated["profile"] == {**entry, "name": "Office", "emailLabel": "typed@example.com"}
    assert "hidden-" not in json.dumps(manager.status())
    assert directory.stat().st_ino == before_inode
    assert cookies.read_bytes() == b"synthetic-session-data hidden-session@example.com"
    assert credentials.read_bytes() == b"synthetic-cli-auth hidden-cli@example.com"
    profiles.launch.assert_not_called()


@pytest.mark.parametrize("confirm", [None, False, 1, "true", [], {}])
def test_management_requires_exact_consent_before_creating_files(profiles, confirm):
    manager = profiles.manager
    with pytest.raises(ProviderActionError, match="Confirm"):
        manager.update("a" * 32, "Work", "", confirm=confirm)
    with pytest.raises(ProviderActionError, match="Confirm"):
        manager.delete("a" * 32, confirm=confirm)
    assert not manager.root.exists()


@pytest.mark.parametrize("profile_id", ["default", None, [], {}, True, 42, "", "../x", "a" * 31, "A" * 32])
def test_management_cannot_target_default_or_untrusted_ids(profiles, profile_id):
    manager = profiles.manager
    with pytest.raises(ProviderActionError, match="Select a saved"):
        manager.update(profile_id, "Work", "", confirm=True)
    with pytest.raises(ProviderActionError, match="Select a saved"):
        manager.delete(profile_id, confirm=True)
    assert not manager.root.exists()


def test_management_never_accepts_unregistered_ids(profiles):
    manager = profiles.manager
    manager.create("Work", confirm=True)
    original = (manager.root / "profiles.json").read_bytes()
    with pytest.raises(ProviderActionError, match="saved list"):
        manager.update("a" * 32, "Office", "", confirm=True)
    with pytest.raises(ProviderActionError, match="saved list"):
        manager.delete("a" * 32, confirm=True)
    assert (manager.root / "profiles.json").read_bytes() == original


def test_management_is_unavailable_on_unsupported_platforms(profiles, monkeypatch):
    manager = profiles.manager
    monkeypatch.setattr(cd.sys, "platform", "win32")
    with pytest.raises(ProviderActionError, match="macOS and Linux"):
        manager.update("a" * 32, "Work", "", confirm=True)
    with pytest.raises(ProviderActionError, match="macOS and Linux"):
        manager.delete("a" * 32, confirm=True)
    status = manager.status()
    assert not status["canManage"] and not status["canDelete"]
    assert not manager.root.exists()


@pytest.mark.parametrize("installed", [True, False])
@pytest.mark.parametrize("active", [True, False, None])
def test_management_readiness_is_independent_of_installation(profiles, monkeypatch, installed, active):
    manager = profiles.manager
    monkeypatch.setattr(cd, "installed_executable", lambda: profiles.executable if installed else None)
    scan = Mock(return_value=active)
    monkeypatch.setattr(cd, "running", scan)
    result = manager.status()
    assert result["canManage"] and result["canCreate"]
    can_delete = active is False and cd.shutil.rmtree.avoids_symlink_attacks
    assert result["canDelete"] is can_delete
    assert result["available"] is (installed and active is not None)
    assert result["running"] is active
    assert bool(result["deleteError"]) is not can_delete
    if not installed:
        assert "To open profiles, install" in result["error"]
    scan.assert_called_once_with()
    assert not manager.root.exists()


def test_missing_installation_message_survives_a_failed_deletion_process_scan(profiles, monkeypatch):
    monkeypatch.setattr(cd, "installed_executable", lambda: None)
    monkeypatch.setattr(cd, "running", Mock(side_effect=ProviderActionError("Unable to check processes")))
    result = profiles.manager.status()
    assert result["canManage"] and not result["canDelete"]
    assert "To open profiles, install" in result["error"]
    assert result["deleteError"] == "Unable to check processes"
    assert result["running"] is None


@POSIX_DELETE
def test_delete_erases_only_the_selected_saved_profile_without_an_installation(profiles, monkeypatch, tmp_path):
    manager = profiles.manager
    selected = manager.create("Delete", emailLabel="typed@example.com", confirm=True)["profile"]
    other = manager.create("Keep", confirm=True)["profile"]
    selected_dir = manager.root / "profiles" / selected["id"]
    (selected_dir / "desktop" / "nested").mkdir()
    (selected_dir / "desktop" / "nested" / "Cookies").write_bytes(b"synthetic-data")
    (selected_dir / "claude-code" / ".credentials.json").write_bytes(b"synthetic-data")
    keep = manager.root / "profiles" / other["id"] / "desktop" / "keep"
    keep.write_bytes(b"keep-other-profile")
    default = tmp_path / "usual-claude-data"
    cli = tmp_path / "cli-account-store"
    default.mkdir()
    cli.mkdir()
    (default / "keep").write_bytes(b"keep-default")
    (cli / "keep").write_bytes(b"keep-cli")
    monkeypatch.setattr(cd, "installed_executable", Mock(side_effect=AssertionError("Deletion must not require installation")))
    scan = Mock(return_value=False)
    monkeypatch.setattr(cd, "running", scan)

    result = manager.delete(selected["id"], confirm=True)
    assert result["ok"] and result["profileId"] == selected["id"]
    scan.assert_called_once_with()
    assert not selected_dir.exists()
    assert manager._read() == [other]
    assert keep.read_bytes() == b"keep-other-profile"
    assert (default / "keep").read_bytes() == b"keep-default"
    assert (cli / "keep").read_bytes() == b"keep-cli"
    assert not list(manager.root.glob(".deleting-*"))
    profiles.launch.assert_not_called()


@POSIX_DELETE
def test_delete_migrates_a_legacy_registry(profiles):
    manager = profiles.manager
    selected = manager.create("Delete", confirm=True)["profile"]
    other = manager.create("Keep", confirm=True)["profile"]
    path = manager.root / "profiles.json"
    path.write_text(json.dumps({"version": 1, "profiles": [
        {"id": profile["id"], "name": profile["name"]} for profile in (selected, other)
    ]}))
    assert manager.delete(selected["id"], confirm=True)["ok"]
    assert json.loads(path.read_text()) == {"version": 2, "profiles": [other]}


@pytest.mark.parametrize("active", [True, None, 0, "", "false"])
def test_running_or_unconfirmed_process_state_blocks_deletion_even_without_installation(profiles, monkeypatch, active):
    manager = profiles.manager
    entry = manager.create("Work", confirm=True)["profile"]
    before = (manager.root / "profiles.json").read_bytes()
    monkeypatch.setattr(cd, "installed_executable", lambda: None)
    scan = Mock(return_value=active)
    monkeypatch.setattr(cd, "running", scan)
    with pytest.raises(ProviderActionError, match="Fully quit|Unable to confirm"):
        manager.delete(entry["id"], confirm=True)
    scan.assert_called_once_with()
    assert (manager.root / "profiles.json").read_bytes() == before
    assert (manager.root / "profiles" / entry["id"] / "desktop").is_dir()
    assert not list(manager.root.glob(".deleting-*"))


def test_failed_process_scan_never_changes_a_profile(profiles, monkeypatch):
    manager = profiles.manager
    entry = manager.create("Work", confirm=True)["profile"]
    original = (manager.root / "profiles.json").read_bytes()
    monkeypatch.setattr(cd, "installed_executable", lambda: None)
    monkeypatch.setattr(cd, "running", Mock(side_effect=ProviderActionError("Unable to check processes")))
    with pytest.raises(ProviderActionError, match="Unable to check"):
        manager.delete(entry["id"], confirm=True)
    assert (manager.root / "profiles.json").read_bytes() == original
    assert (manager.root / "profiles" / entry["id"]).is_dir()
    assert not list(manager.root.glob(".deleting-*"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX profile link safety")
@pytest.mark.parametrize("component", ["root", "registry", "profiles", "profile", "desktop", "claude-code"])
@pytest.mark.parametrize("action", ["update", "delete"])
def test_management_refuses_linked_roots_registries_and_profile_directories(profiles, tmp_path, component, action):
    manager = profiles.manager
    entry = manager.create("Work", confirm=True)["profile"]
    root = manager.root
    original = (root / "profiles.json").read_bytes()
    directory = root / "profiles" / entry["id"]
    paths = {
        "root": root, "registry": root / "profiles.json", "profiles": root / "profiles",
        "profile": directory, "desktop": directory / "desktop", "claude-code": directory / "claude-code",
    }
    path = paths[component]
    destination = tmp_path / "linked-original"
    path.rename(destination)
    path.symlink_to(destination, target_is_directory=destination.is_dir())
    if destination.is_dir():
        (destination / "external-sentinel").write_bytes(b"must-not-change")
    with pytest.raises(ProviderActionError, match="link"):
        if action == "update":
            manager.update(entry["id"], "Office", "typed@example.com", confirm=True)
        else:
            manager.delete(entry["id"], confirm=True)
    assert (root / "profiles.json").read_bytes() == original
    assert path.is_symlink()
    if destination.is_dir():
        assert (destination / "external-sentinel").read_bytes() == b"must-not-change"


@pytest.mark.parametrize("component", ["desktop", "claude-code"])
def test_missing_profile_directories_are_not_recreated_by_management(profiles, component):
    manager = profiles.manager
    entry = manager.create("Work", confirm=True)["profile"]
    missing = manager.root / "profiles" / entry["id"] / component
    missing.rmdir()
    original = (manager.root / "profiles.json").read_bytes()
    with pytest.raises(ProviderActionError, match="missing"):
        manager.update(entry["id"], "Office", "", confirm=True)
    with pytest.raises(ProviderActionError, match="missing"):
        manager.delete(entry["id"], confirm=True)
    assert not missing.exists()
    assert (manager.root / "profiles.json").read_bytes() == original


@POSIX_DELETE
def test_delete_unlinks_nested_links_without_touching_external_children(profiles, tmp_path):
    manager = profiles.manager
    entry = manager.create("Delete", confirm=True)["profile"]
    other = manager.create("Keep", confirm=True)["profile"]
    directory = manager.root / "profiles" / entry["id"]
    external = tmp_path / "external-claude-data"
    external.mkdir()
    (external / "Cookies").write_bytes(b"external-session-must-remain")
    (directory / "desktop" / "external").symlink_to(external, target_is_directory=True)
    (directory / "claude-code" / "credentials-link").symlink_to(external / "Cookies")
    (directory / "desktop" / "broken").symlink_to(tmp_path / "not-present")
    other_dir = manager.root / "profiles" / other["id"]
    (other_dir / "desktop" / "keep").write_bytes(b"other-profile")
    (directory / "desktop" / "other-profile").symlink_to(other_dir, target_is_directory=True)
    os.link(external / "Cookies", directory / "desktop" / "hardlink")

    assert manager.delete(entry["id"], confirm=True)["ok"]
    assert (external / "Cookies").read_bytes() == b"external-session-must-remain"
    assert (other_dir / "desktop" / "keep").read_bytes() == b"other-profile"
    assert not directory.exists()


@POSIX_DELETE
@pytest.mark.parametrize("component", ["root", "profiles"])
def test_delete_refuses_parent_links_swapped_in_during_the_process_check(profiles, monkeypatch, tmp_path, component):
    manager = profiles.manager
    entry = manager.create("Work", confirm=True)["profile"]
    original_registry = (manager.root / "profiles.json").read_bytes()
    external = tmp_path / "external"
    external_directory = external / "profiles" / entry["id"] if component == "root" else external / entry["id"]
    (external_directory / "desktop").mkdir(parents=True)
    (external_directory / "claude-code").mkdir()
    sentinel = external_directory / "desktop" / "Cookies"
    sentinel.write_bytes(b"must-not-delete-external-data")
    source = manager.root if component == "root" else manager.root / "profiles"
    parked = tmp_path / "parked-original"

    def swap_parent():
        source.rename(parked)
        source.symlink_to(external, target_is_directory=True)
        return False

    monkeypatch.setattr(cd, "running", swap_parent)
    try:
        with pytest.raises(ProviderActionError):
            manager.delete(entry["id"], confirm=True)
    finally:
        if source.is_symlink():
            source.unlink()
            parked.rename(source)
    assert sentinel.read_bytes() == b"must-not-delete-external-data"
    assert (manager.root / "profiles.json").read_bytes() == original_registry
    assert (manager.root / "profiles" / entry["id"] / "desktop").is_dir()
    assert not list(external.glob(".deleting-*"))


@POSIX_DELETE
def test_delete_registry_write_and_cleanup_stay_anchored_when_root_moves(profiles, monkeypatch, tmp_path):
    manager = profiles.manager
    entry = manager.create("Work", confirm=True)["profile"]
    root = manager.root
    external = tmp_path / "external"
    external.mkdir()
    (external / "profiles.json").write_bytes(b"external-registry-must-remain")
    (external / "Cookies").write_bytes(b"external-session-must-remain")
    parked = tmp_path / "parked-root"
    original_write = manager._write

    def move_root_before_write(saved, **kwargs):
        root.rename(parked)
        root.symlink_to(external, target_is_directory=True)
        original_write(saved, **kwargs)

    monkeypatch.setattr(manager, "_write", move_root_before_write)
    try:
        result = manager.delete(entry["id"], confirm=True)
    finally:
        if root.is_symlink():
            root.unlink()
            parked.rename(root)
    assert result["ok"]
    assert manager._read() == []
    assert not (root / "profiles" / entry["id"]).exists()
    assert not list(root.glob(".deleting-*"))
    assert (external / "profiles.json").read_bytes() == b"external-registry-must-remain"
    assert (external / "Cookies").read_bytes() == b"external-session-must-remain"
    assert {path.name for path in external.iterdir()} == {"profiles.json", "Cookies"}


@POSIX_DELETE
def test_cleanup_does_not_follow_a_replaced_staging_directory(profiles, monkeypatch, tmp_path):
    manager = profiles.manager
    entry = manager.create("Work", confirm=True)["profile"]
    external = tmp_path / "external"
    (external / "profile").mkdir(parents=True)
    (external / "profile" / "Cookies").write_bytes(b"external-session-must-remain")
    parked = tmp_path / "parked-staging"
    original_write = manager._write

    def replace_staging_after_write(saved, **kwargs):
        original_write(saved, **kwargs)
        [staging] = manager.root.glob(".deleting-*")
        staging.rename(parked)
        staging.symlink_to(external, target_is_directory=True)

    monkeypatch.setattr(manager, "_write", replace_staging_after_write)
    result = manager.delete(entry["id"], confirm=True)
    assert not result["ok"] and result["warning"]
    assert manager._read() == []
    assert not (parked / "profile").exists()
    assert (external / "profile" / "Cookies").read_bytes() == b"external-session-must-remain"


def test_delete_fails_closed_without_symlink_resistant_cleanup(profiles, monkeypatch):
    manager = profiles.manager
    entry = manager.create("Work", confirm=True)["profile"]
    original = (manager.root / "profiles.json").read_bytes()
    monkeypatch.setattr(cd.shutil.rmtree, "avoids_symlink_attacks", False)
    assert not manager.status()["canDelete"]
    assert "safely delete" in manager.status()["deleteError"]
    with pytest.raises(ProviderActionError, match="safely delete"):
        manager.delete(entry["id"], confirm=True)
    assert (manager.root / "profiles.json").read_bytes() == original
    assert not list(manager.root.glob(".deleting-*"))


@POSIX_DELETE
def test_registry_write_failure_rolls_back_staged_profile_without_erasing_data(profiles, monkeypatch):
    manager = profiles.manager
    entry = manager.create("Work", confirm=True)["profile"]
    directory = manager.root / "profiles" / entry["id"]
    (directory / "desktop" / "Cookies").write_bytes(b"synthetic-data")
    before_inode = directory.stat().st_ino
    original = (manager.root / "profiles.json").read_bytes()

    def fail_registry_write(*args, **kwargs):
        assert not directory.exists()
        assert len(list(manager.root.glob(".deleting-*/profile/desktop/Cookies"))) == 1
        raise PermissionError("private-diagnostic")

    monkeypatch.setattr(cd.os, "replace", fail_registry_write)
    cleanup = Mock(avoids_symlink_attacks=True)
    monkeypatch.setattr(cd.shutil, "rmtree", cleanup)
    with pytest.raises(ProviderActionError, match="restored") as error:
        manager.delete(entry["id"], confirm=True)
    assert "private-diagnostic" not in str(error.value)
    assert (manager.root / "profiles.json").read_bytes() == original
    assert directory.stat().st_ino == before_inode
    assert (directory / "desktop" / "Cookies").read_bytes() == b"synthetic-data"
    assert not list(manager.root.glob(".deleting-*"))
    assert not list(manager.root.glob(".profiles-*"))
    cleanup.assert_not_called()


@POSIX_DELETE
def test_failed_staging_keeps_registry_and_original_data(profiles, monkeypatch):
    manager = profiles.manager
    entry = manager.create("Work", confirm=True)["profile"]
    directory = manager.root / "profiles" / entry["id"]
    original = (manager.root / "profiles.json").read_bytes()
    original_rename = os.rename

    def fail_selected_rename(path, target, **kwargs):
        if path == entry["id"] and target == "profile":
            raise PermissionError("private-diagnostic")
        return original_rename(path, target, **kwargs)

    monkeypatch.setattr(cd.os, "rename", fail_selected_rename)
    with pytest.raises(ProviderActionError, match="folder permissions") as error:
        manager.delete(entry["id"], confirm=True)
    assert "private-diagnostic" not in str(error.value)
    assert directory.is_dir()
    assert (manager.root / "profiles.json").read_bytes() == original
    assert not list(manager.root.glob(".deleting-*"))


@POSIX_DELETE
def test_failed_rollback_preserves_staged_data_and_reports_incomplete_state(profiles, monkeypatch):
    manager = profiles.manager
    entry = manager.create("Work", confirm=True)["profile"]
    directory = manager.root / "profiles" / entry["id"]
    (directory / "desktop" / "Cookies").write_bytes(b"synthetic-data")
    original = (manager.root / "profiles.json").read_bytes()
    monkeypatch.setattr(cd.os, "replace", Mock(side_effect=PermissionError("private-diagnostic")))
    original_rename = os.rename

    def fail_rollback(path, target, **kwargs):
        if path == "profile" and target == entry["id"]:
            raise PermissionError("private-diagnostic")
        return original_rename(path, target, **kwargs)

    monkeypatch.setattr(cd.os, "rename", fail_rollback)
    with pytest.raises(ProviderActionError, match="staged data could not be returned") as error:
        manager.delete(entry["id"], confirm=True)
    assert "private-diagnostic" not in str(error.value)
    assert "No profile data was erased" in str(error.value)
    assert (manager.root / "profiles.json").read_bytes() == original
    assert not directory.exists()
    [remaining] = manager.root.glob(".deleting-*/profile/desktop/Cookies")
    assert remaining.read_bytes() == b"synthetic-data"


@POSIX_DELETE
@pytest.mark.parametrize("partial", [False, True])
def test_cleanup_failure_is_an_explicit_warning_not_a_success_or_rollback(profiles, monkeypatch, partial):
    manager = profiles.manager
    entry = manager.create("Work", confirm=True)["profile"]
    directory = manager.root / "profiles" / entry["id"]
    (directory / "desktop" / "Cookies").write_bytes(b"synthetic-data")

    def fail_cleanup(name, *, dir_fd):
        assert name == "profile"
        [path] = manager.root.glob(".deleting-*")
        assert os.path.samestat(path.stat(), os.fstat(dir_fd))
        assert manager._read() == []
        assert not directory.exists()
        assert path.stat().st_mode & 0o777 == 0o700
        if partial:
            (path / "profile" / "claude-code").rmdir()
        raise PermissionError("private-diagnostic")

    cleanup = Mock(side_effect=fail_cleanup, avoids_symlink_attacks=True)
    monkeypatch.setattr(cd.shutil, "rmtree", cleanup)
    result = manager.delete(entry["id"], confirm=True)
    assert result["ok"] is False and result["warning"] is True
    assert result["profileId"] == entry["id"]
    assert "removed from the list" in result["message"] and "not a complete deletion" in result["message"]
    assert "private-diagnostic" not in result["message"]
    assert manager.status()["profiles"] == []
    assert not directory.exists()
    [remaining] = manager.root.glob(".deleting-*/profile/desktop/Cookies")
    assert remaining.read_bytes() == b"synthetic-data"
    cleanup.assert_called_once()


@POSIX_DELETE
def test_update_delete_and_cleanup_share_the_profile_directory_lock(profiles, monkeypatch):
    manager = profiles.manager
    entries = [manager.create(f"Work {number}", confirm=True)["profile"] for number in range(4)]
    original_write = cd.ClaudeDesktopProfiles._write
    original_cleanup = cd.shutil.rmtree

    def checked_write(self, saved, **kwargs):
        assert (self.root / ".lock").is_dir()
        original_write(self, saved, **kwargs)

    def checked_scan():
        assert (manager.root / ".lock").is_dir()
        return False

    def checked_cleanup(path, **kwargs):
        assert (manager.root / ".lock").is_dir()
        return original_cleanup(path, **kwargs)

    def manage(number):
        worker = cd.ClaudeDesktopProfiles(manager.root)
        if number % 2:
            return worker.update(entries[number]["id"], f"Updated {number}", "", confirm=True)
        return worker.delete(entries[number]["id"], confirm=True)

    monkeypatch.setattr(cd.ClaudeDesktopProfiles, "_write", checked_write)
    monkeypatch.setattr(cd, "running", checked_scan)
    monkeypatch.setattr(cd.shutil, "rmtree", Mock(side_effect=checked_cleanup, avoids_symlink_attacks=True))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(manage, range(4)))
    assert all(result["ok"] for result in results)
    assert manager._read() == [{**entries[number], "name": f"Updated {number}"} for number in (1, 3)]
    assert not list(manager.root.glob(".deleting-*"))


@pytest.mark.parametrize("route", ["update", "delete"])
def test_management_http_requires_header_auth_exact_fields_and_consent(web, profiles, route):
    manager = profiles.manager
    entry = manager.create("Work", confirm=True)["profile"]
    web.state.claude_desktop = manager
    payload = {"profileId": entry["id"], "confirm": True}
    if route == "update":
        payload.update({"name": "Office", "emailLabel": "typed@example.com"})
    path = f"/api/claude-desktop/{route}"
    original = (manager.root / "profiles.json").read_bytes()
    assert request(web, path, payload, token=False)[0] == 403
    assert request(web, path + "?token=" + web.token, payload, token=False)[0] == 403
    for field in ("path", "executable", "pid", "email", "emailVerified", "unexpected"):
        assert request(web, path, {**payload, field: "private-value"})[0] == 400
    for confirm in (None, False, 1, "true", [], {}):
        assert request(web, path, {**payload, "confirm": confirm})[0] == 400
    for field in payload:
        assert request(web, path, {key: value for key, value in payload.items() if key != field})[0] == 400
    assert request(web, path, {**payload, "profileId": "default"})[0] == 400
    raw = json.dumps(payload).removesuffix("}") + ',"confirm":true}'
    assert request(web, path, raw=raw.encode())[0] == 400
    assert (manager.root / "profiles.json").read_bytes() == original
    assert web.state._claude.calls == [] and web.state._codex.calls == []
    profiles.launch.assert_not_called()


@POSIX_DELETE
def test_http_profile_management_returns_metadata_and_deletes_only_selected_profile(web, profiles):
    web.state.claude_desktop = profiles.manager
    code, created, _ = request(web, "/api/claude-desktop/create", {
        "name": "Work", "emailLabel": "typed@example.com", "confirm": True,
    })
    assert code == 200 and created["profile"]["emailLabel"] == "typed@example.com"
    profile_id = created["profile"]["id"]
    code, updated, _ = request(web, "/api/claude-desktop/update", {
        "profileId": profile_id, "name": "Office", "emailLabel": "", "confirm": True,
    })
    assert code == 200 and updated["ok"]
    assert updated["profile"] == {"id": profile_id, "name": "Office", "emailLabel": ""}
    code, deleted, _ = request(web, "/api/claude-desktop/delete", {"profileId": profile_id, "confirm": True})
    assert code == 200 and deleted["ok"] and deleted["profileId"] == profile_id
    assert profiles.manager.status()["profiles"] == []
    assert web.state._claude.calls == [] and web.state._codex.calls == []


@POSIX_DELETE
def test_http_reports_cleanup_warning_after_registry_removal(web, profiles, monkeypatch):
    entry = profiles.manager.create("Work", confirm=True)["profile"]
    web.state.claude_desktop = profiles.manager
    monkeypatch.setattr(cd.shutil, "rmtree", Mock(side_effect=PermissionError("private-value"), avoids_symlink_attacks=True))
    code, result, _ = request(web, "/api/claude-desktop/delete", {"profileId": entry["id"], "confirm": True})
    assert code == 200 and result["ok"] is False and result["warning"] is True
    assert "private-value" not in json.dumps(result)
    assert "not a complete deletion" in result["message"]
    assert profiles.manager.status()["profiles"] == []
