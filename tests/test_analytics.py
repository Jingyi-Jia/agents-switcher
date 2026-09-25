"""Synthetic usage history; no real account, credential, or network reads."""

from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from claude_swap import analytics
from claude_swap.analytics import UsageAnalytics
from claude_swap.codex import switcher as switcher_mod
from claude_swap.codex.identity import CodexIdentity
from claude_swap.codex.stats import CodexProfileStats, parse_profile_stats
from claude_swap.codex.store import CodexAccountStore
from claude_swap.codex.switcher import CodexSwitcher
from claude_swap.codex.usage import UsageAuthError


TODAY = date(2026, 9, 25)
THROUGH = "2026-09-24"
SUMMARY_FIELDS = {
    "lifetimeTokens", "peakDailyTokens", "currentStreakDays", "longestStreakDays",
    "totalSessions", "totalMessages", "totalThreads",
}


@pytest.fixture(autouse=True)
def forbid_sensitive_actions(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Analytics attempted an unexpected authenticated or mutating operation")

    monkeypatch.setattr("urllib.request.urlopen", forbidden)
    monkeypatch.setattr(switcher_mod, "refresh_tokens", forbidden)
    for name in ("switch_to", "add_current", "usage_for", "reset_credits_for", "status"):
        monkeypatch.setattr(CodexSwitcher, name, forbidden)

    class Calendar(date):
        @classmethod
        def today(cls):
            return TODAY

    monkeypatch.setattr(analytics, "date", Calendar)


@pytest.fixture
def clock(monkeypatch):
    clock = SimpleNamespace(wall=1_790_294_400.0, monotonic=1000.0)
    monkeypatch.setattr(analytics, "time", SimpleNamespace(
        time=lambda: clock.wall, monotonic=lambda: clock.monotonic,
    ))
    return clock


def profile_payload(**stats):
    return {
        "metadata": {"stats_as_of": THROUGH, "stats_error": None},
        "profile": {"display_name": "Not the roster label", "username": "private-name"},
        "stats": {
            "lifetime_tokens": 1200,
            "peak_daily_tokens": 600,
            "current_streak_days": 2,
            "longest_streak_days": 4,
            "total_threads": 9,
            "daily_usage_buckets": [
                {"start_date": "2026-09-24", "tokens": 600},
                {"start_date": "2026-09-23", "tokens": 300},
            ],
            "fast_mode_usage_percentage": 25.5,
            "most_used_reasoning_effort": "high",
            "top_invocations": [
                {"type": "skill", "skill_name": "review", "skill_id": "private-id", "usage_count": 3},
            ],
            **stats,
        },
    }


@pytest.fixture
def codex(tmp_path, monkeypatch):
    store = CodexAccountStore(root=tmp_path / "saved")
    store.add(CodexIdentity("one@example.invalid", "account-one", "pro"), {}, alias="Work")
    store.add(CodexIdentity("two@example.invalid", "account-two", "plus"), {})
    switcher = CodexSwitcher(store)
    original_stats_for = switcher.stats_for
    calls = []
    responses = {
        "1": parse_profile_stats(profile_payload()),
        "2": parse_profile_stats(profile_payload(lifetime_tokens=2400)),
    }

    def stats_for(number, *, allow_refresh):
        assert allow_refresh is False
        calls.append(number)
        result = responses[number]
        if isinstance(result, Exception):
            raise result
        return result() if callable(result) else result

    monkeypatch.setattr(switcher, "stats_for", stats_for)
    return SimpleNamespace(
        store=store, switcher=switcher, calls=calls, responses=responses,
        real_stats_for=original_stats_for,
    )


@pytest.fixture
def claude_cache(tmp_path, monkeypatch):
    root = tmp_path / "configured-claude"
    root.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    payload = {
        "version": 1,
        "lastComputedDate": THROUGH,
        "totalSessions": 3,
        "totalMessages": 12,
        "dailyActivity": [
            {"date": "2026-09-24", "messageCount": 3, "sessionCount": 1, "toolCallCount": 900},
            {"date": "2026-09-22", "messageCount": 1},
            {"date": "2026-09-23", "messageCount": 8, "sessionCount": 2},
        ],
        "dailyModelTokens": [
            {"date": "2026-09-23", "tokensByModel": {"claude-sonnet": 10, "claude-haiku": 5}},
            {"date": "2026-09-24", "tokensByModel": {"claude-sonnet": 0}},
        ],
        "modelUsage": {
            "claude-sonnet": {
                "inputTokens": 100, "outputTokens": 50,
                "cacheReadInputTokens": 1000, "cacheCreationInputTokens": 20,
            },
            "claude-haiku": {"inputTokens": 10, "outputTokens": 5},
        },
        "hourCounts": {"0": 99},
        "longestSession": {"sessionId": "private-session", "prompt": "private-prompt"},
        "credentials": {"accessToken": "private-token"},
    }
    path = root / "stats-cache.json"

    def write(value=None):
        path.write_text(json.dumps(payload if value is None else value), encoding="utf-8")

    write()
    return SimpleNamespace(root=root, path=path, payload=payload, write=write)


@pytest.fixture
def current_claude_cache(claude_cache):
    claude_cache.payload.update({
        "version": 5,
        "dailyModelTokensVersion": 5,
        "dailyModelTokens": [
            {"date": "2026-09-23", "tokensByModel": {"claude-sonnet": 700, "claude-haiku": 100}},
            {"date": "2026-09-24", "tokensByModel": {"claude-sonnet": 470, "claude-haiku": 118}},
        ],
    })
    claude_cache.payload["modelUsage"]["claude-haiku"].update({
        "cacheReadInputTokens": 200, "cacheCreationInputTokens": 3,
    })
    claude_cache.write()
    return claude_cache


def assert_contract(result):
    assert set(result) == {"provider", "scope", "source", "fetchedAt", "accounts", "error"}
    for entry in result["accounts"]:
        assert set(entry) == {
            "number", "label", "available", "stale", "error", "fetchedAt", "dataThrough",
            "summary", "daily", "models", "dailyModels", "insights",
        }
        assert set(entry["summary"]) == SUMMARY_FIELDS
        assert set(entry["insights"]) == {"fastModePercent", "topReasoningEffort", "topInvocations"}
        assert [row["date"] for row in entry["daily"]] == sorted({row["date"] for row in entry["daily"]})
        for row in entry["daily"]:
            assert set(row) == {"date", "tokens", "messages", "sessions"}
            assert date.fromisoformat(row["date"]).isoformat() == row["date"]
        for row in entry["models"]:
            assert set(row) == {"name", "inputTokens", "outputTokens", "cacheReadTokens", "cacheWriteTokens"}
        for row in entry["dailyModels"]:
            assert set(row) == {"date", "tokensByModel"}
            assert all(type(count) is int and count >= 0 for count in row["tokensByModel"].values())
    json.dumps(result, allow_nan=False)


def test_codex_contract_and_explicit_request_only(codex, clock):
    service = UsageAnalytics(codex.switcher)
    assert codex.calls == []
    result = service.get("codex")
    assert_contract(result)
    assert result["scope"] == "account"
    assert result["source"] == "codex-profile-stats"
    assert result["error"] is None
    first, second = result["accounts"]
    assert (first["number"], first["label"], second["number"]) == ("1", "Work", "2")
    assert first["available"] and not first["stale"]
    assert first["fetchedAt"] == clock.wall
    assert first["dataThrough"] == THROUGH
    assert first["summary"] == {
        "lifetimeTokens": 1200, "peakDailyTokens": 600, "currentStreakDays": 2,
        "longestStreakDays": 4, "totalThreads": 9, "totalSessions": None, "totalMessages": None,
    }
    assert first["daily"] == [
        {"date": "2026-09-23", "tokens": 300, "messages": None, "sessions": None},
        {"date": "2026-09-24", "tokens": 600, "messages": None, "sessions": None},
    ]
    assert first["models"] == first["dailyModels"] == []
    assert first["insights"] == {
        "fastModePercent": 25.5, "topReasoningEffort": "high",
        "topInvocations": [{"kind": "skill", "name": "review", "usageCount": 3}],
    }
    assert second["summary"]["lifetimeTokens"] == 2400
    assert codex.calls == ["1", "2"]
    assert "private-" not in json.dumps(result)


def test_codex_five_minute_cache_force_and_copy_isolation(codex, clock):
    service = UsageAnalytics(codex.switcher)
    first = service.get("codex")
    first["accounts"][0]["daily"][0]["tokens"] = 999
    clock.monotonic += 299
    assert service.get("codex")["accounts"][0]["daily"][0]["tokens"] == 300
    assert codex.calls == ["1", "2"]
    clock.monotonic += 2
    service.get("codex")
    assert codex.calls == ["1", "2", "1", "2"]
    clock.monotonic += 1
    service.get("codex", force=True)
    assert codex.calls == ["1", "2", "1", "2", "1", "2"]


def test_codex_stale_on_error_is_labelled_per_account(codex, clock):
    service = UsageAnalytics(codex.switcher)
    first = service.get("codex")
    codex.responses["1"] = RuntimeError("Bearer private-token /home/private/auth.json")
    codex.responses["2"] = parse_profile_stats(profile_payload(lifetime_tokens=3000))
    clock.monotonic += 301
    clock.wall += 301
    result = service.get("codex")
    one, two = result["accounts"]
    assert one["available"] and one["stale"]
    assert one["label"] == "Work"
    assert one["error"] == "Codex statistics could not be loaded."
    assert one["fetchedAt"] == first["accounts"][0]["fetchedAt"]
    assert one["summary"] == first["accounts"][0]["summary"]
    assert two["summary"]["lifetimeTokens"] == 3000
    assert two["error"] is None and not two["stale"]
    assert "private" not in json.dumps(result)
    assert service.get("codex")["accounts"][0] == one
    assert codex.calls == ["1", "2", "1", "2"]
    codex.responses["1"] = parse_profile_stats(profile_payload(lifetime_tokens=1500))
    clock.monotonic += 1
    recovered = service.get("codex", force=True)["accounts"][0]
    assert recovered["error"] is None and not recovered["stale"]
    assert recovered["summary"]["lifetimeTokens"] == 1500


@pytest.mark.parametrize("failure", [
    UsageAuthError("token private-token"),
    ValueError("response /private/cache.json"),
])
def test_codex_initial_failure_does_not_fake_zeros_or_blank_other_accounts(codex, failure):
    codex.responses["1"] = failure
    result = UsageAnalytics(codex.switcher).get("codex")
    assert_contract(result)
    first, second = result["accounts"]
    assert not first["available"]
    assert all(value is None for value in first["summary"].values())
    assert first["daily"] == []
    assert first["error"] and "private" not in first["error"]
    assert second["available"]
    assert result["error"] is None


def test_codex_failed_retries_without_good_data_update_the_attempt_timestamp(codex, clock):
    codex.responses["1"] = RuntimeError("private")
    service = UsageAnalytics(codex.switcher)
    first = service.get("codex")["accounts"][0]
    clock.wall += 301
    clock.monotonic += 301
    second = service.get("codex")["accounts"][0]
    assert not second["available"]
    assert second["fetchedAt"] > first["fetchedAt"]


def test_codex_provider_stats_error_is_unavailable_and_redacted(codex, clock):
    payload = profile_payload()
    payload["metadata"]["stats_error"] = {"message": "private-token /private/cache"}
    codex.responses["1"] = parse_profile_stats(payload)
    result = UsageAnalytics(codex.switcher).get("codex")
    assert not result["accounts"][0]["available"]
    assert result["accounts"][0]["dataThrough"] == THROUGH
    assert result["accounts"][0]["summary"]["lifetimeTokens"] is None
    assert result["accounts"][1]["available"]
    assert "private" not in json.dumps(result)


def test_codex_provider_stats_error_preserves_only_previous_good_history(codex, clock):
    service = UsageAnalytics(codex.switcher)
    service.get("codex")
    payload = {"metadata": {"stats_error": "private-error"}, "stats": {}}
    codex.responses["1"] = parse_profile_stats(payload)
    clock.monotonic += 301
    entry = service.get("codex")["accounts"][0]
    assert entry["available"] and entry["stale"]
    assert entry["summary"]["lifetimeTokens"] == 1200
    assert entry["error"] == "Codex statistics are temporarily unavailable."


def test_codex_empty_payload_is_unavailable(codex):
    codex.responses["1"] = parse_profile_stats({})
    entry = UsageAnalytics(codex.switcher).get("codex")["accounts"][0]
    assert not entry["available"]
    assert all(value is None for value in entry["summary"].values())


def test_codex_typed_stats_without_raw_are_supported(codex):
    codex.responses["1"] = CodexProfileStats(lifetime_tokens=0, stats_as_of=THROUGH)
    entry = UsageAnalytics(codex.switcher).get("codex")["accounts"][0]
    assert entry["available"] and entry["summary"]["lifetimeTokens"] == 0
    assert entry["summary"]["peakDailyTokens"] is None


def test_codex_raw_counts_do_not_inherit_parser_default_zeros(codex):
    codex.responses["1"] = parse_profile_stats(profile_payload(
        daily_usage_buckets=[
            {"start_date": "2026-09-21"},
            {"start_date": "2026-09-22", "tokens": True},
            {"start_date": "2026-09-23", "tokens": "300"},
            {"start_date": "2026-09-24", "tokens": 0},
        ],
        top_invocations=[{"type": "skill", "skill_name": "review"}],
    ))
    entry = UsageAnalytics(codex.switcher).get("codex")["accounts"][0]
    assert [row["tokens"] for row in entry["daily"]] == [None, None, None, 0]
    assert entry["insights"]["topInvocations"] == []


@pytest.mark.parametrize("bad", [-1, True, False, float("nan"), float("inf"), -float("inf"), "8", [], {}, 10**400])
def test_codex_invalid_numbers_are_null(codex, bad):
    codex.responses["1"] = CodexProfileStats(raw=profile_payload(
        lifetime_tokens=bad, peak_daily_tokens=bad, current_streak_days=bad,
        longest_streak_days=bad, fast_mode_usage_percentage=bad,
        daily_usage_buckets=[{"start_date": THROUGH, "tokens": bad}],
        top_invocations=[{"type": "skill", "skill_name": "review", "usage_count": bad}],
    ))
    result = UsageAnalytics(codex.switcher).get("codex")
    assert_contract(result)
    entry = result["accounts"][0]
    assert entry["summary"]["lifetimeTokens"] is None
    assert entry["summary"]["currentStreakDays"] is None
    assert entry["daily"][0]["tokens"] is None
    assert entry["insights"]["fastModePercent"] is None
    assert entry["insights"]["topInvocations"] == []


def test_codex_insights_reject_malformed_names_and_percentages(codex):
    codex.responses["1"] = CodexProfileStats(raw=profile_payload(
        fast_mode_usage_percentage=100.1, most_used_reasoning_effort={"private": "token"},
        top_invocations=[
            {"type": "skill", "skill_name": {"token": "private-token"}, "usage_count": 1},
            {"type": "private", "skill_name": "private-prompt", "usage_count": 1},
            {"type": "skill", "skill_name": "x" * 1000, "usage_count": 1},
            {"type": "skill", "skill_name": "bad\nname", "usage_count": 1},
        ],
    ))
    entry = UsageAnalytics(codex.switcher).get("codex")["accounts"][0]
    assert entry["insights"] == {"fastModePercent": None, "topReasoningEffort": None, "topInvocations": []}


def test_codex_invocations_are_deduplicated_sorted_and_bounded(codex):
    rows = [{"type": "skill", "skill_name": f"skill-{count}", "usage_count": count} for count in range(150)]
    rows.append({"type": "skill", "skill_name": "skill-0", "usage_count": 1000})
    codex.responses["1"] = parse_profile_stats(profile_payload(top_invocations=rows))
    rows = UsageAnalytics(codex.switcher).get("codex")["accounts"][0]["insights"]["topInvocations"]
    assert len(rows) == 100
    assert rows[0] == {"kind": "skill", "name": "skill-0", "usageCount": 1000}
    assert rows[-1]["usageCount"] == 51


def test_codex_missing_or_old_data_through_is_not_fabricated(codex):
    one = profile_payload()
    one["metadata"] = {"generated_at": "2026-09-25T23:00:00Z"}
    two = profile_payload()
    two["metadata"] = {"stats_as_of": "2026-09-01"}
    codex.responses.update({"1": parse_profile_stats(one), "2": parse_profile_stats(two)})
    first, second = UsageAnalytics(codex.switcher).get("codex")["accounts"]
    assert first["dataThrough"] is None and first["stale"]
    assert second["dataThrough"] == "2026-09-01" and second["stale"]
    assert second["daily"] == []


def test_codex_slot_replacement_and_new_incarnation_never_reuse_stats(codex, clock):
    service = UsageAnalytics(codex.switcher)
    service.get("codex")
    original = codex.store.get("1")
    codex.store.update(replace(original, account_id="replacement", email="new@example.invalid"))
    codex.responses["1"] = UsageAuthError("private-token")
    entry = service.get("codex")["accounts"][0]
    assert not entry["available"]
    assert entry["summary"]["lifetimeTokens"] is None
    assert codex.calls == ["1", "2", "1"]
    codex.store.update(replace(original, added="new-incarnation"))
    assert not service.get("codex")["accounts"][0]["available"]
    assert codex.calls == ["1", "2", "1", "1"]


def test_codex_observed_deletion_clears_cached_identity(codex):
    service = UsageAnalytics(codex.switcher)
    service.get("codex")
    original = codex.store.get("1")
    codex.store.remove("1")
    assert [entry["number"] for entry in service.get("codex")["accounts"]] == ["2"]
    codex.store.add(original.identity, {}, number="1")
    codex.store.update(original)
    codex.responses["1"] = RuntimeError("private")
    assert not service.get("codex")["accounts"][0]["available"]


@pytest.mark.parametrize("remove", [False, True])
def test_codex_roster_change_during_fetch_discards_old_result(codex, remove):
    def mutate():
        if remove:
            codex.store.remove("1")
        else:
            codex.store.update(replace(codex.store.get("1"), account_id="replacement", alias="New"))
        return parse_profile_stats(profile_payload(lifetime_tokens=9999))

    codex.responses["1"] = mutate
    result = UsageAnalytics(codex.switcher).get("codex")
    if remove:
        assert [row["number"] for row in result["accounts"]] == ["2"]
    else:
        entry = result["accounts"][0]
        assert entry["label"] == "New" and not entry["available"]
        assert entry["summary"]["lifetimeTokens"] is None
    assert "9999" not in json.dumps(result)


def test_codex_labels_update_without_refetch_and_unknown_identity_is_not_requested(codex):
    service = UsageAnalytics(codex.switcher)
    service.get("codex")
    codex.store.update(replace(codex.store.get("1"), alias="Renamed", disabled=True))
    codex.store.update(replace(codex.store.get("2"), account_id=""))
    result = service.get("codex")
    assert result["accounts"][0]["label"] == "Renamed"
    assert not result["accounts"][1]["available"]
    assert codex.calls == ["1", "2"]


def test_codex_bad_roster_is_not_reported_as_empty_success(codex):
    service = UsageAnalytics(codex.switcher)
    service.get("codex")
    codex.store.accounts_file.write_text("private-malformed-roster")
    result = service.get("codex")
    assert result["accounts"] == []
    assert result["error"] == "Saved Codex accounts could not be read."
    assert "private" not in json.dumps(result)


@pytest.mark.parametrize("force", [False, True])
def test_unconfigured_codex_does_not_construct_default_switcher_or_inspect_stores(monkeypatch, force):
    def forbidden(*args, **kwargs):
        pytest.fail("Unconfigured Codex analytics tried to inspect a default store")

    monkeypatch.setattr(switcher_mod, "CodexSwitcher", forbidden)
    monkeypatch.setattr(switcher_mod, "CodexAccountStore", forbidden)
    monkeypatch.setattr(analytics, "_read_claude_cache", forbidden)
    result = UsageAnalytics().get("codex", force=force)
    assert_contract(result)
    assert result["accounts"] == []
    assert result["error"] == "Codex analytics is not configured."


def test_configured_codex_with_no_accounts_is_an_empty_success(codex):
    codex.store.remove("1")
    codex.store.remove("2")
    result = UsageAnalytics(codex.switcher).get("codex")
    assert result["accounts"] == [] and result["error"] is None
    assert codex.calls == []


@pytest.mark.parametrize("auth_failure", [False, True])
def test_real_codex_stats_path_never_rotates_or_switches(codex, monkeypatch, auth_failure):
    credentials = {
        "tokens": {"access_token": "synthetic-expired", "refresh_token": "synthetic-refresh", "account_id": "account-one"},
    }
    codex.store.write_credentials("1", credentials)
    codex.store.remove("2")
    monkeypatch.setattr(codex.switcher, "stats_for", codex.real_stats_for)
    calls = []

    def fetch(tokens):
        calls.append(tokens["access_token"])
        if auth_failure:
            raise UsageAuthError("private-error-body")
        return parse_profile_stats(profile_payload())

    monkeypatch.setattr(switcher_mod, "fetch_profile_stats", fetch)
    before = codex.store.credentials_dir.joinpath("1.json").read_bytes()
    entry = UsageAnalytics(codex.switcher).get("codex")["accounts"][0]
    assert entry["available"] is not auth_failure
    assert calls == ["synthetic-expired"]
    assert codex.store.credentials_dir.joinpath("1.json").read_bytes() == before
    assert "synthetic" not in json.dumps(entry)


@pytest.mark.parametrize("force", [False, True])
def test_codex_concurrent_requests_share_one_bounded_batch(codex, claude_cache, force):
    entered = threading.Event()
    release = threading.Event()
    second_waiting = threading.Event()
    service = UsageAnalytics(codex.switcher)
    original_lock = service._codex_lock

    class ObservedLock:
        def __enter__(self):
            if original_lock.locked():
                second_waiting.set()
            original_lock.acquire()

        def __exit__(self, *args):
            original_lock.release()

    service._codex_lock = ObservedLock()

    def slow_fetch():
        entered.set()
        assert release.wait(5)
        return parse_profile_stats(profile_payload())

    codex.responses["1"] = slow_fetch
    with ThreadPoolExecutor(max_workers=3) as workers:
        first = workers.submit(service.get, "codex", force=force)
        try:
            assert entered.wait(5)
            second = workers.submit(service.get, "codex", force=force)
            assert second_waiting.wait(5)
            local = workers.submit(service.get, "claude").result(timeout=5)
            assert local["accounts"][0]["available"]
            assert codex.calls == ["1"]
        finally:
            release.set()
        assert first.result(timeout=5)["accounts"] == second.result(timeout=5)["accounts"]
    assert codex.calls == ["1", "2"]


@pytest.mark.parametrize("version", [1, 2])
def test_claude_legacy_contract_preserves_incomplete_totals_and_explicit_token_buckets(claude_cache, version):
    claude_cache.payload["version"] = version
    claude_cache.write()
    result = UsageAnalytics().get("claude")
    assert_contract(result)
    assert result["scope"] == "local" and result["source"] == "claude-stats-cache"
    assert result["error"] is None
    assert len(result["accounts"]) == 1
    entry = result["accounts"][0]
    assert entry["number"] is None and entry["label"] == "This device"
    assert entry["available"] and not entry["stale"]
    assert entry["dataThrough"] == THROUGH
    assert entry["summary"] == {
        "lifetimeTokens": None, "peakDailyTokens": 15, "currentStreakDays": None,
        "longestStreakDays": None, "totalSessions": 3, "totalMessages": 12, "totalThreads": None,
    }
    assert entry["daily"] == [
        {"date": "2026-09-22", "tokens": None, "messages": 1, "sessions": None},
        {"date": "2026-09-23", "tokens": 15, "messages": 8, "sessions": 2},
        {"date": "2026-09-24", "tokens": 0, "messages": 3, "sessions": 1},
    ]
    assert entry["models"] == [
        {"name": "claude-haiku", "inputTokens": 10, "outputTokens": 5, "cacheReadTokens": None, "cacheWriteTokens": None},
        {"name": "claude-sonnet", "inputTokens": 100, "outputTokens": 50, "cacheReadTokens": 1000, "cacheWriteTokens": 20},
    ]
    assert entry["dailyModels"] == [
        {"date": "2026-09-23", "tokensByModel": {"claude-haiku": 5, "claude-sonnet": 10}},
        {"date": "2026-09-24", "tokensByModel": {"claude-sonnet": 0}},
    ]
    assert "private" not in json.dumps(result)


def test_claude_v5_matches_all_four_lifetime_components_without_double_counting_daily_cache(current_claude_cache):
    result = UsageAnalytics().get("claude")
    assert_contract(result)
    entry = result["accounts"][0]
    assert entry["available"] and not entry["stale"]
    assert entry["summary"] == {
        "lifetimeTokens": 1388, "peakDailyTokens": 800, "currentStreakDays": None,
        "longestStreakDays": None, "totalSessions": 3, "totalMessages": 12, "totalThreads": None,
    }
    assert entry["models"] == [
        {"name": "claude-haiku", "inputTokens": 10, "outputTokens": 5, "cacheReadTokens": 200, "cacheWriteTokens": 3},
        {"name": "claude-sonnet", "inputTokens": 100, "outputTokens": 50, "cacheReadTokens": 1000, "cacheWriteTokens": 20},
    ]
    assert entry["daily"] == [
        {"date": "2026-09-22", "tokens": None, "messages": 1, "sessions": None},
        {"date": "2026-09-23", "tokens": 800, "messages": 8, "sessions": 2},
        {"date": "2026-09-24", "tokens": 588, "messages": 3, "sessions": 1},
    ]
    assert entry["dailyModels"] == current_claude_cache.payload["dailyModelTokens"]


@pytest.mark.parametrize("version", [1, 2, 3, 4, 5])
def test_claude_known_versions_preserve_complete_reported_components(current_claude_cache, version):
    current_claude_cache.payload["version"] = version
    current_claude_cache.payload["dailyModelTokensVersion"] = version
    current_claude_cache.write()
    entry = UsageAnalytics().get("claude")["accounts"][0]
    assert entry["available"]
    assert entry["summary"]["lifetimeTokens"] == 1388
    assert [row["tokens"] for row in entry["daily"]] == [None, 800, 588]


@pytest.mark.parametrize("version", [1, 2, 5])
@pytest.mark.parametrize("missing", ["inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens"])
def test_claude_incomplete_lifetime_components_are_unknown_even_with_complete_daily_buckets(
    current_claude_cache, version, missing,
):
    current_claude_cache.payload["version"] = version
    current_claude_cache.payload["modelUsage"]["claude-haiku"].pop(missing)
    current_claude_cache.write()
    entry = UsageAnalytics().get("claude")["accounts"][0]
    assert entry["available"]
    assert entry["summary"]["lifetimeTokens"] is None
    assert [row["tokens"] for row in entry["daily"]] == [None, 800, 588]


def test_claude_explicit_zero_cache_components_preserve_zero_lifetime_tokens(claude_cache):
    claude_cache.write({
        "version": 5, "lastComputedDate": THROUGH,
        "modelUsage": {"claude-sonnet": {
            "inputTokens": 0, "outputTokens": 0, "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0,
        }},
    })
    entry = UsageAnalytics().get("claude")["accounts"][0]
    assert entry["available"]
    assert entry["summary"]["lifetimeTokens"] == 0
    assert entry["summary"]["peakDailyTokens"] is None


def test_claude_reads_only_stats_cache_and_never_attributes_history_to_an_account(claude_cache, monkeypatch):
    class NoCodex:
        def __getattribute__(self, key):
            pytest.fail("Claude analytics accessed Codex")

    opened = []
    original_open = analytics.os.open

    def read_only_cache(path, flags, *args, **kwargs):
        assert Path(path) == claude_cache.path.resolve()
        assert not flags & (os.O_CREAT | os.O_WRONLY | os.O_RDWR | os.O_TRUNC)
        opened.append(path)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(analytics.os, "open", read_only_cache)
    service = UsageAnalytics(NoCodex())
    before = claude_cache.path.read_bytes()
    first = service.get("claude")
    claude_cache.root.joinpath(".credentials.json").write_text('{"accessToken":"private-token"}')
    claude_cache.root.joinpath("settings.json").write_text('{"account":"someone-else"}')
    second = service.get("claude", force=True)
    assert len(opened) == 2
    assert first["accounts"][0]["summary"] == second["accounts"][0]["summary"]
    assert second["accounts"][0]["number"] is None
    assert second["accounts"][0]["label"] == "This device"
    assert claude_cache.path.read_bytes() == before


def test_claude_missing_cache_creates_nothing_and_default_home_is_honored(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "missing-home")
    result = UsageAnalytics().get("claude")
    assert_contract(result)
    entry = result["accounts"][0]
    assert not entry["available"] and entry["dataThrough"] is None
    assert all(value is None for value in entry["summary"].values())
    assert "not available" in entry["error"]
    assert not (tmp_path / "missing-home").exists()


@pytest.mark.parametrize("through,stale", [(THROUGH, False), ("2026-09-01", True), (None, True), ("2026-99-01", True)])
def test_claude_reports_original_cache_date_and_freshness(claude_cache, through, stale):
    claude_cache.payload["lastComputedDate"] = through
    claude_cache.write()
    entry = UsageAnalytics().get("claude")["accounts"][0]
    assert entry["dataThrough"] == (through if through in (THROUGH, "2026-09-01") else None)
    assert entry["stale"] is stale
    assert entry["summary"]["totalSessions"] == 3
    if through == "2026-09-01":
        assert entry["daily"] == entry["dailyModels"] == []


def test_claude_each_request_rereads_current_config_root(claude_cache, tmp_path, monkeypatch):
    service = UsageAnalytics()
    service.get("claude")
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(other))
    assert not service.get("claude")["accounts"][0]["available"]
    other.joinpath("stats-cache.json").write_text(json.dumps({
        "version": 2, "lastComputedDate": THROUGH, "totalSessions": 0,
    }))
    entry = service.get("claude")["accounts"][0]
    assert entry["available"] and entry["summary"]["totalSessions"] == 0
    assert entry["summary"]["lifetimeTokens"] is None


@pytest.mark.parametrize("content", [b"{private-malformed", b"[]", b"null", b"\xff", b"[" * 2000 + b"]" * 2000])
def test_claude_malformed_json_is_unavailable_and_redacted(claude_cache, content):
    claude_cache.path.write_bytes(content)
    result = UsageAnalytics().get("claude")
    assert_contract(result)
    assert not result["accounts"][0]["available"]
    assert result["error"]
    assert "private" not in json.dumps(result)
    assert str(claude_cache.root) not in json.dumps(result)


@pytest.mark.parametrize("change", [
    {"version": True}, {"version": 0}, {"version": 6}, {"version": 999}, {"version": None},
    {"dailyActivity": {}}, {"dailyModelTokens": "private-content"}, {"modelUsage": []},
])
def test_claude_unsupported_schema_is_unavailable(claude_cache, change):
    claude_cache.payload.update(change)
    claude_cache.write()
    entry = UsageAnalytics().get("claude")["accounts"][0]
    assert not entry["available"]
    assert all(value is None for value in entry["summary"].values())


def test_claude_empty_schema_has_nulls_not_zeros(claude_cache):
    claude_cache.write({"version": 1, "lastComputedDate": THROUGH})
    result = UsageAnalytics().get("claude")
    assert not result["accounts"][0]["available"]
    assert result["accounts"][0]["dataThrough"] == THROUGH
    assert all(value is None for value in result["accounts"][0]["summary"].values())


@pytest.mark.parametrize("bad", [-1, True, False, float("nan"), float("inf"), -float("inf"), "8", [], {}, 10**400])
def test_claude_invalid_numbers_are_null(claude_cache, bad):
    claude_cache.write({
        "version": 1, "lastComputedDate": THROUGH, "totalMessages": 1, "totalSessions": bad,
        "modelUsage": {"claude-sonnet": {
            "inputTokens": bad, "outputTokens": 10,
            "cacheReadInputTokens": bad, "cacheCreationInputTokens": bad,
        }},
        "dailyActivity": [{"date": THROUGH, "messageCount": bad, "sessionCount": bad}],
        "dailyModelTokens": [{"date": THROUGH, "tokensByModel": {"claude-sonnet": bad}}],
    })
    result = UsageAnalytics().get("claude")
    assert_contract(result)
    entry = result["accounts"][0]
    assert entry["summary"]["totalSessions"] is None
    assert entry["summary"]["lifetimeTokens"] is None
    assert entry["summary"]["peakDailyTokens"] is None
    assert entry["daily"] == [{"date": THROUGH, "tokens": None, "messages": None, "sessions": None}]
    assert entry["dailyModels"] == []
    assert entry["models"][0]["cacheReadTokens"] is None
    assert entry["models"][0]["cacheWriteTokens"] is None


@pytest.mark.parametrize("buckets", [{}, {"claude-sonnet": 1.5}, {"claude-sonnet": 1, "other": True}])
def test_claude_incomplete_or_noninteger_model_buckets_do_not_become_daily_totals(claude_cache, buckets):
    claude_cache.payload["dailyModelTokens"] = [{"date": THROUGH, "tokensByModel": buckets}]
    claude_cache.write()
    entry = UsageAnalytics().get("claude")["accounts"][0]
    assert entry["daily"][-1]["tokens"] is None
    assert entry["summary"]["peakDailyTokens"] is None


def test_claude_partial_model_buckets_are_available_without_a_known_daily_total(claude_cache):
    claude_cache.write({
        "version": 2, "lastComputedDate": THROUGH,
        "dailyModelTokens": [{
            "date": THROUGH,
            "tokensByModel": {"claude-sonnet@20260924": 3, "gateway/claude-haiku": 4, "bad-model": True},
        }],
    })
    entry = UsageAnalytics().get("claude")["accounts"][0]
    assert entry["available"] and entry["error"] is None
    assert entry["daily"][0]["tokens"] is None
    assert entry["dailyModels"] == [{
        "date": THROUGH, "tokensByModel": {"claude-sonnet@20260924": 3, "gateway/claude-haiku": 4},
    }]


def test_claude_does_not_infer_tokens_from_activity_cache_components_or_hours(claude_cache):
    claude_cache.payload.pop("dailyModelTokens")
    claude_cache.payload["modelUsage"] = {"claude-sonnet": {"cacheReadInputTokens": 1000}}
    claude_cache.write()
    entry = UsageAnalytics().get("claude")["accounts"][0]
    assert entry["summary"]["lifetimeTokens"] is None
    assert entry["summary"]["peakDailyTokens"] is None
    assert all(row["tokens"] is None for row in entry["daily"])


def test_claude_malformed_model_names_cannot_leak_nested_payloads_or_make_partial_totals(claude_cache):
    claude_cache.payload["modelUsage"]["/private/path"] = {"inputTokens": 100}
    claude_cache.payload["modelUsage"]["not-a-model-object"] = "private-token"
    claude_cache.payload["dailyModelTokens"] = [{
        "date": THROUGH, "tokensByModel": {"claude-sonnet": 5, "/private/path": 6},
    }]
    claude_cache.write()
    result = UsageAnalytics().get("claude")
    entry = result["accounts"][0]
    assert entry["summary"]["lifetimeTokens"] is None
    assert entry["daily"][-1]["tokens"] is None
    assert "private" not in json.dumps(result)


@pytest.mark.parametrize("field", ["modelUsage", "dailyModelTokens"])
def test_claude_excessive_model_counts_are_not_silently_truncated(claude_cache, field):
    models = {f"model-{index}": 1 for index in range(257)}
    if field == "modelUsage":
        claude_cache.payload[field] = {name: {"inputTokens": 1, "outputTokens": 1} for name in models}
    else:
        claude_cache.payload[field] = [{"date": THROUGH, "tokensByModel": models}]
    claude_cache.write()
    entry = UsageAnalytics().get("claude")["accounts"][0]
    assert not entry["available"] and "too many models" in entry["error"]


def test_claude_overflowing_derived_totals_are_null(claude_cache):
    large = 2**1023
    claude_cache.write({
        "version": 2, "lastComputedDate": THROUGH,
        "modelUsage": {"claude-sonnet": {
            "inputTokens": large, "outputTokens": large, "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0,
        }},
        "dailyModelTokens": [{"date": THROUGH, "tokensByModel": {"claude-sonnet": large, "claude-haiku": large}}],
    })
    result = UsageAnalytics().get("claude")
    assert_contract(result)
    entry = result["accounts"][0]
    assert entry["available"]
    assert entry["summary"]["lifetimeTokens"] is None
    assert entry["summary"]["peakDailyTokens"] is None
    assert entry["daily"][0]["tokens"] is None


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_future_reporting_dates_are_kept_but_not_treated_as_fresh(codex, claude_cache, provider):
    future = "2026-09-26"
    if provider == "claude":
        claude_cache.payload["lastComputedDate"] = future
        claude_cache.payload["dailyModelTokens"].append({"date": future, "tokensByModel": {"claude-sonnet": 999}})
        claude_cache.write()
    else:
        payload = profile_payload()
        payload["metadata"]["stats_as_of"] = future
        payload["stats"]["daily_usage_buckets"].append({"start_date": future, "tokens": 999})
        codex.responses["1"] = parse_profile_stats(payload)
    entry = UsageAnalytics(codex.switcher).get(provider)["accounts"][0]
    assert entry["dataThrough"] == future and entry["stale"]
    assert future not in [row["date"] for row in entry["daily"]]


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_daily_dates_are_valid_sorted_deduplicated_and_bounded(codex, claude_cache, provider):
    days = [(TODAY - timedelta(days=count)).isoformat() for count in range(1, 3701)]
    invalid = ["2026-02-30", "2026-2-01", "20260201", "2026-09-24T00:00:00Z", "private-date", None, True]
    if provider == "codex":
        buckets = [{"start_date": day, "tokens": index} for index, day in enumerate(days)]
        buckets.extend({"start_date": day, "tokens": 10} for day in invalid)
        buckets.extend([
            {"start_date": THROUGH, "tokens": 7},
            {"start_date": "2026-09-25", "tokens": 999},
        ])
        codex.responses["1"] = parse_profile_stats(profile_payload(daily_usage_buckets=buckets))
    else:
        buckets = [{"date": day, "tokensByModel": {"claude-sonnet": index}} for index, day in enumerate(days)]
        buckets.extend({"date": day, "tokensByModel": {"claude-sonnet": 10}} for day in invalid)
        buckets.extend([
            {"date": THROUGH, "tokensByModel": {"claude-sonnet": 7}},
            {"date": "2026-09-25", "tokensByModel": {"claude-sonnet": 999}},
        ])
        claude_cache.payload["dailyModelTokens"] = buckets
        claude_cache.payload["dailyActivity"] = [{"date": THROUGH, "messageCount": 5}, {"date": THROUGH, "messageCount": 6}]
        claude_cache.write()
    result = UsageAnalytics(codex.switcher).get(provider)
    assert_contract(result)
    entry = result["accounts"][0]
    assert len(entry["daily"]) == 3660
    assert entry["daily"][-1]["date"] == THROUGH
    assert entry["daily"][-1]["tokens"] == 7
    assert entry["daily"][0]["date"] == days[3659]
    if provider == "claude":
        assert len(entry["dailyModels"]) == 3660
        assert entry["daily"][-1]["messages"] == 6


def test_claude_oversized_cache_is_rejected_before_opening(claude_cache, monkeypatch):
    monkeypatch.setattr(analytics, "_MAX_CACHE_BYTES", 32)

    def forbidden(*args, **kwargs):
        pytest.fail("An oversized cache was opened")

    monkeypatch.setattr(analytics.os, "open", forbidden)
    entry = UsageAnalytics().get("claude")["accounts"][0]
    assert not entry["available"]
    assert "size limit" in entry["error"]


def test_claude_cache_growth_is_bounded(claude_cache, monkeypatch):
    claude_cache.path.write_bytes(b"{}")
    monkeypatch.setattr(analytics, "_MAX_CACHE_BYTES", 32)
    original_open = analytics.os.open

    def grow_then_open(path, flags):
        Path(path).write_bytes(b" " * 1000)
        return original_open(path, flags)

    monkeypatch.setattr(analytics.os, "open", grow_then_open)
    entry = UsageAnalytics().get("claude")["accounts"][0]
    assert not entry["available"] and "size limit" in entry["error"]


@pytest.mark.parametrize("target", ["file", "root"])
def test_claude_symlinks_cannot_escape_config_root(claude_cache, tmp_path, target):
    outside = tmp_path / "private"
    outside.mkdir()
    outside.joinpath("stats-cache.json").write_text(json.dumps(claude_cache.payload))
    claude_cache.path.unlink()
    try:
        if target == "file":
            claude_cache.path.symlink_to(outside / "stats-cache.json")
        else:
            claude_cache.root.rmdir()
            claude_cache.root.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks requires permission on this platform")
    entry = UsageAnalytics().get("claude")["accounts"][0]
    assert not entry["available"] and "regular local file" in entry["error"]


def test_claude_file_replacement_between_stat_and_open_is_not_read(claude_cache, tmp_path, monkeypatch):
    replacement = tmp_path / "replacement"
    replacement.write_text(json.dumps({"version": 1, "totalSessions": 999}))
    original_open = analytics.os.open

    def replace_then_open(path, flags):
        os.replace(replacement, path)
        return original_open(path, flags)

    monkeypatch.setattr(analytics.os, "open", replace_then_open)
    entry = UsageAnalytics().get("claude")["accounts"][0]
    assert not entry["available"]
    assert "changed during" in entry["error"]
    assert entry["summary"]["totalSessions"] is None


@pytest.mark.parametrize("kind", ["directory", "fifo"])
def test_claude_nonregular_cache_does_not_block_or_read(claude_cache, kind):
    claude_cache.path.unlink()
    if kind == "directory":
        claude_cache.path.mkdir()
    else:
        if not hasattr(os, "mkfifo"):
            pytest.skip("Named pipes are not available on this platform")
        os.mkfifo(claude_cache.path)
    entry = UsageAnalytics().get("claude")["accounts"][0]
    assert not entry["available"] and "regular local file" in entry["error"]


def test_claude_permission_errors_do_not_expose_paths(claude_cache, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError("private-token in /private/home/stats-cache.json")

    monkeypatch.setattr(analytics.os, "open", denied)
    result = UsageAnalytics().get("claude")
    assert not result["accounts"][0]["available"]
    assert "private" not in json.dumps(result)


@pytest.mark.parametrize("provider", ["other", "CLAUDE", "", None, {"private": "token"}])
def test_unsupported_provider_is_rejected_without_echoing_input(provider):
    with pytest.raises(ValueError, match=r"^Unsupported analytics provider\.$"):
        UsageAnalytics().get(provider)
