from __future__ import annotations

import errno
import hashlib
import os
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

if os.name != "nt":
    import fcntl

from agents_switcher import claude_desktop as cd
from agents_switcher import claude_desktop_storage as storage
from agents_switcher.providers import ProviderActionError
from tests.test_claude_desktop import profiles
from tests.test_web_actions import request, web


pytestmark = pytest.mark.skipif(os.name == "nt", reason="macOS filesystem compaction uses POSIX descriptors")


@pytest.fixture
def disk(profiles, monkeypatch):
    profile = profiles.manager.create("Synthetic", confirm=True)["profile"]
    monkeypatch.setattr(cd, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(storage, "sys", SimpleNamespace(platform="darwin"))
    if sys.platform != "darwin":
        native_fcntl = fcntl.fcntl
        monkeypatch.setattr(fcntl, "fcntl", lambda fd, operation, argument=0: 0 if operation == 48 else native_fcntl(fd, operation, argument))
    parent = profiles.manager.root / "profiles" / profile["id"] / "desktop" / "vm_bundles" / "claudevm.bundle"
    parent.mkdir(parents=True)
    path = parent / "rootfs.img"
    data = b"A" * 4096 + bytes(8192) + b"B" * 4096 + bytes(4096) + b"tail"
    path.write_bytes(data)
    path.chmod(0o600)
    calls = []

    def punch(descriptor, offset, length):
        assert offset % 4096 == length % 4096 == 0
        assert os.pread(descriptor, length, offset) == bytes(length)
        calls.append((offset, length))

    monkeypatch.setattr(storage, "_punch_hole", punch)
    return SimpleNamespace(manager=profiles.manager, profile=profile, path=path, data=data, calls=calls,
                           launch=profiles.launch, punch=punch)


def test_compaction_only_targets_zero_blocks_and_preserves_profile_data_and_metadata(disk):
    root = disk.manager.root
    original_registry = (root / "profiles.json").read_bytes()
    unrelated = disk.path.parent / "sessiondata.img"
    unrelated.write_bytes(b"synthetic private guest session data")
    credential = disk.path.parents[3] / "claude-code" / "synthetic-credentials.json"
    credential.write_bytes(b"synthetic provider data must not be opened")
    before = disk.path.stat()
    result = disk.manager.compact(disk.profile["id"], confirm=True)
    after = disk.path.stat()
    assert disk.calls == [(4096, 8192), (16384, 4096)]
    assert disk.path.read_bytes() == disk.data
    assert before.st_ino == after.st_ino and before.st_size == after.st_size
    assert before.st_mode == after.st_mode and before.st_mtime_ns == after.st_mtime_ns
    assert result["ok"] and result["storage"]["logicalBytes"] == len(disk.data)
    assert set(result["storage"]) == {"logicalBytes", "beforeBytes", "afterBytes", "reclaimedBytes"}
    assert "verified unchanged" in result["message"]
    assert (root / "profiles.json").read_bytes() == original_registry
    assert unrelated.read_bytes() == b"synthetic private guest session data"
    assert credential.read_bytes() == b"synthetic provider data must not be opened"
    disk.launch.assert_not_called()


@pytest.mark.parametrize("confirm", [False, None, 1, "true", [], {}])
def test_compaction_requires_literal_confirmation_without_opening_a_disk(disk, confirm):
    with pytest.raises(ProviderActionError, match="Confirm"):
        disk.manager.compact(disk.profile["id"], confirm=confirm)
    assert disk.calls == [] and disk.path.read_bytes() == disk.data


@pytest.mark.parametrize("target", ["default", "../outside", "", None, True, "b" * 32])
def test_only_a_saved_named_profile_can_be_compacted(disk, target):
    with pytest.raises(ProviderActionError):
        disk.manager.compact(target, confirm=True)
    assert disk.calls == []


@pytest.mark.parametrize("running", [True, None, 0, 1, "false"])
def test_compaction_needs_confirmed_process_exit(disk, monkeypatch, running):
    monkeypatch.setattr(cd, "running", lambda: running)
    with pytest.raises(ProviderActionError, match="confirmed quit"):
        disk.manager.compact(disk.profile["id"], confirm=True)
    assert disk.calls == []


def test_unknown_process_scan_blocks_compaction(disk, monkeypatch):
    monkeypatch.setattr(cd, "running", Mock(side_effect=ProviderActionError("Process state is unknown.")))
    with pytest.raises(ProviderActionError, match="unknown"):
        disk.manager.compact(disk.profile["id"], confirm=True)
    assert disk.calls == []


def test_unsupported_platform_does_not_touch_the_disk(disk, monkeypatch):
    monkeypatch.setattr(cd, "sys", SimpleNamespace(platform="linux"))
    with pytest.raises(ProviderActionError, match="macOS only"):
        disk.manager.compact(disk.profile["id"], confirm=True)
    assert disk.calls == []
    assert disk.manager.status()["canCompact"] is False


def test_compaction_capability_is_separate_from_installation_but_requires_readable_labels(disk, monkeypatch):
    monkeypatch.setattr(cd, "installed_executable", lambda: None)
    result = disk.manager.status()
    assert result["canCompact"] is True and result["installed"] is False
    (disk.manager.root / "profiles.json").write_text("invalid")
    assert disk.manager.status()["canCompact"] is False
    with pytest.raises(ProviderActionError, match="invalid"):
        disk.manager.compact(disk.profile["id"], confirm=True)
    assert disk.calls == []


@pytest.mark.parametrize("component", ["rootfs.img", "claudevm.bundle", "vm_bundles", "desktop"])
def test_compaction_refuses_linked_files_and_every_nested_directory(disk, component):
    path = disk.path
    while path.name != component:
        path = path.parent
    moved = path.with_name(path.name + "-original")
    path.rename(moved)
    path.symlink_to(moved, target_is_directory=moved.is_dir())
    with pytest.raises(ProviderActionError):
        disk.manager.compact(disk.profile["id"], confirm=True)
    assert disk.calls == []
    actual = moved if component == "rootfs.img" else moved / disk.path.relative_to(path)
    assert actual.read_bytes() == disk.data


def test_compaction_refuses_hardlinked_disks(disk):
    os.link(disk.path, disk.path.with_name("another.img"))
    with pytest.raises(ProviderActionError, match="linked files"):
        disk.manager.compact(disk.profile["id"], confirm=True)
    assert disk.calls == []


def test_compaction_does_not_create_a_missing_disk(disk):
    disk.path.unlink()
    with pytest.raises(ProviderActionError, match="no local VM disk"):
        disk.manager.compact(disk.profile["id"], confirm=True)
    assert not disk.path.exists()
    assert disk.calls == []


def test_another_file_lock_blocks_scanning_and_mutation(disk):
    descriptor = os.open(disk.path, os.O_RDWR)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ProviderActionError):
            disk.manager.compact(disk.profile["id"], confirm=True)
        assert disk.calls == []
    finally:
        os.close(descriptor)
    assert disk.manager.compact(disk.profile["id"], confirm=True)["ok"]


def test_compaction_serializes_with_the_existing_profile_directory_lock(disk):
    from agents_switcher.dirlock import directory_lock

    with directory_lock(disk.manager.root / ".lock", timeout=1):
        with pytest.raises(ProviderActionError):
            disk.manager.compact(disk.profile["id"], confirm=True)
    assert disk.calls == []


def test_native_punch_failure_keeps_logical_data_and_does_not_return_success(disk, monkeypatch):
    def fail(*args):
        raise OSError(errno.ENOTSUP, "private disk location")

    monkeypatch.setattr(storage, "_punch_hole", fail)
    with pytest.raises(ProviderActionError) as error:
        disk.manager.compact(disk.profile["id"], confirm=True)
    assert "private disk location" not in str(error.value)
    assert disk.path.read_bytes() == disk.data


def test_digest_mismatch_is_never_reported_as_a_verified_success(disk, monkeypatch):
    def incorrect_punch(descriptor, offset, length):
        os.pwrite(descriptor, b"wrong", offset)

    monkeypatch.setattr(storage, "_punch_hole", incorrect_punch)
    with pytest.raises(ProviderActionError, match="verified"):
        disk.manager.compact(disk.profile["id"], confirm=True)


def test_source_changed_during_scan_is_refused(disk, monkeypatch):
    read = os.pread

    def changed(descriptor, length, offset):
        result = read(descriptor, length, offset)
        os.pwrite(descriptor, b"changed", 0)
        return result

    monkeypatch.setattr(storage.os, "pread", changed)
    with pytest.raises(ProviderActionError):
        disk.manager.compact(disk.profile["id"], confirm=True)
    assert disk.calls == []


def test_short_reads_do_not_punch_a_partially_read_range(disk, monkeypatch):
    read = os.pread
    monkeypatch.setattr(storage.os, "pread", lambda descriptor, length, offset: read(descriptor, length - 1, offset))
    with pytest.raises(ProviderActionError):
        disk.manager.compact(disk.profile["id"], confirm=True)
    assert disk.calls == []


def test_oversized_disks_are_refused_before_reading(disk, monkeypatch):
    monkeypatch.setattr(storage, "MAX_IMAGE_BYTES", len(disk.data) - 1)
    with pytest.raises(ProviderActionError):
        disk.manager.compact(disk.profile["id"], confirm=True)
    assert disk.calls == []


@pytest.mark.parametrize("kind", ["empty", "directory", "fifo"])
def test_ineligible_disk_entries_are_never_read_or_punched(disk, kind):
    disk.path.unlink()
    if kind == "empty":
        disk.path.touch()
    elif kind == "directory":
        disk.path.mkdir()
    else:
        os.mkfifo(disk.path)
    with pytest.raises(ProviderActionError):
        disk.manager.compact(disk.profile["id"], confirm=True)
    assert disk.calls == []


@pytest.mark.parametrize("kind", ["owner", "compressed"])
def test_foreign_and_transparently_compressed_files_are_refused_before_scan(disk, monkeypatch, kind):
    original = os.fstat

    def changed(descriptor):
        current = original(descriptor)
        if current.st_ino != disk.path.stat().st_ino:
            return current
        fields = {name: getattr(current, name) for name in ("st_mode", "st_uid", "st_gid", "st_nlink", "st_size")}
        fields["st_uid"] += kind == "owner"
        fields["st_flags"] = 32 if kind == "compressed" else 0
        return SimpleNamespace(**fields)

    monkeypatch.setattr(storage.stat, "UF_COMPRESSED", 32, raising=False)
    monkeypatch.setattr(storage.os, "fstat", changed)
    with pytest.raises(ProviderActionError):
        disk.manager.compact(disk.profile["id"], confirm=True)
    assert disk.calls == []


def test_chunk_boundaries_and_partial_trailing_zero_bytes_preserve_the_full_image(disk):
    data = b"A" * 4096 + bytes(2 * 1024**2) + b"B" * 4096 + bytes(31)
    disk.path.write_bytes(data)
    result = disk.manager.compact(disk.profile["id"], confirm=True)
    assert disk.path.read_bytes() == data
    assert result["storage"]["logicalBytes"] == len(data)
    assert sum(length for offset, length in disk.calls) == 2 * 1024**2
    assert all(offset % 4096 == length % 4096 == 0 for offset, length in disk.calls)


def test_authenticated_http_compaction_uses_the_real_controller_without_provider_collection(web, disk):
    web.state.claude_desktop = disk.manager
    code, result, _ = request(web, "/api/claude-desktop/compact", {"profileId": disk.profile["id"], "confirm": True})
    assert code == 200 and result["ok"]
    assert disk.calls and disk.path.read_bytes() == disk.data
    assert web.state._claude.calls == [] and web.state._codex.calls == []


def test_nonzero_files_and_existing_holes_are_safe_and_repeatable(disk):
    disk.path.write_bytes(b"nonzero" * 1024)
    first = disk.manager.compact(disk.profile["id"], confirm=True)
    second = disk.manager.compact(disk.profile["id"], confirm=True)
    assert first == second and first["storage"]["reclaimedBytes"] == 0
    assert disk.calls == []
    with disk.path.open("wb") as stream:
        stream.truncate(8192)
    result = disk.manager.compact(disk.profile["id"], confirm=True)
    assert result["storage"]["logicalBytes"] == 8192
    assert disk.path.read_bytes() == bytes(8192)


@pytest.mark.skipif(sys.platform != "darwin", reason="requires native APFS hole punching")
def test_native_compaction_reclaims_allocation_without_changing_bytes_or_inode(tmp_path):
    path = tmp_path / "synthetic.img"
    data = os.urandom(1024**2) + bytes(8 * 1024**2) + os.urandom(1024**2)
    path.write_bytes(data)
    path.chmod(0o600)
    before = path.stat()
    descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        try:
            result = storage.compact_zero_blocks(descriptor)
        except OSError as error:
            if error.errno in {errno.ENOTSUP, errno.EOPNOTSUPP}:
                pytest.skip("temporary filesystem does not support APFS hole punching")
            raise
    finally:
        os.close(descriptor)
    assert hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256(data).digest()
    assert before.st_ino == path.stat().st_ino
    assert result["reclaimedBytes"] >= 7 * 1024**2
    assert result["logicalBytes"] == len(data)
