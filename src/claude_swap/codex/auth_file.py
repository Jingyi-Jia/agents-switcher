"""Read and write Codex's ``auth.json`` without losing or corrupting it.

Two hazards shape this module, both created by Codex's own writer
(``FileAuthStorage::save`` in codex-rs/login/src/auth/storage.rs), which opens
the real path with ``truncate(true)`` and writes in place:

1. TORN READS. Between the truncate and the final write the file is empty or
   half-written. A reader arriving in that window sees invalid JSON for a
   credential that is perfectly healthy a millisecond later, so a parse failure
   is retried before it is believed.
2. TRUNCATION ON A FAILED WRITE. An interrupted in-place write leaves the file
   short and the account unrecoverable. On a soft-mounted network home -- an NFS
   cluster home, say -- an I/O timeout does exactly that. Every write here goes
   to a sibling tempfile and is renamed onto the target instead, which is atomic
   on POSIX (rename(2)) and on NFSv3, so a reader sees either generation whole
   and an interrupted write leaves the previous one intact.

THE PASSTHROUGH RULE. Codex's ``auth.json`` carries fields this tool does not
model -- ``auth_mode`` today, more later. Everything here operates on the WHOLE
parsed object and never reconstructs one from known keys, so unmodelled fields
survive a round trip untouched. Modelling the schema instead is what makes a
switcher silently drop ``auth_mode`` on write; do not "tidy" this into a
dataclass with named fields.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

from claude_swap.codex.paths import get_auth_file
from claude_swap.fsutil import replace_with_retry


class CodexAuthError(Exception):
    """``auth.json`` exists but could not be read as a credential object."""


def read_auth(
    path: Path | None = None,
    *,
    attempts: int = 4,
    initial_delay: float = 0.01,
) -> dict | None:
    """Return the parsed ``auth.json``, or ``None`` when there is no file.

    Retries an unparseable or empty read ``attempts`` times before giving up,
    because Codex's in-place writer makes both states a normal, transient view
    of a healthy file rather than evidence of corruption. A file that is still
    unparseable after the budget raises :class:`CodexAuthError` -- callers must
    not treat that as "logged out", which would invite overwriting a credential
    that merely could not be read.

    Raises:
        CodexAuthError: The file exists but never parsed as a JSON object.
    """
    target = Path(path) if path is not None else get_auth_file()
    delay = initial_delay
    last_error: Exception | None = None

    for attempt in range(attempts):
        try:
            raw = target.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as e:
            raise CodexAuthError(f"could not read {target}: {e}") from e

        if raw.strip():
            try:
                data = json.loads(raw)
            except ValueError as e:
                last_error = e
            else:
                if isinstance(data, dict):
                    return data
                last_error = CodexAuthError(
                    f"{target} holds a JSON {type(data).__name__}, not an object"
                )
        else:
            last_error = CodexAuthError(f"{target} is empty")

        if attempt < attempts - 1:
            time.sleep(delay)
            delay = min(delay * 2, 0.2)

    raise CodexAuthError(
        f"{target} is not a readable credential object after {attempts} "
        f"attempts ({last_error}). Refusing to treat it as logged out."
    )


def write_auth(data: dict, path: Path | None = None) -> None:
    """Atomically write ``data`` as Codex's ``auth.json``, mode 0600.

    ``data`` is written verbatim, so any key the caller preserved from a prior
    read survives -- see the passthrough rule in the module docstring.

    The tempfile is created in the TARGET'S OWN directory: ``os.replace`` is
    only atomic within a filesystem, and a temp dir elsewhere would silently
    degrade the rename into a copy on any machine where ``/tmp`` is a different
    mount -- which is the common case, and exactly the machines whose network
    homes made atomicity necessary.
    """
    if not isinstance(data, dict):
        raise TypeError("auth.json content must be a dict")
    target = Path(path) if path is not None else get_auth_file()
    target.parent.mkdir(parents=True, exist_ok=True)

    encoded = json.dumps(data, indent=2).encode("utf-8")
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".auth-", suffix=".tmp")
    try:
        os.write(fd, encoded)
        # fsync before the rename: the rename is atomic with respect to other
        # readers, but without the flush a crash can publish the new name over
        # unwritten blocks and leave a zero-length credential.
        os.fsync(fd)
        os.close(fd)
        fd = -1
        if sys.platform != "win32":
            os.chmod(tmp, 0o600)
        replace_with_retry(tmp, str(target))
    except BaseException:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def has_live_login(auth: dict | None) -> bool:
    """Whether a parsed ``auth.json`` carries any usable credential.

    Mirrors Codex's own ``has_active_login``: either ChatGPT tokens or an API
    key counts. A present-but-null ``OPENAI_API_KEY`` -- the shape a ChatGPT
    login actually writes -- is correctly not a credential.
    """
    if not isinstance(auth, dict):
        return False
    tokens = auth.get("tokens")
    if isinstance(tokens, dict) and tokens.get("access_token"):
        return True
    return bool(auth.get("OPENAI_API_KEY"))
