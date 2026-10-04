from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

import pytest

from agents_switcher import claude_desktop as cd
from agents_switcher.providers import ProviderActionError
from tests.test_claude_desktop import profiles
from tests.test_web_actions import request, web


REMOVED_AT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def _files(directory: Path) -> dict[str, bytes]:
    return {str(path.relative_to(directory)): path.read_bytes() for path in sorted(directory.rglob("*")) if path.is_file()}


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
    assert not status["canManage"] and not status["canCreate"]
    assert status["profiles"] == [] and status["removedProfiles"] == []
    assert "invalid" in status["error"] and "private-value" not in json.dumps(status)
    for action in (
        lambda: manager.create("New", confirm=True),
        lambda: manager.update("a" * 32, "New", "", confirm=True),
        lambda: manager.remove("a" * 32, confirm=True),
        lambda: manager.restore("a" * 32, "New", "", confirm=True),
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
        scan = Mock(side_effect=AssertionError("Label changes must not inspect app processes"))
        guard.setattr(cd, "running", scan)
        guard.setattr(cd, "installed_executable", lambda: None)
        updated = manager.update(entry["id"], "Office", "typed@example.com", confirm=True)
        removed = manager.remove(entry["id"], confirm=True)["profile"]
        guard.setattr(cd, "running", lambda: False)
        assert manager.status()["removedProfiles"] == [removed]
        guard.setattr(cd, "running", scan)
        restored = manager.restore(entry["id"], "Office", "typed@example.com", confirm=True)
        scan.assert_not_called()

    assert updated["profile"] == {**entry, "name": "Office", "emailLabel": "typed@example.com"}
    assert restored["profile"] == updated["profile"]
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
        manager.remove("a" * 32, confirm=confirm)
    with pytest.raises(ProviderActionError, match="Confirm"):
        manager.restore("a" * 32, "Work", "", confirm=confirm)
    assert not manager.root.exists()


@pytest.mark.parametrize("profile_id", ["default", None, [], {}, True, 42, "", "../x", "a" * 31, "A" * 32])
def test_management_cannot_target_default_or_untrusted_ids(profiles, profile_id):
    manager = profiles.manager
    with pytest.raises(ProviderActionError, match="Select a saved"):
        manager.update(profile_id, "Work", "", confirm=True)
    with pytest.raises(ProviderActionError, match="Select a saved"):
        manager.remove(profile_id, confirm=True)
    with pytest.raises(ProviderActionError, match="Select a removed"):
        manager.restore(profile_id, "Work", "", confirm=True)
    assert not manager.root.exists()


def test_management_never_accepts_unregistered_ids(profiles):
    manager = profiles.manager
    manager.create("Work", confirm=True)
    original = (manager.root / "profiles.json").read_bytes()
    with pytest.raises(ProviderActionError, match="saved list"):
        manager.update("a" * 32, "Office", "", confirm=True)
    with pytest.raises(ProviderActionError, match="saved list"):
        manager.remove("a" * 32, confirm=True)
    with pytest.raises(ProviderActionError, match="removed list"):
        manager.restore("a" * 32, "Office", "", confirm=True)
    assert (manager.root / "profiles.json").read_bytes() == original
    assert not (manager.root / "removed-profiles.json").exists()


def test_management_is_unavailable_on_unsupported_platforms(profiles, monkeypatch):
    manager = profiles.manager
    monkeypatch.setattr(cd.sys, "platform", "win32")
    with pytest.raises(ProviderActionError, match="macOS and Linux"):
        manager.update("a" * 32, "Work", "", confirm=True)
    with pytest.raises(ProviderActionError, match="macOS and Linux"):
        manager.remove("a" * 32, confirm=True)
    with pytest.raises(ProviderActionError, match="macOS and Linux"):
        manager.restore("a" * 32, "Work", "", confirm=True)
    status = manager.status()
    assert not status["canManage"] and status["removedProfiles"] == []
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
    assert "canDelete" not in result and "deleteError" not in result
    assert result["available"] is (installed and active is not None)
    assert result["running"] is active
    assert result["removedProfiles"] == [] and result["removedError"] is None
    if not installed:
        assert "To open profiles, install" in result["error"]
    scan.assert_called_once_with()
    assert not manager.root.exists()


def test_missing_installation_message_survives_a_failed_process_scan(profiles, monkeypatch):
    monkeypatch.setattr(cd, "installed_executable", lambda: None)
    monkeypatch.setattr(cd, "running", Mock(side_effect=ProviderActionError("Unable to check processes")))
    result = profiles.manager.status()
    assert result["canManage"] and not result["available"]
    assert "To open profiles, install" in result["error"]
    assert result["running"] is None


@pytest.mark.skipif(os.name == "nt", reason="POSIX profile link safety")
@pytest.mark.parametrize("action,component", [
    *[("update", component) for component in ("root", "registry", "profiles", "profile", "desktop", "claude-code")],
    *[("restore", component) for component in ("root", "registry", "removed", "profiles", "profile", "desktop", "claude-code")],
    # Removal only rewrites the two label files, so only links among those (or the root) can stop it.
    *[("remove", component) for component in ("root", "registry", "removed")],
])
def test_management_refuses_linked_roots_registries_and_profile_directories(profiles, tmp_path, action, component):
    manager = profiles.manager
    entry = manager.create("Work", confirm=True)["profile"]
    if action == "restore":
        manager.remove(entry["id"], confirm=True)
    root = manager.root
    if action != "restore":
        (root / "removed-profiles.json").write_text(json.dumps({"version": 1, "profiles": []}))
    original = (root / "profiles.json").read_bytes()
    removed = (root / "removed-profiles.json").read_bytes()
    directory = root / "profiles" / entry["id"]
    paths = {
        "root": root, "registry": root / "profiles.json", "removed": root / "removed-profiles.json",
        "profiles": root / "profiles", "profile": directory,
        "desktop": directory / "desktop", "claude-code": directory / "claude-code",
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
        elif action == "remove":
            manager.remove(entry["id"], confirm=True)
        else:
            manager.restore(entry["id"], "Office", "", confirm=True)
    assert (root / "profiles.json").read_bytes() == original
    assert (root / "removed-profiles.json").read_bytes() == removed
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
    assert not missing.exists()
    assert (manager.root / "profiles.json").read_bytes() == original


@pytest.mark.parametrize("component", [
    "missing-desktop", "missing-claude-code",
    pytest.param("linked-profiles", marks=pytest.mark.skipif(os.name == "nt", reason="POSIX link safety")),
    pytest.param("linked-profile", marks=pytest.mark.skipif(os.name == "nt", reason="POSIX link safety")),
    pytest.param("linked-desktop", marks=pytest.mark.skipif(os.name == "nt", reason="POSIX link safety")),
])
def test_remove_takes_a_broken_profile_off_the_list_without_following_links(profiles, tmp_path, component):
    manager = profiles.manager
    entry = manager.create("Broken", confirm=True)["profile"]
    kept = manager.create("Keep", confirm=True)["profile"]
    directory = manager.root / "profiles" / entry["id"]
    kind, name = component.split("-", 1)
    path = {"profiles": manager.root / "profiles", "profile": directory}.get(name, directory / name)
    destination = tmp_path / "linked-original"
    if kind == "missing":
        path.rmdir()
    else:
        path.rename(destination)
        path.symlink_to(destination, target_is_directory=True)
        (destination / "external-sentinel").write_bytes(b"must-not-change")

    record = manager.remove(entry["id"], confirm=True)["profile"]
    assert manager._read() == [kept]
    assert json.loads((manager.root / "removed-profiles.json").read_text())["profiles"] == [record]
    assert manager.status()["removedProfiles"] == []
    with pytest.raises(ProviderActionError, match="missing|link"):
        manager.restore(entry["id"], "Broken", "", confirm=True)
    if kind == "missing":
        assert not path.exists()
    else:
        assert path.is_symlink()
        assert (destination / "external-sentinel").read_bytes() == b"must-not-change"


@pytest.mark.parametrize("route", ["update", "remove", "restore"])
def test_management_http_requires_header_auth_exact_fields_and_consent(web, profiles, route):
    manager = profiles.manager
    entry = manager.create("Work", confirm=True)["profile"]
    web.state.claude_desktop = manager
    payload = {"profileId": entry["id"], "confirm": True}
    if route == "restore":
        manager.remove(entry["id"], confirm=True)
    if route in {"update", "restore"}:
        payload.update({"name": "Office", "emailLabel": "typed@example.com"})
    path = f"/api/claude-desktop/{route}"
    original = (manager.root / "profiles.json").read_bytes()
    removed = manager.status()["removedProfiles"]
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
    assert manager.status()["removedProfiles"] == removed
    assert web.state._claude.calls == [] and web.state._codex.calls == []
    profiles.launch.assert_not_called()


def test_remove_keeps_every_file_and_offers_the_profile_for_restore(profiles, monkeypatch):
    manager = profiles.manager
    entry = manager.create("Work", emailLabel="Work@Example.com", confirm=True)["profile"]
    kept = manager.create("Keep", confirm=True)["profile"]
    directory = manager.root / "profiles" / entry["id"]
    (directory / "desktop" / "Cookies").write_bytes(b"synthetic-session")
    (directory / "claude-code" / "projects").mkdir()
    (directory / "claude-code" / "projects" / "chat.jsonl").write_bytes(b"synthetic-history")
    before, inode = _files(directory), directory.stat().st_ino
    with monkeypatch.context() as guard:
        guard.setattr(cd, "running", Mock(side_effect=AssertionError("Removal must not depend on Claude's process state")))
        guard.setattr(cd, "installed_executable", Mock(side_effect=AssertionError("Removal must not require installation")))
        result = manager.remove(entry["id"], confirm=True)

    record = result["profile"]
    assert result["ok"] and set(record) == {"id", "name", "emailLabel", "removedAt"}
    assert {key: record[key] for key in ("id", "name", "emailLabel")} == entry
    assert REMOVED_AT.fullmatch(record["removedAt"])
    assert "stays on this computer" in result["message"]
    assert json.loads((manager.root / "profiles.json").read_text()) == {"version": 2, "profiles": [kept]}
    assert json.loads((manager.root / "removed-profiles.json").read_text()) == {"version": 1, "profiles": [record]}
    assert _files(directory) == before and directory.stat().st_ino == inode
    status = manager.status()
    assert status["profiles"] == [kept] and status["removedProfiles"] == [record]
    assert status["removedError"] is None
    assert not list(manager.root.glob(".*profiles-*"))
    profiles.launch.assert_not_called()


def test_restore_brings_back_the_same_folder_under_new_labels(profiles):
    manager = profiles.manager
    entry = manager.create("Work", emailLabel="work@example.com", confirm=True)["profile"]
    directory = manager.root / "profiles" / entry["id"]
    (directory / "desktop" / "Cookies").write_bytes(b"synthetic-session")
    before = _files(directory)
    manager.remove(entry["id"], confirm=True)

    result = manager.restore(entry["id"], " Lab ", " WORK@example.com ", confirm=True)
    restored = {"id": entry["id"], "name": "Lab", "emailLabel": "WORK@example.com"}
    assert result["ok"] and result["profile"] == restored
    status = manager.status()
    assert status["profiles"] == [restored] and status["removedProfiles"] == []
    assert json.loads((manager.root / "removed-profiles.json").read_text()) == {"version": 1, "profiles": []}
    assert _files(directory) == before
    manager.open(entry["id"], confirm=True)
    args, kwargs = profiles.launch.call_args
    assert f"--user-data-dir={directory / 'desktop'}" in args[0]
    assert kwargs["env"]["CLAUDE_CONFIG_DIR"] == str(directory / "claude-code")


def test_remove_migrates_a_legacy_registry(profiles):
    manager = profiles.manager
    selected = manager.create("Work", confirm=True)["profile"]
    other = manager.create("Keep", confirm=True)["profile"]
    path = manager.root / "profiles.json"
    path.write_text(json.dumps({"version": 1, "profiles": [
        {"id": profile["id"], "name": profile["name"]} for profile in (selected, other)
    ]}))
    assert manager.remove(selected["id"], confirm=True)["profile"]["emailLabel"] == ""
    assert json.loads(path.read_text()) == {"version": 2, "profiles": [other]}


def test_removed_profiles_are_newest_first_and_hidden_once_their_folder_is_gone(profiles):
    manager = profiles.manager
    entries = [manager.create(name, confirm=True)["profile"] for name in ("Old", "New", "Gone")]
    records = [{**entry, "removedAt": stamp} for entry, stamp in zip(entries, (
        "2026-09-01T08:00:00Z", "2026-10-01T08:00:00Z", "2026-10-02T08:00:00Z",
    ))]
    (manager.root / "profiles.json").write_text(json.dumps({"version": 2, "profiles": []}))
    (manager.root / "removed-profiles.json").write_text(json.dumps({"version": 1, "profiles": records}))
    (manager.root / "profiles" / entries[2]["id"] / "desktop").rmdir()
    assert manager.status()["removedProfiles"] == [records[1], records[0]]
    with pytest.raises(ProviderActionError, match="missing"):
        manager.restore(entries[2]["id"], "Gone", "", confirm=True)


def test_removals_in_the_same_second_list_the_later_one_first(profiles):
    manager = profiles.manager
    entries = [manager.create(name, emailLabel="same@example.com", confirm=True)["profile"] for name in ("First", "Second")]
    records = [{**entry, "removedAt": "2026-10-01T08:00:00Z"} for entry in entries]
    (manager.root / "profiles.json").write_text(json.dumps({"version": 2, "profiles": []}))
    (manager.root / "removed-profiles.json").write_text(json.dumps({"version": 1, "profiles": records}))
    assert manager.status()["removedProfiles"] == [records[1], records[0]]


def test_removed_order_follows_the_list_not_the_clock(profiles):
    manager = profiles.manager
    entries = [manager.create(name, confirm=True)["profile"] for name in ("First", "Second")]
    # The clock was set back between the two removals, so the later one carries the earlier time.
    records = [{**entries[0], "removedAt": "2026-10-02T08:00:00Z"}, {**entries[1], "removedAt": "2026-10-01T08:00:00Z"}]
    (manager.root / "profiles.json").write_text(json.dumps({"version": 2, "profiles": []}))
    (manager.root / "removed-profiles.json").write_text(json.dumps({"version": 1, "profiles": records}))
    assert manager.status()["removedProfiles"] == [records[1], records[0]]


def test_a_failed_restore_keeps_the_profile_in_the_removed_list(profiles, monkeypatch):
    manager = profiles.manager
    entry = manager.create("Work", confirm=True)["profile"]
    record = manager.remove(entry["id"], confirm=True)["profile"]
    files = [manager.root / "profiles.json", manager.root / "removed-profiles.json"]
    before = [path.read_bytes() for path in files]
    with monkeypatch.context() as failing:
        failing.setattr(cd.ClaudeDesktopProfiles, "_write", Mock(side_effect=OSError("private-diagnostic")))
        with pytest.raises(ProviderActionError) as error:
            manager.restore(entry["id"], "Work", "", confirm=True)
    assert "private-diagnostic" not in str(error.value)
    assert [path.read_bytes() for path in files] == before
    assert manager.status()["removedProfiles"] == [record]


def test_the_active_list_wins_over_a_stale_removed_entry(profiles):
    manager = profiles.manager
    entry = manager.create("Work", confirm=True)["profile"]
    stale = {**entry, "removedAt": "2026-10-01T08:00:00Z"}
    (manager.root / "removed-profiles.json").write_text(json.dumps({"version": 1, "profiles": [stale]}))
    assert manager.status()["removedProfiles"] == []
    with pytest.raises(ProviderActionError, match="removed list"):
        manager.restore(entry["id"], "Office", "", confirm=True)
    record = manager.remove(entry["id"], confirm=True)["profile"]
    assert json.loads((manager.root / "removed-profiles.json").read_text())["profiles"] == [record]


@pytest.mark.parametrize("case", ["unknown", "active", "duplicate", "full", "name", "email"])
def test_restore_refuses_invalid_targets_without_changes(profiles, case):
    manager = profiles.manager
    entry = manager.create("Work", emailLabel="work@example.com", confirm=True)["profile"]
    active = manager.create("Office", confirm=True)["profile"]
    manager.remove(entry["id"], confirm=True)
    if case == "full":
        registry = json.loads((manager.root / "profiles.json").read_text())
        registry["profiles"] += [
            {"id": f"{number:032x}", "name": f"Filler {number}", "emailLabel": ""}
            for number in range(cd._MAX_PROFILES - 1)
        ]
        (manager.root / "profiles.json").write_text(json.dumps(registry))
    files = [manager.root / "profiles.json", manager.root / "removed-profiles.json"]
    before = [path.read_bytes() for path in files]
    target, name, email, error = {
        "unknown": ("c" * 32, "Lab", "", "removed list"),
        "active": (active["id"], "Lab", "", "removed list"),
        "duplicate": (entry["id"], "OFFICE", "", "already"),
        "full": (entry["id"], "Lab", "", "limit"),
        "name": (entry["id"], "Lab\n", "", "profile name"),
        "email": (entry["id"], "Lab", "not-an-email", "email label"),
    }[case]
    with pytest.raises(ProviderActionError, match=error):
        manager.restore(target, name, email, confirm=True)
    assert [path.read_bytes() for path in files] == before


def test_remove_rolls_back_when_the_registry_cannot_be_saved(profiles, monkeypatch):
    manager = profiles.manager
    entry = manager.create("Work", confirm=True)["profile"]
    registry = (manager.root / "profiles.json").read_bytes()
    with monkeypatch.context() as failing:
        failing.setattr(cd.ClaudeDesktopProfiles, "_write", Mock(side_effect=OSError("private-diagnostic")))
        with pytest.raises(ProviderActionError, match="could not be saved") as error:
            manager.remove(entry["id"], confirm=True)
    assert "private-diagnostic" not in str(error.value)
    assert (manager.root / "profiles.json").read_bytes() == registry
    assert json.loads((manager.root / "removed-profiles.json").read_text()) == {"version": 1, "profiles": []}
    status = manager.status()
    assert status["profiles"] == [entry] and status["removedProfiles"] == []
    assert not list(manager.root.glob(".*profiles-*"))


def test_remove_refuses_when_the_removed_list_would_outgrow_the_registry_limit(profiles, monkeypatch):
    manager = profiles.manager
    entry = manager.create("Work", confirm=True)["profile"]
    registry = (manager.root / "profiles.json").read_bytes()
    monkeypatch.setattr(cd, "_MAX_REGISTRY_BYTES", len(registry) + 10)
    with pytest.raises(ProviderActionError, match="Too many"):
        manager.remove(entry["id"], confirm=True)
    assert (manager.root / "profiles.json").read_bytes() == registry
    assert not (manager.root / "removed-profiles.json").exists()
    assert not list(manager.root.glob(".*profiles-*"))


def test_restore_succeeds_even_if_the_removed_list_cannot_be_rewritten(profiles, monkeypatch):
    manager = profiles.manager
    entry = manager.create("Work", confirm=True)["profile"]
    manager.remove(entry["id"], confirm=True)
    with monkeypatch.context() as failing:
        failing.setattr(cd.ClaudeDesktopProfiles, "_write_removed", Mock(side_effect=OSError("private-diagnostic")))
        assert manager.restore(entry["id"], "Work", "", confirm=True)["ok"]
    status = manager.status()
    assert status["profiles"] == [entry] and status["removedProfiles"] == []


@pytest.mark.parametrize("raw", [
    b'{"version":1,"profiles":[],"token":"private-value"}',
    b'{"version":2,"profiles":[]}',
    b'{"version":1,"profiles":[{"id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","name":"Work","emailLabel":"",'
    b'"removedAt":"2026-10-01T08:00:00Z","removedAt":"private-value"}]}',
    b'{"version":1,"profiles":[{"id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","name":"Work","emailLabel":"",'
    b'"removedAt":"2026-10-01T08:00:00Z","token":"private-value"}]}',
    b'{"version":1,"profiles":[{"id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","name":"Work","emailLabel":"","removedAt":"yesterday"}]}',
    b'{"version":1,"profiles":[{"id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","name":"Work","emailLabel":"","removedAt":"2026-13-45T08:00:00Z"}]}',
    b'{"version":1,"profiles":[{"id":"../elsewhere","name":"Work","emailLabel":"","removedAt":"2026-10-01T08:00:00Z"}]}',
    b'{"version":1,"profiles":[{"id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","name":"Work","emailLabel":"not-an-email",'
    b'"removedAt":"2026-10-01T08:00:00Z"}]}',
    b'{"version":1,"profiles":[{"id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","name":"A","emailLabel":"","removedAt":"2026-10-01T08:00:00Z"},'
    b'{"id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","name":"B","emailLabel":"","removedAt":"2026-10-01T08:00:00Z"}]}',
    b"",
    pytest.param(b" " * (cd._MAX_REGISTRY_BYTES + 1), id="oversized"),
])
def test_an_invalid_removed_list_is_reported_and_never_overwritten(profiles, raw):
    manager = profiles.manager
    entry = manager.create("Work", confirm=True)["profile"]
    path = manager.root / "removed-profiles.json"
    path.write_bytes(raw)
    registry = (manager.root / "profiles.json").read_bytes()
    status = manager.status()
    assert status["canManage"] and status["profiles"] == [entry] and status["removedProfiles"] == []
    assert "invalid" in status["removedError"] and "private-value" not in json.dumps(status)
    with pytest.raises(ProviderActionError, match="invalid"):
        manager.remove(entry["id"], confirm=True)
    with pytest.raises(ProviderActionError, match="invalid"):
        manager.restore("a" * 32, "Lab", "", confirm=True)
    assert path.read_bytes() == raw
    assert (manager.root / "profiles.json").read_bytes() == registry


def test_update_remove_and_restore_share_the_profile_directory_lock(profiles, monkeypatch):
    manager = profiles.manager
    entries = [manager.create(f"Work {number}", confirm=True)["profile"] for number in range(6)]
    for number in (4, 5):
        manager.remove(entries[number]["id"], confirm=True)

    def locked(write):
        def checked(self, saved, **kwargs):
            assert (self.root / ".lock").is_dir()
            write(self, saved, **kwargs)
        return checked

    def manage(number):
        worker = cd.ClaudeDesktopProfiles(manager.root)
        if number >= 4:
            return worker.restore(entries[number]["id"], f"Restored {number}", "", confirm=True)
        if number % 2:
            return worker.update(entries[number]["id"], f"Updated {number}", "", confirm=True)
        return worker.remove(entries[number]["id"], confirm=True)

    monkeypatch.setattr(cd.ClaudeDesktopProfiles, "_write", locked(cd.ClaudeDesktopProfiles._write))
    monkeypatch.setattr(cd.ClaudeDesktopProfiles, "_write_removed", locked(cd.ClaudeDesktopProfiles._write_removed))
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(manage, range(6)))
    assert all(result["ok"] for result in results)
    assert {profile["id"]: profile["name"] for profile in manager._read()} == {
        entries[1]["id"]: "Updated 1", entries[3]["id"]: "Updated 3",
        entries[4]["id"]: "Restored 4", entries[5]["id"]: "Restored 5",
    }
    assert {record["id"] for record in manager.status()["removedProfiles"]} == {entries[0]["id"], entries[2]["id"]}


def test_http_profile_management_removes_and_restores_without_deleting(web, profiles):
    web.state.claude_desktop = profiles.manager
    code, created, _ = request(web, "/api/claude-desktop/create", {
        "name": "Work", "emailLabel": "typed@example.com", "confirm": True,
    })
    assert code == 200 and created["profile"]["emailLabel"] == "typed@example.com"
    profile_id = created["profile"]["id"]
    code, updated, _ = request(web, "/api/claude-desktop/update", {
        "profileId": profile_id, "name": "Office", "emailLabel": "", "confirm": True,
    })
    assert code == 200 and updated["profile"] == {"id": profile_id, "name": "Office", "emailLabel": ""}
    code, removed, _ = request(web, "/api/claude-desktop/remove", {"profileId": profile_id, "confirm": True})
    assert code == 200 and removed["ok"] and removed["profile"]["id"] == profile_id
    assert profiles.manager.status()["profiles"] == []
    assert request(web, "/api/claude-desktop/delete", {"profileId": profile_id, "confirm": True})[0] == 404
    code, restored, _ = request(web, "/api/claude-desktop/restore", {
        "profileId": profile_id, "name": "Lab", "emailLabel": "typed@example.com", "confirm": True,
    })
    assert code == 200 and restored["profile"] == {"id": profile_id, "name": "Lab", "emailLabel": "typed@example.com"}
    assert (profiles.manager.root / "profiles" / profile_id / "desktop").is_dir()
    assert web.state._claude.calls == [] and web.state._codex.calls == []
