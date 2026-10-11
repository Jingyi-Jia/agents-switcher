"""Byte-preserving macOS compaction of an exclusively locked raw VM disk."""

from __future__ import annotations

import errno
import hashlib
import os
import stat
import sys


MAX_IMAGE_BYTES = 16 * 1024**3
_CHUNK_BYTES = 1024**2
_F_PUNCHHOLE = 99
_F_NOCACHE = 48


def _punch_hole(descriptor: int, offset: int, length: int) -> None:
    import fcntl
    import struct

    fcntl.fcntl(descriptor, _F_PUNCHHOLE, struct.pack("=IIqq", 0, 0, offset, length))


def compact_zero_blocks(descriptor: int) -> dict[str, int]:
    if sys.platform != "darwin":
        raise OSError(errno.ENOTSUP, "VM compaction requires macOS.")

    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    before = os.fstat(descriptor)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.getuid()
        or before.st_nlink != 1
        or not 0 < before.st_size <= MAX_IMAGE_BYTES
        or getattr(before, "st_flags", 0) & getattr(stat, "UF_COMPRESSED", 0)
    ):
        raise OSError(errno.EINVAL, "VM disk is not eligible for compaction.")
    block = os.fstatvfs(descriptor).f_frsize
    if not 0 < block <= _CHUNK_BYTES or block & (block - 1):
        raise OSError(errno.ENOTSUP, "Unsupported filesystem block size.")
    fcntl.fcntl(descriptor, _F_NOCACHE, 1)
    expected = before

    def check_source():
        current = os.fstat(descriptor)
        if (
            current.st_dev != before.st_dev
            or current.st_ino != before.st_ino
            or current.st_size != expected.st_size
            or current.st_mtime_ns != expected.st_mtime_ns
            or current.st_ctime_ns != expected.st_ctime_ns
            or current.st_mode != before.st_mode
            or current.st_uid != before.st_uid
            or current.st_gid != before.st_gid
            or current.st_nlink != 1
        ):
            raise OSError(errno.EBUSY, "VM disk changed during compaction.")
        return current

    original = hashlib.sha256()
    zero = bytes(block)
    for offset in range(0, before.st_size, _CHUNK_BYTES):
        check_source()
        length = min(_CHUNK_BYTES, before.st_size - offset)
        chunk = os.pread(descriptor, length, offset)
        if len(chunk) != length:
            raise OSError(errno.EIO, "VM disk could not be read completely.")
        original.update(chunk)
        check_source()
        if os.pread(descriptor, length, offset) != chunk:
            raise OSError(errno.EBUSY, "VM disk changed during compaction.")
        start = None
        for position in range(0, len(chunk) - block + 1, block):
            if chunk[position:position + block] == zero:
                if start is None:
                    start = position
            elif start is not None:
                check_source()
                _punch_hole(descriptor, offset + start, position - start)
                expected = os.fstat(descriptor)
                start = None
        if start is not None:
            check_source()
            _punch_hole(descriptor, offset + start, len(chunk) // block * block - start)
            expected = os.fstat(descriptor)

    os.fsync(descriptor)
    verified = hashlib.sha256()
    for offset in range(0, before.st_size, _CHUNK_BYTES):
        check_source()
        length = min(_CHUNK_BYTES, before.st_size - offset)
        chunk = os.pread(descriptor, length, offset)
        if len(chunk) != length:
            raise OSError(errno.EIO, "VM disk could not be verified completely.")
        verified.update(chunk)
    check_source()
    if original.digest() != verified.digest():
        raise OSError(errno.EIO, "VM disk verification failed.")
    os.utime(descriptor, ns=(before.st_atime_ns, before.st_mtime_ns))
    os.fsync(descriptor)
    after = os.fstat(descriptor)
    return {
        "logicalBytes": before.st_size,
        "beforeBytes": before.st_blocks * 512,
        "afterBytes": after.st_blocks * 512,
        "reclaimedBytes": max(0, (before.st_blocks - after.st_blocks) * 512),
    }
