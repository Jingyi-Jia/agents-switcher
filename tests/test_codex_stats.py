"""Tests for Codex account statistics and rate-limit reset credits.

Fixtures are modelled on real responses from both endpoints.
"""

from __future__ import annotations

import json
import urllib.error

import pytest

from claude_swap.codex import stats as stats_mod
from claude_swap.codex.stats import (
    CodexResetCredits,
    ResetCredit,
    fetch_profile_stats,
    parse_profile_stats,
    parse_reset_credits,
)
from claude_swap.codex.usage import UsageAuthError, UsageError

PROFILE = {
    "metadata": {"generated_at": "2026-09-17T22:18:04Z", "stats_as_of": "2026-09-17",
                 "stats_error": None},
    "profile": {"display_name": "A Person", "username": "aperson",
                "profile_picture_url": "https://example.com/a.png"},
    "stats": {
        "lifetime_tokens": 10530939718,
        "peak_daily_tokens": 1440225072,
        "longest_running_turn_sec": 43916,
        "current_streak_days": 1,
        "longest_streak_days": 26,
        "fast_mode_usage_percentage": 62.05413156479656,
        "most_used_reasoning_effort": "xhigh",
        "most_used_reasoning_effort_percentage": 78.06474954483917,
        "total_threads": 1719,
        "total_skills_used": 1029,
        "unique_skills_used": 36,
        "top_invocations": [
            {"type": "plugin", "plugin_id": "spreadsheets@x", "plugin_name": "spreadsheets",
             "skill_id": None, "skill_name": None, "usage_count": 279},
            {"type": "skill", "plugin_id": None, "plugin_name": None,
             "skill_id": "brainstorming", "skill_name": "brainstorming", "usage_count": 210},
        ],
        "daily_usage_buckets": [{"start_date": "2026-04-12", "tokens": 4414465}],
        "weekly_usage_buckets": [{"start_date": "2026-04-06", "tokens": 4414465}],
        "workspace_rank": None,
    },
}

RESET_CREDITS = {
    "available_count": 0,
    "total_earned_count": 0,
    "credits": [],
    "history_enabled": True,
    "immediate_reset_purchase_eligible": False,
}


class TestProfileStats:
    def test_reads_the_live_shape(self):
        s = parse_profile_stats(PROFILE)
        assert s.lifetime_tokens == 10530939718
        assert s.peak_daily_tokens == 1440225072
        assert s.longest_turn_seconds == 43916
        assert (s.current_streak_days, s.longest_streak_days) == (1, 26)
        assert s.total_threads == 1719
        assert s.unique_skills_used == 36
        assert s.stats_as_of == "2026-09-17"

    def test_names_plugins_and_skills_from_whichever_field_is_set(self):
        items = parse_profile_stats(PROFILE).top_invocations
        assert (items[0].kind, items[0].name) == ("plugin", "spreadsheets")
        assert (items[1].kind, items[1].name) == ("skill", "brainstorming")

    def test_buckets_are_parsed(self):
        s = parse_profile_stats(PROFILE)
        assert s.daily[0].start_date == "2026-04-12"
        assert s.weekly[0].tokens == 4414465

    def test_recent_daily_is_the_tail(self):
        payload = {"stats": {"daily_usage_buckets": [
            {"start_date": f"2026-01-{d:02d}", "tokens": d} for d in range(1, 32)
        ]}}
        recent = parse_profile_stats(payload).recent_daily
        assert len(recent) == 30
        assert recent[-1].start_date == "2026-01-31"

    def test_tolerates_an_empty_payload(self):
        s = parse_profile_stats({})
        assert s.lifetime_tokens is None and s.top_invocations == ()

    def test_ignores_malformed_buckets_and_invocations(self):
        payload = {"stats": {
            "daily_usage_buckets": ["nope", {"tokens": 5}, {"start_date": "d", "tokens": 1}],
            "top_invocations": ["nope"],
        }}
        s = parse_profile_stats(payload)
        assert len(s.daily) == 1
        assert s.top_invocations == ()

    def test_rejects_a_non_object(self):
        with pytest.raises(UsageError):
            parse_profile_stats([1, 2])

    def test_keeps_the_raw_payload(self):
        assert "workspace_rank" in parse_profile_stats(PROFILE).raw["stats"]


class TestResetCredits:
    def test_reads_the_live_empty_shape(self):
        r = parse_reset_credits(RESET_CREDITS)
        assert r.available_count == 0
        assert r.purchase_eligible is False
        assert r.credits == ()

    def test_parses_credit_entries(self):
        payload = dict(RESET_CREDITS, available_count=2, credits=[
            {"id": "c1", "reset_type": "weekly", "status": "available",
             "expires_at": "2026-10-01T00:00:00Z", "title": "Reset"},
            {"id": "c2", "reset_type": "weekly", "status": "redeemed",
             "redeemed_at": "2026-09-01T00:00:00Z"},
        ])
        r = parse_reset_credits(payload)
        assert r.credits[0].is_available is True
        assert r.credits[1].is_available is False

    def test_next_expiry_ignores_spent_credits(self):
        r = CodexResetCredits(credits=(
            ResetCredit(identifier="spent", status="redeemed",
                        redeemed_at="x", expires_at="2026-01-01"),
            ResetCredit(identifier="live", status="available", expires_at="2026-06-01"),
        ))
        assert r.next_expiry == "2026-06-01"

    def test_no_spendable_credits_has_no_expiry(self):
        assert CodexResetCredits().next_expiry is None

    def test_an_unknown_status_is_treated_as_available(self):
        # Over-reporting costs a user a look; under-reporting hides a remedy
        # for an account that is currently exhausted.
        assert ResetCredit(status="some_new_state").is_available is True

    @pytest.mark.parametrize("status", ["redeemed", "expired", "revoked", "EXPIRED"])
    def test_known_spent_states_are_unavailable(self, status):
        assert ResetCredit(status=status).is_available is False


class TestFetching:
    def test_requires_a_token(self):
        with pytest.raises(UsageError, match="no access token"):
            fetch_profile_stats({})

    def test_rejection_is_an_auth_error(self, monkeypatch):
        monkeypatch.setattr(
            stats_mod.urllib.request, "urlopen",
            lambda *a, **k: (_ for _ in ()).throw(
                urllib.error.HTTPError("u", 401, "no", {}, None)),
        )
        with pytest.raises(UsageAuthError):
            fetch_profile_stats({"access_token": "t"})

    def test_success_parses(self, monkeypatch):
        class Response:
            def read(self): return json.dumps(PROFILE).encode()
            def __enter__(self): return self
            def __exit__(self, *a): return False

        monkeypatch.setattr(stats_mod.urllib.request, "urlopen",
                            lambda *a, **k: Response())
        assert fetch_profile_stats({"access_token": "t"}).username == "aperson"
