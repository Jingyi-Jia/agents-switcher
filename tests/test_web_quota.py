from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from claude_swap.codex.usage import CodexUsage, CodexWindow
from claude_swap.usage_store import UsageEntry
from claude_swap.web.server import DashboardState, _claude_windows, _quota_window
from tests.test_provider_actions import Claude, Codex
from tests.test_web_page_actions import node, run_page


NOW = datetime(2026, 9, 25, 12, tzinfo=timezone.utc).timestamp()


@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setattr("claude_swap.web.server.time.time", lambda: NOW)


def test_relative_reset_is_anchored_to_the_measurement_not_the_render(clock):
    window = _quota_window("5h", 40, 18000, None, 3600, NOW - 120)
    assert window == {
        "label": "5h", "usedPercent": 40, "windowSeconds": 18000,
        "resetAt": NOW + 3480, "resetAfterSeconds": 3480, "observedAt": NOW - 120,
    }


def test_absolute_reset_wins_over_the_relative_value_and_preserves_expiry(clock):
    future = _quota_window("7d", 40, 604800, NOW + 3600, 45, NOW)
    assert future["resetAt"] == NOW + 3600
    assert future["resetAfterSeconds"] == 3600
    expired = _quota_window("5h", 40, 18000, NOW - 60, 3600, NOW - 120)
    assert expired["resetAt"] == NOW - 60
    assert expired["resetAfterSeconds"] == 0


@pytest.mark.parametrize("value", [None, True, False, "3600", -1, float("nan"), float("inf"), 1e30])
def test_unreliable_relative_resets_never_become_dates(clock, value):
    assert _quota_window("5h", 40, 18000, None, value, NOW)["resetAt"] is None


@pytest.mark.parametrize("value", [None, True, False, "1234", 0, -1, float("nan"), float("inf"), 1e30])
def test_relative_reset_requires_a_valid_observation_time(clock, value):
    window = _quota_window("5h", 40, 18000, None, 3600, value)
    assert window["resetAt"] is None
    assert window["observedAt"] is None
    json.dumps(window, allow_nan=False)


@pytest.mark.parametrize("value", [True, False, "1234", 0, -1, float("nan"), float("inf"), 1e30])
def test_invalid_absolute_reset_is_not_overridden_by_a_relative_guess(clock, value):
    window = _quota_window("5h", 40, 18000, value, 3600, NOW)
    assert window["resetAt"] is None
    json.dumps(window, allow_nan=False)


@pytest.mark.parametrize("absolute,relative", [(NOW + 18001, None), (None, 18001)])
def test_reset_outside_the_reported_window_is_unknown(clock, absolute, relative):
    window = _quota_window("5h", 40, 18000, absolute, relative, NOW)
    assert window["resetAt"] is None
    assert window["resetAfterSeconds"] is None


@pytest.mark.parametrize("value", [None, True, False, "18000", 0, -1, float("nan"), float("inf"), 1e30])
def test_invalid_window_length_stays_unknown_without_losing_a_valid_reset(clock, value):
    window = _quota_window("?", 40, value, NOW + 3600, None, NOW)
    assert window["windowSeconds"] is None
    assert window["resetAt"] == NOW + 3600
    json.dumps(window, allow_nan=False)


def test_claude_windows_parse_timezone_aware_dates_and_keep_the_source_unchanged(clock):
    source = {
        "five_hour": {"pct": 42.1, "resets_at": "2026-09-25T15:00:00+02:00"},
        "seven_day": {"pct": 72, "resets_at": "2026-10-01T12:00:00Z"},
    }
    original = copy.deepcopy(source)
    windows = _claude_windows(source, NOW)
    assert windows == [
        {"label": "5h", "usedPercent": 42, "windowSeconds": 18000, "resetAt": NOW + 3600,
         "resetAfterSeconds": 3600, "observedAt": NOW},
        {"label": "7d", "usedPercent": 72, "windowSeconds": 604800, "resetAt": NOW + 518400,
         "resetAfterSeconds": 518400, "observedAt": NOW},
    ]
    assert source == original


@pytest.mark.parametrize("value", [None, True, 123, "invalid", "2026-09-25", "2026-09-25T13:00:00", "2026-13-25T13:00:00Z"])
def test_claude_does_not_guess_a_timezone_or_a_reset(clock, value):
    window = _claude_windows({"five_hour": {"pct": 42, "resets_at": value}}, NOW)[0]
    assert window["resetAt"] is None
    assert window["resetAfterSeconds"] is None
    assert window["usedPercent"] == 42


def test_collected_windows_preserve_each_providers_measurement_time(clock, tmp_path):
    claude = Claude()
    usage = UsageEntry(last_good={"five_hour": {"pct": 20, "resets_at": "2026-09-25T13:00:00Z"}}, fetched_at=NOW - 120)
    claude.accounts = [SimpleNamespace(
        usage=usage, number="1", email="sample@example.test", alias="Work", display_tag="Pro",
        is_active=True, disabled=False, kind="oauth", switchable=True,
    )]
    codex = Codex(tmp_path / "codex")
    codex.usage["1"] = CodexUsage(windows=(CodexWindow(45, 604800, reset_after_seconds=86400),), fetched_at=NOW - 240)
    state = DashboardState(claude, codex)
    try:
        claude_result = state._collect_claude()
        codex_result = state._collect_codex()
        assert claude_result["available"] is True
        assert codex_result["available"] is True
        assert claude_result["accounts"][0]["windows"][0]["observedAt"] == NOW - 120
        assert claude_result["accounts"][0]["percent"] == 20
        assert codex_result["accounts"][0]["windows"][0]["resetAt"] == NOW - 240 + 86400
        assert codex_result["accounts"][0]["windows"][0]["observedAt"] == NOW - 240
        assert codex_result["accounts"][0]["percent"] == 45
        assert claude.calls == codex.calls == []
        assert usage.fetched_at == NOW - 120
        assert state.actions.auto.status("claude")["mode"] == "stopped"
        assert state.actions.auto.status("codex")["mode"] == "stopped"
    finally:
        state.close()


@pytest.mark.parametrize("zone", ["UTC", "America/Los_Angeles", "Asia/Tokyo"])
def test_exact_reset_dates_are_visible_in_the_local_timezone(node, zone):
    run_page(node, r"""
const now = Date.UTC(2026, 8, 25, 12) / 1000;
Date.now = () => now * 1000;
for (const [label, windowSeconds, remaining] of [['5h', 18000, 3600], ['7d', 604800, 172800]]) {
  const window = {label, usedPercent: 42, windowSeconds, observedAt: now, resetAt: now + remaining};
  const result = meter(window);
  const date = new Date(window.resetAt * 1000);
  const time = nodes(result).find(n => n.tagName === 'TIME');
  assert.equal(time.dateTime, date.toISOString());
  assert.equal(time.textContent, date.toLocaleString(undefined, {month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short'}));
  assert.equal(time.title, date.toLocaleString(undefined, {dateStyle: 'full', timeStyle: 'long'}));
  assert(result.textContent.includes(time.textContent));
  assert.match(result.textContent, /Resets/);
  assert.equal(posts().length, 0);
}
""", setup="process.env.TZ = " + json.dumps(zone) + ";")


def test_even_pace_uses_the_observation_not_time_elapsed_since_a_cached_read(node):
    run_page(node, r"""
let now = Date.UTC(2026, 8, 25, 12) / 1000;
Date.now = () => now * 1000;
const window = {label: '5h', usedPercent: 50, windowSeconds: 18000, observedAt: now, resetAt: now + 9000};
assert.equal(quotaTiming(window).pace, 'Near even pace');
assert.equal(quotaTiming({...window, usedPercent: 55}).pace, 'Near even pace');
assert.equal(quotaTiming({...window, usedPercent: 56}).pace, 'Above even pace');
assert.equal(quotaTiming({...window, usedPercent: 44}).pace, 'Below even pace');
const hint = quotaTiming(window).hint;
now += 300;
assert.equal(quotaTiming(window).hint, hint);
assert.equal(quotaTiming(window).remaining, 8700);
now += 1;
assert.equal(quotaTiming(window).pace, 'Pace unavailable');
assert.equal(quotaTiming(window).date.getTime(), window.resetAt * 1000);
assert.match(hint, /last report.*not a forecast or a switching rule/);
""")


def test_unknown_expired_and_inconsistent_timing_never_fabricates_pace_or_dates(node):
    run_page(node, r"""
const now = Date.UTC(2026, 8, 25, 12) / 1000;
Date.now = () => now * 1000;
const window = {label: '5h', usedPercent: 50, windowSeconds: 18000, observedAt: now, resetAt: now + 9000};
for (const override of [{observedAt: null}, {observedAt: now + 1}, {observedAt: now - 301}, {windowSeconds: null}, {windowSeconds: 0}, {resetAt: now - 1}, {resetAt: now + 18000}, {resetAt: now + 18001}, {usedPercent: null}, {usedPercent: NaN}, {usedPercent: -1}, {usedPercent: 101}]) {
  assert.equal(quotaTiming({...window, ...override}).pace, 'Pace unavailable', JSON.stringify(override));
}
for (const resetAt of [null, 0, true, '1234', NaN, Infinity, 1e30, now + 18001]) {
  const result = meter({...window, resetAt, resetAfterSeconds: 100});
  assert.match(result.textContent, /Reset time unavailable/);
  assert.equal(nodes(result).filter(n => n.tagName === 'TIME').length, 0);
}
const expired = meter({...window, resetAt: now - 1});
assert.match(expired.textContent, /Reported reset.*Refresh usage for the new window.*Pace unavailable/);
assert.equal(duration(10), '<1m');
assert.equal(duration(0), null);
assert.equal(duration(Infinity), null);
""")


def test_failed_or_paid_credit_samples_do_not_show_pace_guidance(node):
    run_page(node, r"""
const now = Date.now() / 1000;
Date.now = () => now * 1000;
const windows = [{label: '5h', usedPercent: 20, windowSeconds: 18000, observedAt: now, resetAt: now + 9000}];
for (const flags of [{error: 'Unavailable'}, {sentinel: 'stale'}, {onCredits: true}]) {
  const account = card('codex', {number: '9', windows, ...flags}, apiState.codex);
  assert.match(account.textContent, /Pace unavailable/);
  assert.doesNotMatch(account.textContent, /Below even pace/);
}
""")
