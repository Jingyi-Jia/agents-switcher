import json
import os
from pathlib import Path

import pytest

from claude_swap.providers import ProviderActionError
from claude_swap.web.preferences import UiPreferences


def test_reading_defaults_does_not_create_files(tmp_path):
    root = tmp_path / "absent"
    assert UiPreferences(root).get() == {"theme": "system", "profileNoticeVersion": 0}
    assert not root.exists()


def test_preferences_persist_across_server_instances_without_touching_accounts(tmp_path):
    account_file = tmp_path / "accounts.json"
    account_file.write_bytes(b"untouched")
    prefs = UiPreferences(tmp_path)
    assert prefs.update(theme="dark")["ok"] is True
    assert prefs.update(profileNoticeVersion=1, confirm=True)["preferences"] == {"theme": "dark", "profileNoticeVersion": 1}
    assert UiPreferences(tmp_path).get() == {"theme": "dark", "profileNoticeVersion": 1}
    assert account_file.read_bytes() == b"untouched"
    assert not list(tmp_path.glob(".ui-preferences-*"))
    if os.name != "nt":
        assert (tmp_path / "ui-preferences.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("confirm", [None, False, 1, "true", [], {}])
def test_notice_acknowledgement_requires_exact_consent(tmp_path, confirm):
    with pytest.raises(ProviderActionError, match="Acknowledge"):
        UiPreferences(tmp_path).update(profileNoticeVersion=1, confirm=confirm)
    assert not (tmp_path / "ui-preferences.json").exists()


@pytest.mark.parametrize("update", [{}, {"theme": None}, {"theme": []}, {"theme": "neon"}, {"profileNoticeVersion": True}, {"profileNoticeVersion": 2}])
def test_invalid_settings_do_not_write(tmp_path, update):
    with pytest.raises(ProviderActionError):
        UiPreferences(tmp_path).update(**update)
    assert not (tmp_path / "ui-preferences.json").exists()


@pytest.mark.parametrize("raw", [b"{", b"null", b"[]", b"x" * 4097,
    b'{"version":1,"theme":"dark","profileNoticeVersion":true}',
    b'{"version":1,"theme":"dark","profileNoticeVersion":1,"profileNoticeVersion":0}',
    b'{"version":1,"theme":[],"profileNoticeVersion":1}',
])
def test_corrupt_preferences_never_imply_consent_and_are_not_overwritten(tmp_path, raw):
    path = tmp_path / "ui-preferences.json"
    path.write_bytes(raw)
    prefs = UiPreferences(tmp_path)
    assert prefs.get()["profileNoticeVersion"] == 0
    with pytest.raises(ProviderActionError, match="not replaced"):
        prefs.update(theme="light")
    assert path.read_bytes() == raw


@pytest.mark.skipif(os.name == "nt", reason="Symlink creation needs a Windows privilege")
def test_symlink_is_not_followed(tmp_path):
    other = tmp_path / "other"
    raw = json.dumps({"version": 1, "theme": "dark", "profileNoticeVersion": 1})
    other.write_text(raw)
    (tmp_path / "ui-preferences.json").symlink_to(other)
    prefs = UiPreferences(tmp_path)
    assert prefs.get()["profileNoticeVersion"] == 0
    with pytest.raises(ProviderActionError):
        prefs.update(theme="light")
    assert other.read_text() == raw


def test_io_errors_do_not_disclose_paths(tmp_path, monkeypatch):
    prefs = UiPreferences(tmp_path)
    monkeypatch.setattr(Path, "mkdir", lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError("private-path")))
    with pytest.raises(ProviderActionError) as error:
        prefs.update(theme="dark")
    assert "private-path" not in str(error.value)
