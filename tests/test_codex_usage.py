"""Tests for reading a Codex account's quota.

Modelled on a REAL /wham/usage response, which carries several fields the
published OpenAPI schema does not.
"""

from __future__ import annotations

import json
import urllib.error

import pytest

from claude_swap.codex import usage as usage_mod
from claude_swap.codex.usage import (
    CodexCredits,
    CodexUsage,
    CodexWindow,
    UsageAuthError,
    UsageError,
    fetch_usage,
    parse_usage,
)

#: Captured from a live Pro account. Note primary_window is SEVEN DAYS and
#: secondary_window is absent -- the primary/secondary naming says nothing
#: about window length.
LIVE = {
    "account_id": "acct-1",
    "email": "person@example.com",
    "plan_type": "pro",
    "rate_limit": {
        "allowed": False,
        "limit_reached": True,
        "primary_window": {
            "used_percent": 100,
            "limit_window_seconds": 604800,
            "reset_after_seconds": 122583,
            "reset_at": 1789805442,
        },
        "secondary_window": None,
    },
    "credits": {
        "has_credits": True,
        "unlimited": False,
        "balance": "844.7392570000",
        "overage_limit_reached": False,
        "approx_local_messages": [211, 1098],
        "approx_cloud_messages": [34, 211],
    },
    "spend_control": {"reached": False, "individual_limit": None},
    "rate_limit_reached_type": {"type": "rate_limit_reached", "details": "default"},
    "rate_limit_reset_credits": {"available_count": 0, "applicable_available_count": 0},
    "model_usage": {"gpt-6-astra": {"available": True, "available_at": None}},
    "additional_rate_limits": None,
    "code_review_rate_limit": None,
    "promo": None,
}


class TestParsing:
    def test_reads_the_live_shape(self):
        u = parse_usage(LIVE)
        assert (u.account_id, u.email, u.plan) == ("acct-1", "person@example.com", "pro")
        assert u.allowed is False and u.limit_reached is True
        assert u.reached_type == "rate_limit_reached"
        assert u.model_availability == {"gpt-6-astra": True}

    def test_window_length_comes_from_the_payload_not_the_name(self):
        # Reading "primary" as "5 hours" would mislabel this by a factor of 33.
        window = parse_usage(LIVE).windows[0]
        assert window.window_seconds == 604800
        assert window.label == "7d"

    def test_absent_secondary_window_is_simply_not_there(self):
        assert len(parse_usage(LIVE).windows) == 1

    def test_keeps_the_raw_payload_including_unmodelled_keys(self):
        raw = parse_usage(LIVE).raw
        for key in ("code_review_rate_limit", "model_usage", "promo"):
            assert key in raw

    def test_tolerates_an_empty_payload(self):
        u = parse_usage({})
        assert u.windows == () and u.binding_percent is None and u.allowed is True

    def test_rejects_a_non_object(self):
        with pytest.raises(UsageError):
            parse_usage(["nope"])

    def test_ignores_a_malformed_window(self):
        payload = {"rate_limit": {"primary_window": {"limit_window_seconds": 300}}}
        assert parse_usage(payload).windows == ()


class TestWindowLabels:
    @pytest.mark.parametrize(
        "seconds,label",
        [(18000, "5h"), (604800, "7d"), (86400, "1d"), (3600, "1h"),
         (1800, "30m"), (0, "?"), (None, "?")],
    )
    def test_derived_from_seconds(self, seconds, label):
        assert CodexWindow(0, seconds or 0).label == label


class TestBindingWindow:
    def test_picks_the_window_closest_to_its_limit(self):
        u = CodexUsage(windows=(CodexWindow(12, 18000), CodexWindow(87, 604800)))
        assert u.binding_percent == 87
        assert u.binding_window.label == "7d"

    def test_next_reset_is_the_soonest_exhausted_one(self):
        u = CodexUsage(windows=(
            CodexWindow(100, 18000, reset_at=500),
            CodexWindow(100, 604800, reset_at=900),
            CodexWindow(10, 3600, reset_at=100),  # not exhausted; irrelevant
        ))
        assert u.next_reset_at == 500

    def test_no_exhausted_window_has_no_reset(self):
        assert CodexUsage(windows=(CodexWindow(10, 3600, reset_at=1),)).next_reset_at is None


class TestUsable:
    """'Exhausted' and 'unusable' are different questions, and a switcher asks
    the second one."""

    def test_allowed_is_usable(self):
        assert CodexUsage(allowed=True).usable is True

    def test_blocked_but_with_credits_is_still_usable(self):
        # The live case: 100% of the weekly window, and $844 of credits.
        u = parse_usage(LIVE)
        assert u.allowed is False
        assert u.usable is True
        assert "on paid credits" in u.summary

    def test_blocked_without_credits_is_not_usable(self):
        u = CodexUsage(allowed=False, credits=CodexCredits(has_credits=False))
        assert u.usable is False

    def test_unlimited_credits_count(self):
        u = CodexUsage(allowed=False, credits=CodexCredits(unlimited=True))
        assert u.usable is True

    def test_credits_past_their_overage_limit_do_not_count(self):
        u = CodexUsage(
            allowed=False,
            credits=CodexCredits(has_credits=True, overage_limit_reached=True),
        )
        assert u.usable is False

    def test_a_spend_control_blocks_even_a_fresh_account(self):
        u = CodexUsage(allowed=True, spend_control_reached=True,
                       windows=(CodexWindow(3, 18000),))
        assert u.usable is False
        assert "spend limit" in u.summary


class TestCreditsPolicy:
    """Spending money is the user's decision, not a background loop's.

    An account past its included quota still WORKS on credits, so a manual
    switch to it is fine -- but every request then costs money, so the
    auto-switcher must never select it and must never count it as spare
    capacity when choosing where to go.
    """

    def test_the_live_case_is_usable_but_not_auto_switchable(self):
        u = parse_usage(LIVE)
        assert u.usable is True          # manual switch: fine
        assert u.on_credits is True
        assert u.auto_switch_eligible is False   # automatic switch: never

    def test_a_healthy_account_is_auto_switchable(self):
        u = CodexUsage(allowed=True, windows=(CodexWindow(12, 18000),))
        assert u.on_credits is False
        assert u.auto_switch_eligible is True

    def test_an_account_within_quota_is_not_on_credits_even_holding_some(self):
        u = CodexUsage(allowed=True, credits=CodexCredits(has_credits=True))
        assert u.on_credits is False
        assert u.auto_switch_eligible is True

    def test_an_unusable_account_is_not_auto_switchable_either(self):
        u = CodexUsage(allowed=False, credits=CodexCredits(has_credits=False))
        assert u.auto_switch_eligible is False

    def test_a_spend_blocked_account_is_never_auto_switchable(self):
        u = CodexUsage(allowed=True, spend_control_reached=True)
        assert u.auto_switch_eligible is False

    def test_the_summary_says_manual_switch_only(self):
        assert "manual switch only" in parse_usage(LIVE).summary


class TestCreditBalanceIsNotCurrency:
    def test_message_estimates_are_exposed_as_a_range(self):
        # The balance is a count of CREDITS; 844.74 credits sat alongside a
        # roughly $40 purchase, so the two are not interchangeable. The message
        # estimate is the figure that means something to a person.
        credits = parse_usage(LIVE).credits
        assert credits.balance == "844.7392570000"
        assert credits.approx_local_messages == (211, 1098)
        assert credits.approx_messages_label == "~211-1098 messages"

    def test_summary_quotes_messages_never_a_currency(self):
        summary = parse_usage(LIVE).summary
        assert "messages" in summary
        assert "$" not in summary

    def test_a_single_estimate_is_not_rendered_as_a_range(self):
        c = CodexCredits(has_credits=True, approx_local_messages=(50,))
        assert c.approx_messages_label == "~50 messages"

    def test_no_estimate_yields_no_label(self):
        assert CodexCredits(has_credits=True).approx_messages_label == ""

    def test_falls_back_to_cloud_estimates(self):
        c = CodexCredits(has_credits=True, approx_cloud_messages=(34, 211))
        assert c.approx_messages_label == "~34-211 messages"


class TestFeatureLimits:
    def test_additional_rate_limits_are_captured(self):
        payload = dict(LIVE, additional_rate_limits=[{
            "limit_name": "gpt-6-astra weekly",
            "metered_feature": "astra",
            "rate_limit": {
                "allowed": True, "limit_reached": False,
                "primary_window": {"used_percent": 40, "limit_window_seconds": 604800},
            },
        }])
        limits = parse_usage(payload).feature_limits
        assert limits[0].name == "gpt-6-astra weekly"
        assert limits[0].windows[0].used_percent == 40

    def test_code_review_limit_is_captured_though_undocumented(self):
        payload = dict(LIVE, code_review_rate_limit={
            "allowed": False, "limit_reached": True,
            "primary_window": {"used_percent": 100, "limit_window_seconds": 86400},
        })
        limits = parse_usage(payload).feature_limits
        assert limits[0].name == "code_review"
        assert limits[0].limit_reached is True


class TestFetch:
    def _http_error(self, code):
        return urllib.error.HTTPError("u", code, "err", {}, None)

    def test_rejects_a_tokenless_account(self):
        with pytest.raises(UsageError, match="no access token"):
            fetch_usage({})

    @pytest.mark.parametrize("code", [401, 403])
    def test_rejected_token_is_an_auth_error(self, monkeypatch, code):
        # Distinct from UsageError so the caller knows to refresh and retry.
        monkeypatch.setattr(usage_mod.urllib.request, "urlopen",
                            lambda *a, **k: (_ for _ in ()).throw(self._http_error(code)))
        with pytest.raises(UsageAuthError):
            fetch_usage({"access_token": "t"})

    def test_rate_limited_endpoint_is_a_plain_error(self, monkeypatch):
        monkeypatch.setattr(usage_mod.urllib.request, "urlopen",
                            lambda *a, **k: (_ for _ in ()).throw(self._http_error(429)))
        with pytest.raises(UsageError, match="429"):
            fetch_usage({"access_token": "t"})

    def test_network_failure_is_an_error(self, monkeypatch):
        monkeypatch.setattr(usage_mod.urllib.request, "urlopen",
                            lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("down")))
        with pytest.raises(UsageError):
            fetch_usage({"access_token": "t"})

    def test_success_parses(self, monkeypatch):
        class Response:
            def read(self): return json.dumps(LIVE).encode()
            def __enter__(self): return self
            def __exit__(self, *a): return False

        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["headers"] = dict(request.headers)
            return Response()

        monkeypatch.setattr(usage_mod.urllib.request, "urlopen", fake_urlopen)
        result = fetch_usage({"access_token": "tok", "account_id": "acct-1"})
        assert result.plan == "pro"
        # Only what the endpoint needs: no browser impersonation, which the
        # live endpoint was measured not to require.
        assert "Origin" not in captured["headers"]
        assert "Sec-fetch-mode" not in captured["headers"]
