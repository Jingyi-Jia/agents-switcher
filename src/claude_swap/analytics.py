"""On-demand, display-only usage history, separate from quota and switching.

Codex reports account-wide totals from its profile endpoint. Claude reports only
this device's stats cache, never the selected account's history. Claude daily
tokens are the cache's explicit ``dailyModelTokens`` buckets, without filling
missing days. Its lifetime total sums complete model input/output components;
cache-read and cache-write components are exposed separately, never added again
to either token series. Its peak is the maximum reported daily token bucket.

``fetchedAt`` is a Unix timestamp for the read, not the reporting date.
``dataThrough`` is the provider/cache's explicit date, never today's date by
default. Yesterday is normal for daily reporting; older or undated history is
marked stale. A failed Codex refresh retains available data with its original
timestamp, ``stale: true``, and a safe error. No cache is persisted to disk.
"""

from __future__ import annotations

import copy
import json
import os
import re
import stat
import sys
import threading
import time
from dataclasses import dataclass
from datetime import date, timedelta

from claude_swap import paths
from claude_swap.codex.usage import UsageAuthError

_CACHE_TTL_S = 300.0
_MAX_CACHE_BYTES = 8 * 1024 * 1024
_MAX_DAYS = 3660
_MAX_MODELS = 256
_MAX_INVOCATIONS = 100
_MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}\Z")


class _AnalyticsError(Exception):
    pass


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if 0 <= value <= sys.float_info.max else None


def _day(value):
    if not isinstance(value, str) or len(value) != 10:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return value if parsed.isoformat() == value else None


def _text(value, *, limit=160):
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > limit or not value.isprintable():
        return None
    return value


def _sum(values):
    if not values or any(value is None for value in values):
        return None
    return _number(sum(values))


def _entry(number=None, label="This device", *, fetched_at):
    return {
        "number": number,
        "label": label,
        "available": False,
        "stale": False,
        "error": None,
        "fetchedAt": fetched_at,
        "dataThrough": None,
        "summary": {
            "lifetimeTokens": None,
            "peakDailyTokens": None,
            "currentStreakDays": None,
            "longestStreakDays": None,
            "totalSessions": None,
            "totalMessages": None,
            "totalThreads": None,
        },
        "daily": [],
        "models": [],
        "dailyModels": [],
        "insights": {
            "fastModePercent": None,
            "topReasoningEffort": None,
            "topInvocations": [],
        },
    }


def _dated_rows(value, through):
    rows = {}
    ceiling = min(through, date.today().isoformat()) if through else date.today().isoformat()
    if isinstance(value, (list, tuple)):
        for row in value:
            if not isinstance(row, dict):
                continue
            day = _day(row.get("date"))
            if day is not None and day <= ceiling:
                rows[day] = row
    return rows


def _stale(through):
    today = date.today()
    return through is None or not (today - timedelta(days=1)).isoformat() <= through <= today.isoformat()


def _finish(entry, *, unavailable):
    entry["available"] = (
        any(value is not None for value in entry["summary"].values())
        or any(
            row[field] is not None
            for row in entry["daily"]
            for field in ("tokens", "messages", "sessions")
        )
        or any(
            model[field] is not None
            for model in entry["models"]
            for field in ("inputTokens", "outputTokens", "cacheReadTokens", "cacheWriteTokens")
        )
        or bool(entry["dailyModels"])
        or any(value is not None and value != [] for value in entry["insights"].values())
    )
    if not entry["available"]:
        entry["error"] = unavailable
    entry["stale"] = _stale(entry["dataThrough"])
    return entry


def _codex_entry(profile, stamp):
    raw = profile.raw
    if raw:
        if not isinstance(raw, dict):
            raise _AnalyticsError("Codex statistics have an unsupported format.")
        metadata = raw.get("metadata", {})
        stats = raw.get("stats", {})
        if not isinstance(metadata, dict) or not isinstance(stats, dict):
            raise _AnalyticsError("Codex statistics have an unsupported format.")
        through = _day(metadata.get("stats_as_of"))
        if metadata.get("stats_error") not in (None, False, ""):
            entry = _entry(fetched_at=stamp)
            entry["dataThrough"] = through
            return _finish(entry, unavailable="Codex statistics are temporarily unavailable.")
    else:
        stats = {
            "lifetime_tokens": profile.lifetime_tokens,
            "peak_daily_tokens": profile.peak_daily_tokens,
            "current_streak_days": profile.current_streak_days,
            "longest_streak_days": profile.longest_streak_days,
            "total_threads": profile.total_threads,
            "fast_mode_usage_percentage": profile.fast_mode_percent,
            "most_used_reasoning_effort": profile.top_reasoning_effort,
            "daily_usage_buckets": [
                {"start_date": row.start_date, "tokens": row.tokens}
                for row in profile.daily
            ],
            "top_invocations": [
                {"type": row.kind, "plugin_name": row.name, "usage_count": row.usage_count}
                for row in profile.top_invocations
            ],
        }
        through = _day(profile.stats_as_of)
    entry = _entry(fetched_at=stamp)
    entry["dataThrough"] = through
    for output, source in (
        ("lifetimeTokens", "lifetime_tokens"),
        ("peakDailyTokens", "peak_daily_tokens"),
        ("currentStreakDays", "current_streak_days"),
        ("longestStreakDays", "longest_streak_days"),
        ("totalThreads", "total_threads"),
    ):
        entry["summary"][output] = _number(stats.get(source))
    buckets = stats.get("daily_usage_buckets")
    daily = _dated_rows([
        {"date": row.get("start_date"), "tokens": row.get("tokens")}
        for row in buckets if isinstance(row, dict)
    ] if isinstance(buckets, (list, tuple)) else [], through)
    entry["daily"] = [
        {"date": day, "tokens": _number(daily[day].get("tokens")), "messages": None, "sessions": None}
        for day in sorted(daily)[-_MAX_DAYS:]
    ]
    percent = _number(stats.get("fast_mode_usage_percentage"))
    entry["insights"]["fastModePercent"] = percent if percent is not None and percent <= 100 else None
    effort = stats.get("most_used_reasoning_effort")
    if effort in ("none", "minimal", "low", "medium", "high", "xhigh", "max"):
        entry["insights"]["topReasoningEffort"] = effort
    invocations = stats.get("top_invocations")
    if isinstance(invocations, (list, tuple)):
        unique = {}
        for row in invocations:
            if not isinstance(row, dict) or row.get("type") not in ("plugin", "skill"):
                continue
            kind = row["type"]
            name = _text(row.get("plugin_name") or row.get("skill_name"))
            count = _number(row.get("usage_count"))
            if name is not None and count is not None:
                unique[kind, name] = {"kind": kind, "name": name, "usageCount": count}
        entry["insights"]["topInvocations"] = sorted(
            unique.values(), key=lambda row: (-row["usageCount"], row["kind"], row["name"])
        )[:_MAX_INVOCATIONS]
    return _finish(entry, unavailable="Codex statistics are unavailable.")


def _regular(info):
    return stat.S_ISREG(info.st_mode) and not (
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _read_claude_cache():
    root = paths.get_claude_config_home()
    root_info = root.lstat()
    if not stat.S_ISDIR(root_info.st_mode) or (
        getattr(root_info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise _AnalyticsError("Claude statistics cache must be a regular local file.")
    root = root.resolve(strict=True)
    path = root / "stats-cache.json"
    before = path.lstat()
    if not _regular(before):
        raise _AnalyticsError("Claude statistics cache must be a regular local file.")
    if before.st_size > _MAX_CACHE_BYTES:
        raise _AnalyticsError("Claude statistics cache exceeds the size limit.")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        current = path.lstat()
        if (
            not _regular(opened) or not _regular(current)
            or not os.path.samestat(before, opened)
            or not os.path.samestat(current, opened)
            or path.resolve(strict=True).parent != root
        ):
            raise _AnalyticsError("Claude statistics cache changed during the read.")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            content = handle.read(_MAX_CACHE_BYTES + 1)
        after = os.fstat(fd)
        if len(content) > _MAX_CACHE_BYTES:
            raise _AnalyticsError("Claude statistics cache exceeds the size limit.")
        if (opened.st_size, opened.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise _AnalyticsError("Claude statistics cache changed during the read.")
    finally:
        os.close(fd)
    try:
        payload = json.loads(content)
    except (ValueError, UnicodeError, RecursionError):
        raise _AnalyticsError("Claude statistics cache is not valid JSON.") from None
    return payload


def _claude_entry(payload, stamp):
    if not isinstance(payload, dict):
        raise _AnalyticsError("Claude statistics cache has an unsupported format.")
    if type(payload.get("version")) is not int or payload["version"] not in (1, 2):
        raise _AnalyticsError("Claude statistics cache has an unsupported version.")
    for field, expected in (("dailyActivity", list), ("dailyModelTokens", list), ("modelUsage", dict)):
        if field in payload and not isinstance(payload[field], expected):
            raise _AnalyticsError("Claude statistics cache has an unsupported format.")
    entry = _entry(fetched_at=stamp)
    through = _day(payload.get("lastComputedDate"))
    entry["dataThrough"] = through
    entry["summary"]["totalSessions"] = _number(payload.get("totalSessions"))
    entry["summary"]["totalMessages"] = _number(payload.get("totalMessages"))
    models = payload.get("modelUsage", {})
    if len(models) > _MAX_MODELS:
        raise _AnalyticsError("Claude statistics cache contains too many models.")
    for name, row in sorted(models.items()):
        if not _MODEL_NAME.fullmatch(name) or not isinstance(row, dict):
            continue
        entry["models"].append({
            "name": name,
            "inputTokens": _number(row.get("inputTokens")),
            "outputTokens": _number(row.get("outputTokens")),
            "cacheReadTokens": _number(row.get("cacheReadInputTokens")),
            "cacheWriteTokens": _number(row.get("cacheCreationInputTokens")),
        })
    entry["summary"]["lifetimeTokens"] = _sum([
        _sum([row["inputTokens"], row["outputTokens"]]) for row in entry["models"]
    ]) if len(entry["models"]) == len(models) else None
    activity = _dated_rows(payload.get("dailyActivity"), through)
    tokens = _dated_rows(payload.get("dailyModelTokens"), through)
    days = sorted(activity.keys() | tokens.keys())
    totals = {}
    daily_models = {}
    for day, row in tokens.items():
        buckets = row.get("tokensByModel")
        if not isinstance(buckets, dict):
            continue
        if len(buckets) > _MAX_MODELS:
            raise _AnalyticsError("Claude statistics cache contains too many models.")
        valid = {
            name: count for name, count in sorted(buckets.items())
            if _MODEL_NAME.fullmatch(name) and type(count) is int and _number(count) is not None
        }
        totals[day] = _sum(list(valid.values())) if len(valid) == len(buckets) else None
        if valid:
            daily_models[day] = valid
    known_totals = [value for value in totals.values() if value is not None]
    entry["summary"]["peakDailyTokens"] = max(known_totals, default=None)
    for day in days[-_MAX_DAYS:]:
        row = activity.get(day, {})
        entry["daily"].append({
            "date": day,
            "tokens": totals.get(day),
            "messages": _number(row.get("messageCount")),
            "sessions": _number(row.get("sessionCount")),
        })
        if day in daily_models:
            entry["dailyModels"].append({"date": day, "tokensByModel": daily_models[day]})
    return _finish(entry, unavailable="Claude statistics cache contains no reported usage.")


@dataclass
class _CachedStats:
    entry: dict
    checked_at: float


class UsageAnalytics:
    """Read usage only when requested, without token rotation or UI-action locks.

    A private Codex lock permits at most one profile request at a time, and
    overlapping refresh requests share the completed batch. Each slot/account
    identity/added-date cache has a five-minute TTL. Roster reads bracket every
    batch so removed or replaced slots cannot receive an old identity's data.
    Claude has a separate lock and rereads its bounded local file on each call.
    """

    def __init__(self, codex_switcher=None):
        self._codex = codex_switcher
        self._codex_lock = threading.Lock()
        self._claude_lock = threading.Lock()
        self._cache: dict[tuple, _CachedStats] = {}
        self._refreshed_at = float("-inf")

    def get(self, provider: str, *, force=False) -> dict:
        if provider not in ("codex", "claude"):
            raise ValueError("Unsupported analytics provider.")
        result = {
            "provider": provider,
            "scope": "account" if provider == "codex" else "local",
            "source": "codex-profile-stats" if provider == "codex" else "claude-stats-cache",
            "fetchedAt": time.time(),
            "accounts": [],
            "error": None,
        }
        if provider == "codex":
            requested_at = time.monotonic()
            with self._codex_lock:
                try:
                    result["accounts"] = self._codex_accounts(
                        force=bool(force) and self._refreshed_at < requested_at
                    )
                except Exception:
                    self._cache.clear()
                    result["error"] = "Saved Codex accounts could not be read."
        else:
            with self._claude_lock:
                entry = _entry(fetched_at=time.time())
                try:
                    entry = _claude_entry(_read_claude_cache(), time.time())
                except _AnalyticsError as exc:
                    entry["error"] = str(exc)
                except FileNotFoundError:
                    entry["error"] = "Claude statistics cache is not available on this device."
                except Exception:
                    entry["error"] = "Claude statistics cache could not be read."
                result["accounts"] = [entry]
                result["error"] = entry["error"]
        result["fetchedAt"] = time.time()
        return result

    @staticmethod
    def _key(account):
        return account.number, account.account_id, account.added

    def _codex_accounts(self, *, force):
        if self._codex is None:
            from claude_swap.codex.switcher import CodexSwitcher

            self._codex = CodexSwitcher()
        accounts = self._codex.list_accounts()
        keys = {self._key(account) for account in accounts}
        self._cache = {key: row for key, row in self._cache.items() if key in keys}
        refreshed = False
        for account in accounts:
            if not _text(account.account_id):
                continue
            key = self._key(account)
            cached = self._cache.get(key)
            if cached and not force and time.monotonic() - cached.checked_at < _CACHE_TTL_S:
                continue
            error = None
            entry = _entry(fetched_at=time.time())
            try:
                stats = self._codex.stats_for(account.number, allow_refresh=False)
                entry = _codex_entry(stats, time.time())
                error = entry["error"]
            except _AnalyticsError as exc:
                error = str(exc)
            except UsageAuthError:
                error = "Codex statistics need a fresh sign-in."
            except Exception:
                error = "Codex statistics could not be loaded."
            if error:
                if cached and cached.entry["available"]:
                    entry = copy.deepcopy(cached.entry)
                    entry["stale"] = True
                entry["error"] = error
            self._cache[key] = _CachedStats(entry, time.monotonic())
            refreshed = True
        accounts = self._codex.list_accounts()
        keys = {self._key(account) for account in accounts}
        self._cache = {key: row for key, row in self._cache.items() if key in keys}
        entries = []
        for account in accounts:
            cached = self._cache.get(self._key(account))
            entry = copy.deepcopy(cached.entry) if cached else _entry(fetched_at=time.time())
            entry["number"] = str(account.number)
            entry["label"] = _text(account.alias) or _text(account.display_label) or f"Account {account.number}"
            if entry["available"] and _stale(entry["dataThrough"]):
                entry["stale"] = True
            if not cached:
                entry["error"] = (
                    "Saved Codex account changed; request usage again."
                    if _text(account.account_id) else "Saved Codex account identity is unavailable."
                )
            entries.append(entry)
        if refreshed:
            self._refreshed_at = time.monotonic()
        return entries
