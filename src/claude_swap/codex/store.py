"""The roster of managed Codex accounts and their credential backups.

Two pieces of state, both under ``<backup root>/codex/``:

* ``accounts.json`` -- the roster: which slot is which account, and which slot
  is active. Identity only; never a credential.
* ``credentials/<slot>.json`` -- one saved ``auth.json`` per slot.

WHY THE LOCK IS A DIRECTORY. Every mutation here runs under
:func:`claude_swap.dirlock.directory_lock`, not the ``FileLock`` the Claude side
uses. ``FileLock`` is ``fcntl.flock``, which a home mounted
``nolock,local_lock=all`` -- the common NFS cluster export -- resolves on the
client without ever consulting the server. Measured on such a home, two nodes
contending for 20s: flock took 2829 "locks" with 2653 critical-section
violations and never once observed contention, while mkdir took 1088 with zero
violations. On a shared home the roster is exactly the file two login nodes
would corrupt, so it gets the primitive that actually excludes.

WHY THE ROSTER READ IS STRICT. A torn ``accounts.json`` must raise rather than
read as "no accounts": the Claude side learned this the hard way -- a torn
roster read as empty, and the next add rebuilt it from nothing, overwriting a
live credential backup. Callers here get an exception instead of a silent empty
roster for the same reason.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from claude_swap.codex.identity import CodexIdentity
from claude_swap.codex.paths import (
    get_accounts_file,
    get_codex_backup_root,
    get_codex_credentials_dir,
)
from claude_swap.dirlock import directory_lock
from claude_swap.exceptions import ConfigError
from claude_swap.fsutil import replace_with_retry
from claude_swap.models import get_timestamp

#: Bumped only for a change that older versions cannot read.
ROSTER_VERSION = 1


@dataclass(frozen=True)
class CodexAccount:
    """One managed Codex account, as recorded in the roster."""

    number: str
    email: str
    account_id: str
    plan: str = ""
    added: str = ""
    alias: str = ""
    disabled: bool = False

    @property
    def display_label(self) -> str:
        """``email [plan]``, falling back to the account id."""
        name = self.email or (
            f"account {self.account_id[:8]}" if self.account_id else "unknown"
        )
        return f"{name} [{self.plan}]" if self.plan else name

    @property
    def identity(self) -> CodexIdentity:
        """This account's identity, for comparison against a live credential."""
        return CodexIdentity(
            email=self.email, account_id=self.account_id, plan=self.plan
        )

    @classmethod
    def from_dict(cls, number: str, data: dict) -> CodexAccount:
        return cls(
            number=str(number),
            email=data.get("email", "") or "",
            account_id=data.get("accountId", "") or "",
            plan=data.get("plan", "") or "",
            added=data.get("added", "") or "",
            alias=data.get("alias", "") or "",
            disabled=bool(data.get("disabled", False)),
        )

    def to_dict(self) -> dict:
        return {
            "email": self.email,
            "accountId": self.account_id,
            "plan": self.plan,
            "added": self.added,
            "alias": self.alias,
            "disabled": self.disabled,
        }


class CodexAccountStore:
    """Reads and writes the Codex roster and per-slot credential backups.

    Construct with an explicit ``root`` in tests; production callers let it
    resolve from the shared backup root.
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else get_codex_backup_root()
        self.accounts_file = (
            get_accounts_file() if root is None else self.root / "accounts.json"
        )
        self.credentials_dir = (
            get_codex_credentials_dir() if root is None else self.root / "credentials"
        )
        self.lock_dir = self.root / ".lock"

    # -- locking ---------------------------------------------------------

    @contextmanager
    def lock(self, *, timeout: float = 9.0):
        """Hold the store's mutation lock. Not reentrant."""
        self.root.mkdir(parents=True, exist_ok=True)
        with directory_lock(self.lock_dir, timeout=timeout):
            yield

    # -- roster ----------------------------------------------------------

    def _read_roster(self) -> dict:
        """Return the roster, or an empty one when the file does not exist.

        Raises:
            ConfigError: The file exists but is not a readable roster object.
                Deliberately NOT returned as an empty roster -- see the module
                docstring for the data loss that conflation causes.
        """
        try:
            raw = self.accounts_file.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {"version": ROSTER_VERSION, "accounts": {}}
        except OSError as e:
            raise ConfigError(f"could not read {self.accounts_file}: {e}") from e

        try:
            data = json.loads(raw)
        except ValueError as e:
            raise ConfigError(
                f"{self.accounts_file} is not valid JSON ({e}). Refusing to treat "
                "it as an empty roster — that would overwrite managed accounts."
            ) from e
        if not isinstance(data, dict):
            raise ConfigError(f"{self.accounts_file} is not a roster object")
        data.setdefault("accounts", {})
        if not isinstance(data["accounts"], dict):
            raise ConfigError(f"{self.accounts_file} has a malformed accounts map")
        return data

    def _write_roster(self, data: dict) -> None:
        """Atomically publish the roster (0600).

        Validates the encoded form before the rename so a serialization bug
        cannot publish a roster that the strict reader above will then refuse.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        data["version"] = ROSTER_VERSION
        data["lastUpdated"] = get_timestamp()
        content = json.dumps(data, indent=2)
        json.loads(content)  # never publish what the reader would reject

        fd, tmp = tempfile.mkstemp(dir=str(self.root), prefix=".accounts-", suffix=".tmp")
        try:
            os.write(fd, content.encode("utf-8"))
            os.fsync(fd)
            os.close(fd)
            fd = -1
            if sys.platform != "win32":
                os.chmod(tmp, 0o600)
            replace_with_retry(tmp, str(self.accounts_file))
        except BaseException:
            if fd >= 0:
                os.close(fd)
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def accounts(self) -> dict[str, CodexAccount]:
        """Every managed account, keyed by slot number."""
        roster = self._read_roster()
        return {
            str(num): CodexAccount.from_dict(num, data)
            for num, data in sorted(
                roster["accounts"].items(), key=lambda kv: _slot_sort_key(kv[0])
            )
            if isinstance(data, dict)
        }

    def get(self, number: str) -> CodexAccount | None:
        return self.accounts().get(str(number))

    def find_by_account_id(self, account_id: str) -> CodexAccount | None:
        """The slot holding ``account_id``, if any.

        An empty ``account_id`` matches nothing: it means "unknown identity",
        and treating it as a match would let one account's credential be filed
        against another's slot.
        """
        if not account_id:
            return None
        for account in self.accounts().values():
            if account.account_id == account_id:
                return account
        return None

    def active_number(self) -> str | None:
        value = self._read_roster().get("activeAccountNumber")
        return str(value) if value is not None else None

    def set_active(self, number: str | None) -> None:
        roster = self._read_roster()
        roster["activeAccountNumber"] = str(number) if number is not None else None
        self._write_roster(roster)

    def next_number(self) -> str:
        """The lowest unused positive slot number.

        Reuses gaps rather than always appending, so removing slot 2 of three
        does not leave the roster permanently sparse.
        """
        taken = {int(n) for n in self._read_roster()["accounts"] if str(n).isdigit()}
        candidate = 1
        while candidate in taken:
            candidate += 1
        return str(candidate)

    def add(
        self,
        identity: CodexIdentity,
        credentials: dict,
        *,
        number: str | None = None,
        alias: str = "",
    ) -> CodexAccount:
        """Record ``identity`` in a slot and save ``credentials`` beside it.

        The credential is written FIRST: a roster entry pointing at a missing
        backup is a slot that cannot be switched to, while an orphaned backup
        with no roster entry is inert and reclaimable.
        """
        roster = self._read_roster()
        slot = str(number) if number is not None else self.next_number()
        account = CodexAccount(
            number=slot,
            email=identity.email,
            account_id=identity.account_id,
            plan=identity.plan,
            added=get_timestamp(),
            alias=alias,
        )
        self.write_credentials(slot, credentials)
        roster["accounts"][slot] = account.to_dict()
        self._write_roster(roster)
        return account

    def remove(self, number: str) -> CodexAccount | None:
        """Drop a slot and its credential backup. Returns the removed account."""
        roster = self._read_roster()
        slot = str(number)
        data = roster["accounts"].pop(slot, None)
        if data is None:
            return None
        if str(roster.get("activeAccountNumber")) == slot:
            roster["activeAccountNumber"] = None
        self._write_roster(roster)
        self.delete_credentials(slot)
        return CodexAccount.from_dict(slot, data)

    def update(self, account: CodexAccount) -> None:
        """Persist a changed roster entry (alias, disabled, refreshed identity)."""
        roster = self._read_roster()
        if account.number not in roster["accounts"]:
            raise ConfigError(f"no Codex account in slot {account.number}")
        roster["accounts"][account.number] = account.to_dict()
        self._write_roster(roster)

    # -- credential backups ----------------------------------------------

    def credentials_path(self, number: str) -> Path:
        """Backup path for a slot.

        Named by SLOT ALONE, deliberately: the Claude side embeds the email in
        its backup filenames, which couples the file to a label that can change
        under it. The credential already carries its own identity in the
        id_token, so the roster stays the only place a name is recorded and a
        changed email needs no file rename.
        """
        return self.credentials_dir / f"{number}.json"

    def write_credentials(self, number: str, auth: dict) -> None:
        """Save a slot's ``auth.json`` content, base64-encoded, mode 0600.

        Base64 is OBSCURITY, not encryption -- it stops a credential appearing
        in a grep or a terminal scrollback, nothing more. It matches what the
        Claude side does so both stores look alike on disk. Treat this
        directory as secret material regardless.
        """
        if not isinstance(auth, dict):
            raise TypeError("credentials must be a dict")
        self.credentials_dir.mkdir(parents=True, exist_ok=True)
        if sys.platform != "win32":
            os.chmod(self.credentials_dir, 0o700)
        encoded = base64.b64encode(
            json.dumps(auth, indent=2).encode("utf-8")
        )
        target = self.credentials_path(number)
        fd, tmp = tempfile.mkstemp(
            dir=str(self.credentials_dir), prefix=".cred-", suffix=".tmp"
        )
        try:
            os.write(fd, encoded)
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

    def read_credentials(self, number: str) -> dict | None:
        """Return a slot's saved credential, or ``None`` when there is none.

        Raises:
            ConfigError: The backup exists but cannot be decoded. Never
                returned as ``None`` -- "no backup" and "unreadable backup"
                lead to opposite actions (re-add versus investigate), and
                conflating them invites overwriting a recoverable credential.
        """
        target = self.credentials_path(number)
        try:
            raw = target.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as e:
            raise ConfigError(f"could not read {target}: {e}") from e
        try:
            data = json.loads(base64.b64decode(raw, validate=True))
        except ValueError as e:  # binascii.Error subclasses ValueError
            raise ConfigError(f"{target} is not a readable credential backup: {e}") from e
        if not isinstance(data, dict):
            raise ConfigError(f"{target} does not hold a credential object")
        return data

    def delete_credentials(self, number: str) -> bool:
        """Remove a slot's backup. True if one was there."""
        try:
            self.credentials_path(number).unlink()
            return True
        except FileNotFoundError:
            return False


def _slot_sort_key(number: str) -> tuple[int, object]:
    """Sort numeric slots numerically (so 10 follows 9, not 1)."""
    text = str(number)
    return (0, int(text)) if text.isdigit() else (1, text)
