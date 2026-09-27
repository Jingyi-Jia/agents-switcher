from __future__ import annotations

import base64
import json
import os
import shlex
import stat
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents_switcher.codex import enrollment as enrollment_mod
from agents_switcher.codex import switcher as switcher_mod
from agents_switcher.codex.auth_file import read_auth, write_auth
from agents_switcher.codex.enrollment import (
    LOGIN_ARGUMENTS,
    MAX_AUTH_BYTES,
    CodexEnrollment,
    EnrollmentError,
)
from agents_switcher.codex.identity import OPENAI_AUTH_CLAIM
from agents_switcher.codex.store import CodexAccountStore
from agents_switcher.codex.switcher import CodexSwitcher
from agents_switcher.codex.usage import CodexUsage
from agents_switcher.exceptions import SwitchError, ValidationError


def jwt(claims):
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


def login(account="one", grant="original"):
    return {
        "auth_mode": "chatgpt", "OPENAI_API_KEY": None,
        "tokens": {
            "id_token": jwt({"email": f"{account}@example.com", OPENAI_AUTH_CLAIM: {
                "chatgpt_account_id": account, "chatgpt_plan_type": "pro",
            }}),
            "account_id": account,
            "access_token": jwt({"exp": 4102444800, "nonce": grant}),
            "refresh_token": f"secret-refresh-{account}-{grant}",
        },
        "future_field": {"untouched": True},
    }


class RevokingAuthority:
    def __init__(self):
        self.revoked = set()

    def sign_in(self, home: Path, account, grant="isolated"):
        current = read_auth(home / "auth.json")
        if current is not None:
            self.revoked.add(current["tokens"]["refresh_token"])
        auth = login(account, grant)
        write_auth(auth, home / "auth.json")
        return auth

    def valid(self, auth):
        return auth["tokens"]["refresh_token"] not in self.revoked


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "live-home"
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setattr(switcher_mod, "running_codex_processes", list)
    store = CodexAccountStore(tmp_path / "owned backup's root")
    switcher = CodexSwitcher(store)
    controller = CodexEnrollment(switcher)

    def forbidden(*args, **kwargs):
        pytest.fail("Enrollment must not contact the provider or refresh tokens")

    monkeypatch.setattr(switcher_mod, "refresh_tokens", forbidden)
    monkeypatch.setattr(switcher_mod, "fetch_usage", forbidden)
    yield SimpleNamespace(home=home, store=store, switcher=switcher,
                          controller=controller, authority=RevokingAuthority())
    controller.close()


def prepare(env, **kwargs):
    result = env.controller.prepare(confirm=True, **kwargs)
    home = Path(env.controller.cli_environment(result["sessionId"])["CODEX_HOME"])
    return result["sessionId"], home


def snapshot(env):
    return (
        read_auth(),
        {number: (account, env.store.read_credentials(number)) for number, account in env.store.accounts().items()},
        env.store.active_number(),
    )


def save_current(env, account="one", grant="original", **kwargs):
    credentials = login(account, grant)
    write_auth(credentials)
    saved = env.switcher.add_current(**kwargs)
    return saved, credentials


def test_model_revokes_preexisting_auth_but_empty_enrollment_preserves_slot_one(env):
    first, original = save_current(env, alias="personal")
    probe = env.home.parent / "unsafe-probe"
    write_auth(original, probe / "auth.json")
    unsafe_authority = RevokingAuthority()
    unsafe_authority.sign_in(probe, "two")
    assert not unsafe_authority.valid(original)

    before = snapshot(env)
    session_id, home = prepare(env)
    assert home.parent == env.store.root.resolve()
    assert not (home / "auth.json").exists()
    assert (home / "config.toml").read_text() == 'cli_auth_credentials_store = "file"\n'
    if os.name != "nt":
        assert stat.S_IMODE(home.stat().st_mode) == 0o700
        assert stat.S_IMODE((home / "config.toml").stat().st_mode) == 0o600
    second_login = env.authority.sign_in(home, "two")
    result = env.controller.complete(session_id, confirm=True)
    assert result["ok"] and result["activationRequired"]
    assert result["account"]["number"] == "2"
    assert read_auth() == before[0]
    assert env.store.get("1") == first
    assert env.store.read_credentials("1") == original
    assert env.store.read_credentials("2") == second_login
    assert env.store.active_number() == "1"
    assert env.authority.valid(original)
    assert env.authority.valid(second_login)
    assert not env.authority.revoked
    assert not home.exists()


def test_repair_one_keeps_second_valid_and_preserves_slot_metadata(env):
    first, original = save_current(env, alias="personal")
    env.switcher.set_account_disabled("1", True)
    second, current = save_current(env, "two", alias="work")
    first = env.store.get("1")
    session_id, home = prepare(env, number="1")
    repaired = env.authority.sign_in(home, "one", "repaired")
    result = env.controller.complete(session_id, confirm=True)
    assert result["account"] == {"number": "1", **first.to_dict()}
    assert env.store.get("1") == first
    assert env.store.get("2") == second
    assert env.store.read_credentials("1") == repaired
    assert env.store.read_credentials("2") == current
    assert read_auth() == current
    assert env.store.active_number() == "2"
    assert env.authority.valid(current)
    switched = env.controller.activate(session_id, confirm=True)
    assert switched["account"]["number"] == "1"
    assert read_auth() == repaired
    assert env.store.read_credentials("2") == current
    assert env.store.active_number() == "1"
    assert not env.authority.revoked


def test_completion_is_idempotent_and_does_not_cache_auth(env):
    session_id, home = prepare(env)
    credentials = env.authority.sign_in(home, "one")
    result = env.controller.complete(session_id, confirm=True)
    result["account"]["email"] = "caller-mutated@example.com"
    again = env.controller.complete(session_id, confirm=True)
    assert again["account"]["email"] == "one@example.com"
    assert len(env.store.accounts()) == 1
    assert read_auth() is None
    assert env.store.active_number() is None
    assert credentials["tokens"]["refresh_token"] not in repr(env.controller._session)
    assert credentials["tokens"]["id_token"] not in json.dumps(again)
    env.controller.cancel(session_id, confirm=True)
    assert env.store.read_credentials("1") == credentials


def test_duplicate_identity_without_repair_target_refreshes_existing_slot(env):
    saved, _ = save_current(env, alias="retained")
    env.switcher.set_account_disabled(saved.number, True)
    session_id, home = prepare(env)
    credentials = env.authority.sign_in(home, "one", "new")
    result = env.controller.complete(session_id, confirm=True)
    assert len(env.store.accounts()) == 1
    assert result["account"]["alias"] == "retained"
    assert result["account"]["disabled"] is True
    assert env.store.read_credentials("1") == credentials


def test_save_only_repair_survives_polling_and_later_same_account_activation(env, monkeypatch):
    _, original = save_current(env)
    session_id, home = prepare(env, number="1")
    repaired = env.authority.sign_in(home, "one", "repaired")
    env.controller.complete(session_id, confirm=True)
    observed = []

    def quota(tokens):
        observed.append(tokens)
        return CodexUsage()

    monkeypatch.setattr(switcher_mod, "fetch_usage", quota)
    restarted = CodexSwitcher(env.store)
    restarted.usage_for("1", allow_refresh=False)
    assert observed == [repaired["tokens"]]
    assert env.store.read_credentials("1") == repaired
    assert read_auth() == original
    with pytest.raises(SwitchError, match="already the active"):
        restarted.switch_to("1")
    switched = env.controller.activate(session_id, confirm=True)
    assert switched["ok"] and not switched["activationRequired"]
    assert read_auth() == repaired
    assert env.store.read_credentials("1") == repaired

    native_rotation = login("one", "native-rotation-after-activation")
    write_auth(native_rotation)
    restarted.usage_for("1", allow_refresh=False)
    assert env.store.read_credentials("1") == native_rotation


@pytest.mark.parametrize("action", ["controller", "manual"])
def test_repeated_activation_preserves_native_rotation_after_import_is_active(env, action):
    save_current(env)
    session_id, home = prepare(env, number="1")
    repaired = env.authority.sign_in(home, "one", "repaired")
    env.controller.complete(session_id, confirm=True)
    env.controller.activate(session_id, confirm=True)
    assert env.switcher._pending_imports() == {}
    assert env.store.read_credentials("1") == repaired

    rotated = {**login("one", "rotated-by-codex"), "native_field": {"preserved": True}}
    write_auth(rotated)
    if action == "controller":
        assert env.controller.activate(session_id, confirm=True)["ok"]
    else:
        assert CodexSwitcher(env.store).switch_to("1", allow_same=True).synced_back
    assert read_auth() == rotated
    assert env.store.read_credentials("1") == rotated


def test_repeated_activation_keeps_a_saved_rotation_newer_than_live(env):
    save_current(env)
    session_id, home = prepare(env, number="1")
    env.authority.sign_in(home, "one", "repaired")
    env.controller.complete(session_id, confirm=True)
    env.controller.activate(session_id, confirm=True)
    saved = {**login("one", "saved-rotation"), "last_refresh": "2030-01-01T00:00:00Z"}
    live = {**login("one", "old-live"), "last_refresh": "2029-01-01T00:00:00Z"}
    env.store.write_credentials("1", saved)
    write_auth(live)
    assert env.controller.activate(session_id, confirm=True)["ok"]
    assert read_auth() == saved
    assert env.store.read_credentials("1") == saved


def test_pending_repair_takes_precedence_over_a_newer_dated_live_login(env):
    save_current(env)
    write_auth({**login("one", "old-live"), "last_refresh": "2050-01-01T00:00:00Z"})
    session_id, home = prepare(env, number="1")
    repaired = env.authority.sign_in(home, "one", "repaired")
    env.controller.complete(session_id, confirm=True)
    assert env.controller.activate(session_id, confirm=True)["ok"]
    assert read_auth() == repaired
    assert env.store.read_credentials("1") == repaired


def test_save_only_repair_survives_outgoing_sync_back(env):
    _, original = save_current(env)
    save_current(env, "two")
    write_auth(original)
    session_id, home = prepare(env, number="1")
    repaired = env.authority.sign_in(home, "one", "repaired")
    env.controller.complete(session_id, confirm=True)
    result = env.switcher.switch_to("2")
    assert not result.synced_back
    assert env.store.read_credentials("1") == repaired
    env.switcher.switch_to("1")
    assert read_auth() == repaired


def test_polling_rotation_of_pending_login_never_changes_live_auth(env, monkeypatch):
    _, original = save_current(env)
    repaired = login("one", "repaired")
    env.switcher.import_login(repaired)
    rotated = login("one", "rotated-after-import")["tokens"]
    calls = []

    def quota(tokens):
        from agents_switcher.codex.usage import UsageAuthError

        calls.append(tokens)
        if len(calls) == 1:
            raise UsageAuthError("HTTP 401")
        assert tokens == rotated
        return CodexUsage()

    monkeypatch.setattr(switcher_mod, "refresh_tokens", lambda tokens: rotated)
    monkeypatch.setattr(switcher_mod, "fetch_usage", quota)
    env.switcher.usage_for("1")
    assert read_auth() == original
    assert env.store.read_credentials("1")["tokens"] == rotated
    env.switcher.switch_to("1", allow_same=True)
    assert read_auth()["tokens"] == rotated


@pytest.mark.parametrize("action", ["complete", "cancel", "activate"])
@pytest.mark.parametrize("confirm", [False, None, 1, "true"])
def test_mutations_require_exact_true(env, action, confirm):
    session_id, home = prepare(env)
    before = snapshot(env)
    with pytest.raises(EnrollmentError, match="Confirm"):
        getattr(env.controller, action)(session_id, confirm=confirm)
    assert home.exists()
    assert snapshot(env) == before


@pytest.mark.parametrize("confirm", [False, None, 1, "true"])
def test_prepare_requires_exact_true(env, confirm):
    with pytest.raises(EnrollmentError, match="Confirm"):
        env.controller.prepare(confirm=confirm)
    assert not env.store.root.exists()


@pytest.mark.parametrize("number", ["alias", "../1", "0", "01", "1.0", "１", 1, True, "9" * 5000])
def test_prepare_accepts_only_a_canonical_slot_number(env, number):
    save_current(env)
    with pytest.raises(EnrollmentError, match="number"):
        env.controller.prepare(number=number, confirm=True)


def test_missing_repair_slot_is_rejected(env):
    with pytest.raises(EnrollmentError, match="unavailable"):
        env.controller.prepare(number="1", confirm=True)


@pytest.mark.parametrize("session_id", ["missing", "../secret", "☃" * 48, "x" * 48, None, {}, 1])
@pytest.mark.parametrize("action", ["complete", "cancel", "activate"])
def test_unknown_sessions_never_select_a_filesystem_path(env, session_id, action):
    prepared, home = prepare(env)
    with pytest.raises(EnrollmentError, match="Unknown"):
        getattr(env.controller, action)(session_id, confirm=True)
    assert home.exists()
    assert not env.store.accounts()


def test_expired_session_is_removed_without_touching_accounts(env, monkeypatch):
    save_current(env)
    before = snapshot(env)
    session_id, home = prepare(env)
    env.authority.sign_in(home, "two")
    expires = env.controller._session.expires_at
    monkeypatch.setattr(enrollment_mod.time, "monotonic", lambda: expires + 1)
    with pytest.raises(EnrollmentError, match="expired"):
        env.controller.complete(session_id, confirm=True)
    assert not home.exists()
    assert snapshot(env) == before


def test_pending_session_cannot_be_replaced(env):
    session_id, home = prepare(env)
    assert env.controller.prepare(confirm=True)["sessionId"] == session_id
    assert home.exists()
    env.controller.cancel(session_id, confirm=True)
    new_session, new_home = prepare(env)
    assert new_session != session_id
    assert new_home != home


def test_cancel_and_close_touch_only_their_own_home(env):
    save_current(env)
    before = snapshot(env)
    other = CodexEnrollment(env.switcher)
    other_result = other.prepare(confirm=True)
    other_home = Path(other.cli_environment(other_result["sessionId"])["CODEX_HOME"])
    session_id, home = prepare(env)
    env.authority.sign_in(home, "two")
    try:
        assert env.controller.cancel(session_id, confirm=True)["ok"]
        assert not home.exists()
        assert other_home.exists()
        next_session, next_home = prepare(env)
        env.controller.close()
        assert not next_home.exists()
        assert other_home.exists()
        assert snapshot(env) == before
        with pytest.raises(EnrollmentError, match="closed"):
            env.controller.prepare(confirm=True)
    finally:
        other.close()


def test_wrong_identity_never_replaces_any_saved_or_live_auth(env):
    save_current(env)
    save_current(env, "two")
    before = snapshot(env)
    session_id, home = prepare(env, number="1")
    env.authority.sign_in(home, "two", "wrong-login")
    with pytest.raises(EnrollmentError, match="different Codex account"):
        env.controller.complete(session_id, confirm=True)
    assert snapshot(env) == before


@pytest.mark.parametrize("mutation", ["remove", "identity", "generation"])
def test_repair_rechecks_expected_slot_before_import(env, mutation):
    account, _ = save_current(env)
    session_id, home = prepare(env, number="1")
    env.authority.sign_in(home, "one", "repair")
    if mutation == "remove":
        env.switcher.remove_account("1")
    else:
        changes = {"account_id": "replacement"} if mutation == "identity" else {"added": "later"}
        env.store.update(replace(account, **changes))
    before = snapshot(env)
    with pytest.raises(EnrollmentError, match="changed"):
        env.controller.complete(session_id, confirm=True)
    assert snapshot(env) == before


def test_repair_keeps_alias_and_disabled_changes_made_during_login(env):
    save_current(env)
    session_id, home = prepare(env, number="1")
    env.switcher.set_alias("1", "new-label")
    env.switcher.set_account_disabled("1", True)
    env.authority.sign_in(home, "one", "repair")
    account = env.controller.complete(session_id, confirm=True)["account"]
    assert account["alias"] == "new-label"
    assert account["disabled"] is True


@pytest.mark.parametrize("raw", [
    pytest.param(b"", id="empty"),
    pytest.param(b"{secret-payload", id="incomplete-json"),
    pytest.param(b"[]", id="array"),
    pytest.param(b"null", id="null"),
    pytest.param(b"\xff", id="invalid-utf8"),
    pytest.param(b"x" * (MAX_AUTH_BYTES + 1), id="oversized"),
])
def test_invalid_or_oversized_auth_is_bounded_and_never_leaks(env, raw):
    save_current(env)
    before = snapshot(env)
    session_id, home = prepare(env)
    (home / "auth.json").write_bytes(raw)
    with pytest.raises(EnrollmentError) as error:
        env.controller.complete(session_id, confirm=True)
    assert "secret-payload" not in str(error.value)
    assert snapshot(env) == before


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "directory", "fifo"])
def test_nonregular_or_linked_auth_is_rejected(env, kind):
    save_current(env)
    before = snapshot(env)
    session_id, home = prepare(env)
    external = env.home.parent / "external-auth.json"
    write_auth(login("two"), external)
    auth = home / "auth.json"
    if kind == "directory":
        auth.mkdir()
    elif kind == "fifo":
        if os.name == "nt":
            pytest.skip("POSIX FIFO")
        os.mkfifo(auth)
    else:
        try:
            auth.symlink_to(external) if kind == "symlink" else os.link(external, auth)
        except OSError:
            pytest.skip("Links are not available")
    with pytest.raises(EnrollmentError):
        env.controller.complete(session_id, confirm=True)
    env.controller.cancel(session_id, confirm=True)
    assert read_auth(external) == login("two")
    assert snapshot(env) == before


def test_replaced_session_directory_cannot_redirect_import_or_cleanup(env):
    session_id, home = prepare(env)
    owned_moved = home.with_name("moved-owned-session")
    home.rename(owned_moved)
    external = env.home.parent / "external-home"
    external.mkdir()
    write_auth(login("two"), external / "auth.json")
    try:
        home.symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("Directory links are not available")
    with pytest.raises(EnrollmentError):
        env.controller.complete(session_id, confirm=True)
    cancelled = env.controller.cancel(session_id, confirm=True)
    assert not cancelled["ok"] and cancelled["warning"]
    assert read_auth(external / "auth.json") == login("two")
    assert owned_moved.exists()


def test_cleanup_never_follows_nested_directory_links(env):
    session_id, home = prepare(env)
    external = env.home.parent / "external-dir"
    external.mkdir()
    sentinel = external / "keep"
    sentinel.write_text("not owned by enrollment")
    try:
        (home / "linked-dir").symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("Directory links are not available")
    env.controller.cancel(session_id, confirm=True)
    assert sentinel.read_text() == "not owned by enrollment"


def test_torn_auth_can_be_retried_after_official_cli_finishes_writing(env, monkeypatch):
    session_id, home = prepare(env)
    (home / "auth.json").write_text("{partial-secret")
    complete_login = login("one")
    monkeypatch.setattr(enrollment_mod.time, "sleep", lambda seconds: write_auth(complete_login, home / "auth.json"))
    result = env.controller.complete(session_id, confirm=True)
    assert result["ok"]
    assert env.store.read_credentials("1") == complete_login


def test_login_read_compares_change_times_from_the_same_stat_api(env, monkeypatch):
    session_id, home = prepare(env)
    credentials = env.authority.sign_in(home, "one")
    original = os.stat

    def path_stat(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if not isinstance(path, int) and Path(path).name == "auth.json":
            values = {name: getattr(info, name) for name in dir(info) if name.startswith("st_")}
            values["st_ctime_ns"] = 1
            return SimpleNamespace(**values)
        return info

    monkeypatch.setattr(os, "stat", path_stat)
    result = env.controller.complete(session_id, confirm=True)
    assert result["ok"]
    assert env.store.read_credentials("1") == credentials


@pytest.mark.parametrize("field", ["st_mtime_ns", "st_ctime_ns", "st_size"])
def test_login_read_detects_changes_during_path_revalidation(env, monkeypatch, field):
    session_id, home = prepare(env)
    env.authority.sign_in(home, "one")
    identity = (home / "auth.json").stat()
    path_checked = False
    observations = 0
    original_stat, original_fstat = os.stat, os.fstat

    def path_stat(path, *args, **kwargs):
        nonlocal path_checked
        info = original_stat(path, *args, **kwargs)
        if not isinstance(path, int) and Path(path).name == "auth.json":
            path_checked = True
        return info

    def handle_stat(descriptor):
        nonlocal path_checked, observations
        info = original_fstat(descriptor)
        if os.path.samestat(info, identity):
            observations += 1
            if observations % 2:
                path_checked = False
            elif path_checked:
                values = {name: getattr(info, name) for name in dir(info) if name.startswith("st_")}
                values[field] += 1
                return SimpleNamespace(**values)
        return info

    monkeypatch.setattr(os, "stat", path_stat)
    monkeypatch.setattr(os, "fstat", handle_stat)
    with pytest.raises(EnrollmentError, match="complete, safe auth.json"):
        env.controller.complete(session_id, confirm=True)
    assert observations == 6
    assert not env.store.accounts()
    assert read_auth() is None


@pytest.mark.parametrize("mutation", [
    "api-key", "missing-access", "empty-refresh", "nonstring-token", "bad-id-token",
    "no-account-identity", "mismatched-account", "keyring-mode", "bad-email", "bad-plan",
])
def test_import_rejects_incomplete_or_malformed_chatgpt_logins(env, mutation):
    credentials = login()
    if mutation == "api-key":
        credentials = {"OPENAI_API_KEY": "secret-api-key"}
    elif mutation == "missing-access":
        del credentials["tokens"]["access_token"]
    elif mutation == "empty-refresh":
        credentials["tokens"]["refresh_token"] = " "
    elif mutation == "nonstring-token":
        credentials["tokens"]["access_token"] = {"secret-token": True}
    elif mutation == "bad-id-token":
        credentials["tokens"]["id_token"] = "unreadable-secret-token"
    elif mutation == "no-account-identity":
        credentials["tokens"]["id_token"] = jwt({"email": "one@example.com"})
        del credentials["tokens"]["account_id"]
    elif mutation == "mismatched-account":
        credentials["tokens"]["account_id"] = "two"
    elif mutation == "keyring-mode":
        credentials["auth_mode"] = "apikey"
    else:
        claims = {"email": "one@example.com", OPENAI_AUTH_CLAIM: {"chatgpt_account_id": "one"}}
        if mutation == "bad-email":
            claims["email"] = {"secret": True}
        else:
            claims[OPENAI_AUTH_CLAIM]["chatgpt_plan_type"] = ["secret"]
        credentials["tokens"]["id_token"] = jwt(claims)
    before = snapshot(env)
    with pytest.raises(ValidationError) as error:
        env.switcher.import_login(credentials)
    assert "secret" not in str(error.value)
    assert snapshot(env) == before


def test_activation_rechecks_identity_and_running_processes(env, monkeypatch):
    save_current(env)
    session_id, home = prepare(env)
    repaired = env.authority.sign_in(home, "one", "new")
    saved = env.controller.complete(session_id, confirm=True)
    before = snapshot(env)
    monkeypatch.setattr(switcher_mod, "running_codex_processes", lambda: [object()])
    with pytest.raises(EnrollmentError, match="Quit Codex"):
        env.controller.activate(session_id, confirm=True)
    assert snapshot(env) == before
    assert env.controller.complete(session_id, confirm=True) == saved
    monkeypatch.setattr(switcher_mod, "running_codex_processes", list)
    env.store.update(replace(env.store.get("1"), account_id="replacement"))
    before = snapshot(env)
    with pytest.raises(EnrollmentError, match="changed"):
        env.controller.activate(session_id, confirm=True)
    assert snapshot(env) == before


def test_activation_requires_completed_login(env):
    session_id, home = prepare(env)
    with pytest.raises(EnrollmentError, match="Save"):
        env.controller.activate(session_id, confirm=True)


def test_import_write_failure_preserves_all_accounts_and_sanitizes_errors(env, monkeypatch):
    save_current(env)
    before = snapshot(env)
    session_id, home = prepare(env, number="1")
    env.authority.sign_in(home, "one", "repaired")

    def fail(*args):
        raise OSError("secret-refresh-in-error")

    monkeypatch.setattr(env.store, "write_credentials", fail)
    with pytest.raises(EnrollmentError) as error:
        env.controller.complete(session_id, confirm=True)
    assert "secret-refresh" not in str(error.value)
    assert snapshot(env) == before
    assert env.switcher._pending_imports() == {}


def test_new_account_roster_failure_keeps_existing_accounts(env, monkeypatch):
    save_current(env)
    before = snapshot(env)
    session_id, home = prepare(env)
    env.authority.sign_in(home, "two")

    def fail(data):
        raise OSError("private-error")

    monkeypatch.setattr(env.store, "_write_roster", fail)
    with pytest.raises(EnrollmentError):
        env.controller.complete(session_id, confirm=True)
    assert snapshot(env) == before
    assert env.switcher._pending_imports() == {}


def test_prepare_command_is_shell_quoted_and_environment_is_not_mutated(env, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret-api-key")
    monkeypatch.setenv("CODEX_APP_SERVER_LOGIN_AUTH_ISSUER", "https://untrusted.invalid")
    before = dict(os.environ)
    prepared = env.controller.prepare(confirm=True)
    child_environment = env.controller.cli_environment(prepared["sessionId"])
    assert prepared.keys() == {"ok", "sessionId", "command", "shell", "instructions"}
    assert "OPENAI_API_KEY" not in child_environment
    assert "CODEX_APP_SERVER_LOGIN_AUTH_ISSUER" not in child_environment
    assert dict(os.environ) == before
    assert "secret-api-key" not in json.dumps(prepared)
    if prepared["shell"] == "sh":
        assert shlex.split(prepared["command"]) == [
            "env", f"CODEX_HOME={child_environment['CODEX_HOME']}", "codex", *LOGIN_ARGUMENTS,
        ]


def test_powershell_command_quotes_home_and_restores_environment(env, monkeypatch):
    monkeypatch.setattr(enrollment_mod.sys, "platform", "win32")
    prepared = env.controller.prepare(confirm=True)
    assert prepared["shell"] == "powershell"
    command = prepared["command"]
    assert "backup''s root" in command
    assert "finally" in command
    assert "$env:CODEX_HOME = $oldCodexHome" in command
    assert "Remove-Item Env:CODEX_HOME" in command
    assert "& codex '-c' 'cli_auth_credentials_store=\"file\"' 'login'" in command


def test_prepare_canonicalizes_system_ancestor_links_without_following_session_links(env):
    parent = env.home.parent / "canonical-parent"
    parent.mkdir()
    linked_parent = env.home.parent / "linked-parent"
    try:
        linked_parent.symlink_to(parent, target_is_directory=True)
    except OSError:
        pytest.skip("Directory links are not available")
    switcher = CodexSwitcher(CodexAccountStore(linked_parent / "backup"))
    controller = CodexEnrollment(switcher)
    try:
        prepared = controller.prepare(confirm=True)
        home = Path(controller.cli_environment(prepared["sessionId"])["CODEX_HOME"])
        assert home.parent == (parent / "backup").resolve()
        assert controller.cancel(prepared["sessionId"], confirm=True)["ok"]
    finally:
        controller.close()


def test_prepare_rejects_a_linked_backup_root(env):
    target = env.home.parent / "not-the-backup-root"
    target.mkdir()
    try:
        env.store.root.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Directory links are not available")
    with pytest.raises(EnrollmentError, match="private"):
        env.controller.prepare(confirm=True)
    assert not list(target.iterdir())


def test_activation_failure_can_be_retried_without_another_login(env, monkeypatch):
    _, original = save_current(env)
    session_id, home = prepare(env, number="1")
    repaired = env.authority.sign_in(home, "one", "fresh")
    result = env.controller.complete(session_id, confirm=True)
    original_writer = switcher_mod.write_auth

    def fail(auth, *args):
        raise OSError("secret-auth-write-payload")

    monkeypatch.setattr(switcher_mod, "write_auth", fail)
    with pytest.raises(EnrollmentError) as error:
        env.controller.activate(session_id, confirm=True)
    assert "secret-auth" not in str(error.value)
    assert read_auth() == original
    assert env.store.read_credentials("1") == repaired
    assert env.controller.complete(session_id, confirm=True) == result
    monkeypatch.setattr(switcher_mod, "write_auth", original_writer)
    assert env.controller.activate(session_id, confirm=True)["ok"]
    assert read_auth() == repaired


def test_pending_import_metadata_corruption_fails_closed_without_secret_errors(env):
    save_current(env)
    before = snapshot(env)
    (env.store.root / ".pending-enrollments.json").write_text("{secret-value")
    session_id, home = prepare(env, number="1")
    env.authority.sign_in(home, "one", "fresh")
    with pytest.raises(EnrollmentError) as error:
        env.controller.complete(session_id, confirm=True)
    assert "secret-value" not in str(error.value)
    assert snapshot(env) == before


def test_completion_reports_private_remnants_without_undoing_the_saved_login(env, monkeypatch):
    session_id, home = prepare(env)
    credentials = env.authority.sign_in(home, "one")
    original_cleanup = env.controller._cleanup
    monkeypatch.setattr(env.controller, "_cleanup", lambda session: False)
    result = env.controller.complete(session_id, confirm=True)
    assert result["ok"] and result["warning"]
    assert env.store.read_credentials("1") == credentials
    assert home.exists()
    monkeypatch.setattr(env.controller, "_cleanup", original_cleanup)
    assert env.controller.cancel(session_id, confirm=True)["ok"]
    assert not home.exists()
    assert env.store.read_credentials("1") == credentials


def test_clearing_a_removed_pending_slot_does_not_affect_another_import(env):
    first = env.switcher.import_login(login("one"))
    second = env.switcher.import_login(login("two"))
    assert env.switcher._pending_imports() == {"1": "one", "2": "two"}
    env.switcher.remove_account("1")
    assert env.switcher._pending_imports() == {"2": "two"}
    assert env.store.read_credentials("2") == login("two")


@pytest.mark.parametrize("refresh_existing", [False, True])
def test_add_current_protects_a_saved_repair_from_the_old_live_login(env, refresh_existing):
    save_current(env)
    session_id, home = prepare(env, number="1")
    repaired = env.authority.sign_in(home, "one", "repaired")
    env.controller.complete(session_id, confirm=True)
    env.controller.cancel(session_id, confirm=True)
    before = snapshot(env)
    pending = (env.store.root / ".pending-enrollments.json").read_bytes()
    with pytest.raises(SwitchError, match="saved login awaiting activation"):
        CodexSwitcher(env.store).add_current(refresh_existing=refresh_existing)
    assert snapshot(env) == before
    assert env.store.read_credentials("1") == repaired
    assert (env.store.root / ".pending-enrollments.json").read_bytes() == pending


def test_add_current_remains_an_explicit_way_to_capture_native_rotation(env):
    env.switcher.import_login(login("one", "saved"))
    env.switcher.switch_to("1", allow_same=True)
    assert env.switcher._pending_imports() == {}
    current = login("one", "native")
    write_auth(current)
    env.switcher.add_current(refresh_existing=True)
    assert env.store.read_credentials("1") == current
    assert env.switcher._pending_imports() == {}


@pytest.mark.parametrize("number", ["../outside", "0", "01"])
def test_import_does_not_write_through_malformed_saved_slot_numbers(env, number):
    account, credentials = save_current(env)
    env.store._write_roster({"accounts": {number: account.to_dict()}})
    roster = env.store.accounts_file.read_bytes()
    backup = env.store.credentials_path("1").read_bytes()
    with pytest.raises(SwitchError, match="invalid"):
        env.switcher.import_login(login("one", "replacement"))
    assert env.store.accounts_file.read_bytes() == roster
    assert env.store.credentials_path("1").read_bytes() == backup
    assert read_auth() == credentials
    assert not (env.store.root / "outside.json").exists()


@pytest.fixture
def native_tree(env, monkeypatch):
    def populate(home):
        logs = home / "log"
        logs.mkdir()
        (logs / "codex-tui.log").write_text("synthetic-login-log")
        sessions = home / "sessions" / "2026" / "09" / "27"
        sessions.mkdir(parents=True)
        (sessions / "rollout-test.jsonl").write_text('{"synthetic":true}\n')
        (home / "state_5.sqlite").write_bytes(b"synthetic-database")

    state = SimpleNamespace(busy=False, populate=populate)
    unlink = os.unlink

    def remove(path, *args, **kwargs):
        if state.busy and Path(path).name == "codex-tui.log":
            raise PermissionError("synthetic-private-log-is-open")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", remove)
    return state


@pytest.mark.parametrize("repair", [False, True])
def test_prepare_retry_returns_same_command_without_resetting_pending_login(env, repair):
    if repair:
        save_current(env)
    number = "1" if repair else None
    prepared = env.controller.prepare(number=number, confirm=True)
    home = Path(env.controller.cli_environment(prepared["sessionId"])["CODEX_HOME"])
    credentials = env.authority.sign_in(home, "one")
    expires = env.controller._session.expires_at
    config = (home / "config.toml").read_bytes()
    before = snapshot(env)
    assert env.controller.prepare(number=number, confirm=True) == prepared
    assert env.controller._session.expires_at == expires
    assert read_auth(home / "auth.json") == credentials
    assert (home / "config.toml").read_bytes() == config
    assert snapshot(env) == before


@pytest.mark.parametrize("first, second", [(None, "1"), ("1", None), ("1", "2")])
def test_prepare_different_target_explains_recovery_without_replacing_session(env, first, second):
    save_current(env)
    save_current(env, "two")
    session_id, home = prepare(env, number=first)
    credentials = env.authority.sign_in(home, "one")
    before = snapshot(env)
    with pytest.raises(EnrollmentError) as caught:
        env.controller.prepare(number=second, confirm=True)
    assert "Stop that terminal login" in str(caught.value)
    assert "quit and reopen Agent Switch" in str(caught.value)
    assert env.controller._session.session_id == session_id
    assert read_auth(home / "auth.json") == credentials
    assert snapshot(env) == before


def test_native_shaped_login_tree_is_removed_without_touching_saved_accounts(env, native_tree):
    save_current(env)
    before = snapshot(env)
    session_id, home = prepare(env)
    native_tree.populate(home)
    env.authority.sign_in(home, "two")
    assert env.controller.cancel(session_id, confirm=True)["ok"]
    assert not home.exists()
    assert snapshot(env) == before


def test_cancel_cleanup_failure_retains_cancel_only_session_for_retry(env, native_tree):
    save_current(env)
    before = snapshot(env)
    session_id, home = prepare(env)
    native_tree.populate(home)
    env.authority.sign_in(home, "two")
    native_tree.busy = True
    cancelled = env.controller.cancel(session_id, confirm=True)
    assert not cancelled["ok"] and cancelled["warning"]
    assert "Private temporary login files remain" in cancelled["cleanupWarning"]
    assert "synthetic-private-log" not in json.dumps(cancelled)
    assert env.controller._session.session_id == session_id
    for action in ("complete", "activate"):
        with pytest.raises(EnrollmentError, match="cancelled"):
            getattr(env.controller, action)(session_id, confirm=True)
    with pytest.raises(EnrollmentError, match="cancelled"):
        env.controller.cli_environment(session_id)
    native_tree.busy = False
    result = env.controller.cancel(session_id, confirm=True)
    assert result["ok"] and not result["warning"]
    assert "cleanupWarning" not in result
    assert env.controller._session is None
    assert not home.exists()
    assert env.controller.cancel(session_id, confirm=True)["ok"]
    assert snapshot(env) == before


def test_close_retries_retained_cancelled_cleanup_even_after_a_failed_close(env, native_tree):
    save_current(env)
    before = snapshot(env)
    session_id, home = prepare(env)
    native_tree.populate(home)
    native_tree.busy = True
    assert not env.controller.cancel(session_id, confirm=True)["ok"]
    env.controller.close()
    assert env.controller._session is not None
    assert home.exists()
    native_tree.busy = False
    env.controller.close()
    assert env.controller._session is None
    assert not home.exists()
    assert snapshot(env) == before


def test_completed_cleanup_warning_survives_combined_activation_and_cleanup_retry(env, native_tree):
    save_current(env)
    session_id, home = prepare(env)
    native_tree.populate(home)
    credentials = env.authority.sign_in(home, "two")
    native_tree.busy = True
    completed = env.controller.complete(session_id, confirm=True)
    assert completed["ok"] and completed["warning"]
    assert "Private temporary login files remain" in completed["cleanupWarning"]
    combined = {**completed, **env.controller.activate(session_id, confirm=True)}
    assert combined["ok"] and not combined["activationRequired"]
    assert combined["cleanupWarning"] == completed["cleanupWarning"]
    assert "synthetic-private-log" not in json.dumps(combined)
    before = snapshot(env)
    assert not env.controller.cancel(session_id, confirm=True)["ok"]
    native_tree.busy = False
    assert env.controller.cancel(session_id, confirm=True)["ok"]
    assert not home.exists()
    assert env.store.read_credentials("2") == credentials
    assert snapshot(env) == before


@pytest.mark.parametrize("busy", [False, True])
def test_expired_owned_session_can_be_cancelled_and_retried(env, native_tree, monkeypatch, busy):
    save_current(env)
    before = snapshot(env)
    session_id, home = prepare(env)
    native_tree.populate(home)
    native_tree.busy = busy
    expires = env.controller._session.expires_at
    monkeypatch.setattr(enrollment_mod.time, "monotonic", lambda: expires + 1)
    with pytest.raises(EnrollmentError, match="expired"):
        env.controller.complete(session_id, confirm=True)
    cancelled = env.controller.cancel(session_id, confirm=True)
    assert cancelled["ok"] is not busy
    assert cancelled["warning"] is busy
    native_tree.busy = False
    assert env.controller.cancel(session_id, confirm=True)["ok"]
    assert not home.exists()
    assert snapshot(env) == before
    newer, newer_home = prepare(env)
    assert env.controller.cancel(session_id, confirm=True)["ok"]
    assert newer_home.exists()
    assert env.controller._session.session_id == newer
