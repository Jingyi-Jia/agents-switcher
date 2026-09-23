"""Tests for reading and writing Codex's auth.json safely.

The behaviours under test exist because of how Codex writes the file itself:
``FileAuthStorage::save`` truncates in place, so readers can catch a torn file
and an interrupted write can truncate the credential permanently.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from claude_swap.codex import auth_file
from tests import conftest
from claude_swap.codex.auth_file import (
    CodexAuthError,
    has_live_login,
    read_auth,
    write_auth,
)

#: A credential in the shape a real ChatGPT login writes -- note ``auth_mode``,
#: which this tool does not model, and a null ``OPENAI_API_KEY``.
REAL_SHAPE = {
    "OPENAI_API_KEY": None,
    "auth_mode": "chatgpt",
    "tokens": {
        "id_token": "header.payload.sig",
        "access_token": "at",
        "refresh_token": "rt",
        "account_id": "acct-1",
    },
    "last_refresh": "2026-09-10T16:21:23.974925203Z",
}


@pytest.fixture
def auth_path(tmp_path: Path) -> Path:
    return tmp_path / "codex" / "auth.json"


class TestReadAuth:
    def test_missing_file_is_none_not_an_error(self, auth_path):
        assert read_auth(auth_path) is None

    def test_reads_a_credential_object(self, auth_path):
        auth_path.parent.mkdir(parents=True)
        auth_path.write_text(json.dumps(REAL_SHAPE))
        assert read_auth(auth_path) == REAL_SHAPE

    def test_preserves_fields_the_tool_does_not_model(self, auth_path):
        auth_path.parent.mkdir(parents=True)
        auth_path.write_text(json.dumps({**REAL_SHAPE, "invented_future_key": 7}))
        data = read_auth(auth_path)
        assert data["auth_mode"] == "chatgpt"
        assert data["invented_future_key"] == 7

    def test_empty_file_raises_rather_than_reading_as_logged_out(self, auth_path):
        # The truncate window of Codex's own writer looks exactly like this.
        # Reporting "logged out" here invites overwriting a live credential.
        auth_path.parent.mkdir(parents=True)
        auth_path.write_text("")
        with pytest.raises(CodexAuthError):
            read_auth(auth_path, attempts=2, initial_delay=0)

    def test_torn_json_raises(self, auth_path):
        auth_path.parent.mkdir(parents=True)
        auth_path.write_text('{"tokens": {"access_to')
        with pytest.raises(CodexAuthError):
            read_auth(auth_path, attempts=2, initial_delay=0)

    def test_json_array_is_not_a_credential_object(self, auth_path):
        auth_path.parent.mkdir(parents=True)
        auth_path.write_text("[1, 2, 3]")
        with pytest.raises(CodexAuthError):
            read_auth(auth_path, attempts=2, initial_delay=0)

    def test_a_torn_read_that_heals_succeeds(self, auth_path, monkeypatch):
        # The whole point of retrying: a file mid-write is healthy a moment
        # later, and must not be reported as corrupt.
        auth_path.parent.mkdir(parents=True)
        auth_path.write_text(json.dumps(REAL_SHAPE))
        calls = {"n": 0}
        real_read = Path.read_text

        def flaky(self, *a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return ""  # caught mid-truncate
            return real_read(self, *a, **kw)

        monkeypatch.setattr(Path, "read_text", flaky)
        assert read_auth(auth_path, attempts=3, initial_delay=0) == REAL_SHAPE
        assert calls["n"] == 2

    def test_unreadable_directory_surfaces_as_auth_error(self, tmp_path):
        # A directory where the file should be: an OSError that is not
        # "absent", so it must not be reported as None/logged-out.
        target = tmp_path / "auth.json"
        target.mkdir()
        with pytest.raises(CodexAuthError):
            read_auth(target, attempts=1)


class TestWriteAuth:
    def test_round_trips_unmodelled_fields(self, auth_path):
        # THE passthrough guarantee. A switcher that models the schema instead
        # silently drops auth_mode on write; this is that regression's guard.
        payload = {**REAL_SHAPE, "some_future_field": {"nested": True}}
        write_auth(payload, auth_path)
        assert read_auth(auth_path) == payload

    def test_creates_the_parent_directory(self, auth_path):
        assert not auth_path.parent.exists()
        write_auth(REAL_SHAPE, auth_path)
        assert auth_path.exists()

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission model")
    def test_written_file_is_owner_only(self, auth_path):
        write_auth(REAL_SHAPE, auth_path)
        assert stat.S_IMODE(auth_path.stat().st_mode) == 0o600

    def test_overwrites_an_existing_credential(self, auth_path):
        write_auth({"tokens": {"access_token": "old"}}, auth_path)
        write_auth(REAL_SHAPE, auth_path)
        assert read_auth(auth_path) == REAL_SHAPE

    def test_leaves_no_temporary_files_behind(self, auth_path):
        write_auth(REAL_SHAPE, auth_path)
        assert [p.name for p in auth_path.parent.iterdir()] == ["auth.json"]

    def test_tempfile_is_created_beside_the_target(self, auth_path, monkeypatch):
        # os.replace is only atomic within one filesystem. A tempfile in /tmp
        # would silently degrade the rename to a copy wherever /tmp is a
        # separate mount -- i.e. exactly the network homes that motivated this.
        seen = {}
        real_mkstemp = auth_file.tempfile.mkstemp

        def spy(*args, **kwargs):
            seen["dir"] = kwargs.get("dir")
            return real_mkstemp(*args, **kwargs)

        monkeypatch.setattr(auth_file.tempfile, "mkstemp", spy)
        write_auth(REAL_SHAPE, auth_path)
        assert Path(seen["dir"]) == auth_path.parent

    def test_rejects_non_dict_content(self, auth_path):
        with pytest.raises(TypeError):
            write_auth(["not", "a", "dict"], auth_path)  # type: ignore[arg-type]

    def test_failed_write_leaves_the_previous_credential_intact(
        self, auth_path, monkeypatch
    ):
        # The failure this module exists to prevent: an interrupted write must
        # never leave a truncated -- i.e. unrecoverable -- credential.
        write_auth(REAL_SHAPE, auth_path)

        def boom(*a, **kw):
            raise OSError("simulated I/O timeout on a soft mount")

        monkeypatch.setattr(auth_file, "replace_with_retry", boom)
        with pytest.raises(OSError):
            write_auth({"tokens": {"access_token": "new"}}, auth_path)

        assert read_auth(auth_path) == REAL_SHAPE
        assert [p.name for p in auth_path.parent.iterdir()] == ["auth.json"]


class TestHasLiveLogin:
    def test_chatgpt_tokens_count(self):
        assert has_live_login({"tokens": {"access_token": "at"}})

    def test_api_key_counts(self):
        assert has_live_login({"OPENAI_API_KEY": "sk-test"})

    def test_null_api_key_is_not_a_credential(self):
        # The shape a real ChatGPT login writes: the key is present but null.
        assert not has_live_login({"OPENAI_API_KEY": None, "auth_mode": "chatgpt"})

    def test_empty_and_malformed_are_not_logins(self):
        assert not has_live_login({})
        assert not has_live_login(None)
        assert not has_live_login({"tokens": {}})
        assert not has_live_login({"tokens": "not a dict"})


class TestRealStoreIsProtected:
    def test_writing_the_developers_real_auth_json_is_blocked(self):
        """The guard must cover ~/.codex, or a stray test ends a real session.

        Unlike the Claude side, there is no recovery here: auth.json IS the
        login, so an unguarded test write logs the developer out of Codex.
        """
        codex_roots = [
            root for root, _ in conftest._REAL_STORE_SPECS
            if root.name == ".codex"
        ]
        assert codex_roots, "real ~/.codex is not in the protected roots"

        target = codex_roots[0] / "auth.json"

        # The FILE itself, not merely the mkdir write_auth does first: assert
        # the primitive is refused, so this cannot pass for the weaker reason
        # that creating the directory happened to trip the guard.
        with pytest.raises(conftest.RealStoreWriteBlocked):
            open(target, "w").close()

        with pytest.raises(conftest.RealStoreWriteBlocked):
            write_auth({"tokens": {"access_token": "would clobber"}}, target)

    def test_the_guard_is_specific_and_not_a_blanket_refusal(self, tmp_path):
        """A control: writes outside the protected roots must still work.

        Without this, the block above would also pass if the guard had simply
        started refusing everything -- which would prove nothing about ~/.codex.
        """
        elsewhere = tmp_path / "not-the-real-store" / "auth.json"
        write_auth({"tokens": {"access_token": "fine"}}, elsewhere)
        assert read_auth(elsewhere)["tokens"]["access_token"] == "fine"

    def test_the_real_codex_home_is_non_recursive(self):
        """Direct children only: ~/.codex also holds sessions/ and six WAL
        SQLite DBs that belong to Codex, not to this tool."""
        for root, recursive in conftest._REAL_STORE_SPECS:
            if root.name == ".codex":
                assert recursive is False
