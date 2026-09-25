"""Private, versioned interface preferences, separate from account data."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path

from claude_swap.dirlock import directory_lock
from claude_swap.exceptions import LockError
from claude_swap.fsutil import replace_with_retry
from claude_swap.paths import get_backup_root
from claude_swap.providers import ProviderActionError


class UiPreferences:
    def __init__(self, root: Path | None = None):
        self.root = root if root is not None else get_backup_root()

    def _read(self) -> dict:
        if self.root.is_symlink():
            raise ValueError
        path = self.root / "ui-preferences.json"
        if path.is_symlink():
            raise ValueError
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        except FileNotFoundError:
            return {"theme": "system", "profileNoticeVersion": 0}
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError
            raw = stream.read(4097)
        if len(raw) > 4096:
            raise ValueError

        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError
                result[key] = value
            return result

        data = json.loads(raw, object_pairs_hook=unique_object)
        if (
            not isinstance(data, dict)
            or set(data) != {"version", "theme", "profileNoticeVersion"}
            or type(data["version"]) is not int or data["version"] != 1
            or not isinstance(data["theme"], str) or data["theme"] not in {"system", "light", "dark"}
            or type(data["profileNoticeVersion"]) is not int or data["profileNoticeVersion"] not in {0, 1}
        ):
            raise ValueError
        return {"theme": data["theme"], "profileNoticeVersion": data["profileNoticeVersion"]}

    def get(self) -> dict:
        try:
            return self._read()
        except (OSError, ValueError, TypeError, RecursionError):
            return {"theme": "system", "profileNoticeVersion": 0, "error": "App preferences could not be read. Profile acknowledgement is required again."}

    def update(self, *, theme=..., profileNoticeVersion=..., confirm=None) -> dict:
        if theme is ... and profileNoticeVersion is ...:
            raise ProviderActionError("Choose an app preference to update.")
        if theme is not ... and (not isinstance(theme, str) or theme not in {"system", "light", "dark"}):
            raise ProviderActionError("Choose System, Light, or Dark appearance.")
        if profileNoticeVersion is not ...:
            if type(profileNoticeVersion) is not int or profileNoticeVersion not in {0, 1}:
                raise ProviderActionError("Unsupported profile notice version.")
            if profileNoticeVersion == 1 and confirm is not True:
                raise ProviderActionError("Acknowledge the Claude profile limitations first.")
        try:
            if self.root.is_symlink() or (self.root.exists() and not self.root.is_dir()):
                raise ValueError
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            with directory_lock(self.root / ".ui-preferences.lock", timeout=5):
                data = self._read()
                if theme is not ...:
                    data["theme"] = theme
                if profileNoticeVersion is not ...:
                    data["profileNoticeVersion"] = profileNoticeVersion
                descriptor, name = tempfile.mkstemp(prefix=".ui-preferences-", dir=self.root)
                temporary = Path(name)
                try:
                    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                        json.dump({"version": 1, **data}, stream)
                        stream.flush()
                        os.fsync(stream.fileno())
                    replace_with_retry(temporary, self.root / "ui-preferences.json")
                finally:
                    temporary.unlink(missing_ok=True)
        except (OSError, ValueError, TypeError, RecursionError, LockError):
            raise ProviderActionError("Could not save app preferences. Check permissions; existing preferences were not replaced.") from None
        return {"ok": True, "preferences": data, "message": "App preferences saved."}
