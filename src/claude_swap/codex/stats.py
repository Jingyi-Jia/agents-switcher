"""Account statistics and rate-limit reset credits.

Two endpoints beyond the quota read, neither of which affects switching -- this
is reporting, and nothing here should ever gate an automatic decision:

* ``GET /backend-api/wham/profiles/me`` -- lifetime totals, streaks, the daily
  and weekly token series, and which plugins and skills an account leans on.
* ``GET /backend-api/wham/rate-limit-reset-credits`` -- the credits that reset
  an exhausted rate-limit window early.

RESET CREDITS ARE NOT THE SAME THING AS BILLING CREDITS, despite the name.
``credits`` in the quota response is a pay-as-you-go balance that lets requests
continue past an exhausted window, billed per request. A RESET credit clears the
window itself. The two are tracked separately here because confusing them would
mean telling a user they can keep working for free when they would in fact be
paying, or the reverse.

Both endpoints are GETs and cost no quota. Like the quota reader, the typed
model is a view and the whole payload is kept on ``raw`` -- these responses
carry fields (``workspace_rank``, ``history_enabled``,
``immediate_reset_purchase_eligible``) that no published schema describes.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from claude_swap.codex.usage import UsageAuthError, UsageError

PROFILE_URL = "https://chatgpt.com/backend-api/wham/profiles/me"
RESET_CREDITS_URL = "https://chatgpt.com/backend-api/wham/rate-limit-reset-credits"
_TIMEOUT_S = 30


@dataclass(frozen=True)
class UsageBucket:
    """Tokens consumed in one day or week."""

    start_date: str
    tokens: int


@dataclass(frozen=True)
class TopInvocation:
    """A plugin or skill the account uses most."""

    kind: str
    name: str
    usage_count: int
    identifier: str = ""


@dataclass(frozen=True)
class CodexProfileStats:
    """Lifetime activity for one account. Display only."""

    display_name: str = ""
    username: str = ""
    lifetime_tokens: int | None = None
    peak_daily_tokens: int | None = None
    longest_turn_seconds: int | None = None
    current_streak_days: int | None = None
    longest_streak_days: int | None = None
    fast_mode_percent: float | None = None
    top_reasoning_effort: str = ""
    top_reasoning_effort_percent: float | None = None
    total_threads: int | None = None
    total_skills_used: int | None = None
    unique_skills_used: int | None = None
    top_invocations: tuple[TopInvocation, ...] = ()
    daily: tuple[UsageBucket, ...] = ()
    weekly: tuple[UsageBucket, ...] = ()
    stats_as_of: str = ""
    generated_at: str = ""
    fetched_at: float = 0.0
    raw: dict = field(default_factory=dict)

    @property
    def recent_daily(self) -> tuple[UsageBucket, ...]:
        """The last 30 days, oldest first, for a sparkline."""
        return self.daily[-30:]


@dataclass(frozen=True)
class ResetCredit:
    """One credit that clears an exhausted rate-limit window."""

    identifier: str = ""
    reset_type: str = ""
    status: str = ""
    granted_at: str | None = None
    expires_at: str | None = None
    redeemed_at: str | None = None
    title: str = ""
    description: str = ""

    @property
    def is_available(self) -> bool:
        """Whether this credit can still be spent.

        An explicitly redeemed credit is spent whatever its status string says;
        otherwise an unknown status is treated as available, because the cost of
        over-reporting one is a user checking, while under-reporting hides a
        remedy for an exhausted account.
        """
        if self.redeemed_at:
            return False
        return self.status.lower() not in ("redeemed", "expired", "revoked")


@dataclass(frozen=True)
class CodexResetCredits:
    """The reset credits an account holds."""

    available_count: int = 0
    total_earned_count: int = 0
    purchase_eligible: bool = False
    credits: tuple[ResetCredit, ...] = ()
    fetched_at: float = 0.0
    raw: dict = field(default_factory=dict)

    @property
    def next_expiry(self) -> str | None:
        """Earliest expiry among still-spendable credits."""
        expiries = sorted(
            c.expires_at for c in self.credits if c.is_available and c.expires_at
        )
        return expiries[0] if expiries else None


def _get(url: str, tokens: dict) -> dict:
    access_token = (tokens or {}).get("access_token")
    if not access_token:
        raise UsageError("no access token for this account")
    request = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {access_token}",
        "chatgpt-account-id": (tokens or {}).get("account_id") or "",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise UsageAuthError(f"request rejected: HTTP {e.code}") from e
        raise UsageError(f"request failed: HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise UsageError(f"request failed: {e}") from e
    except ValueError as e:
        raise UsageError(f"response was not JSON: {e}") from e
    if not isinstance(payload, dict):
        raise UsageError("response was not an object")
    return payload


def _buckets(raw) -> tuple[UsageBucket, ...]:
    if not isinstance(raw, list):
        return ()
    out = []
    for entry in raw:
        if isinstance(entry, dict) and "start_date" in entry:
            out.append(UsageBucket(
                start_date=str(entry.get("start_date") or ""),
                tokens=int(entry.get("tokens") or 0),
            ))
    return tuple(out)


def parse_profile_stats(payload: dict, *, fetched_at: float | None = None) -> CodexProfileStats:
    """Build the typed view of a ``/wham/profiles/me`` response."""
    if not isinstance(payload, dict):
        raise UsageError("profile response was not an object")
    profile = payload.get("profile") or {}
    stats = payload.get("stats") or {}
    metadata = payload.get("metadata") or {}

    invocations = []
    for entry in stats.get("top_invocations") or []:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("type") or "")
        invocations.append(TopInvocation(
            kind=kind,
            name=str(entry.get("plugin_name") or entry.get("skill_name") or "unknown"),
            usage_count=int(entry.get("usage_count") or 0),
            identifier=str(entry.get("plugin_id") or entry.get("skill_id") or ""),
        ))

    def number(key):
        value = stats.get(key)
        return value if isinstance(value, (int, float)) else None

    return CodexProfileStats(
        display_name=str(profile.get("display_name") or ""),
        username=str(profile.get("username") or ""),
        lifetime_tokens=number("lifetime_tokens"),
        peak_daily_tokens=number("peak_daily_tokens"),
        longest_turn_seconds=number("longest_running_turn_sec"),
        current_streak_days=number("current_streak_days"),
        longest_streak_days=number("longest_streak_days"),
        fast_mode_percent=number("fast_mode_usage_percentage"),
        top_reasoning_effort=str(stats.get("most_used_reasoning_effort") or ""),
        top_reasoning_effort_percent=number("most_used_reasoning_effort_percentage"),
        total_threads=number("total_threads"),
        total_skills_used=number("total_skills_used"),
        unique_skills_used=number("unique_skills_used"),
        top_invocations=tuple(invocations),
        daily=_buckets(stats.get("daily_usage_buckets")),
        weekly=_buckets(stats.get("weekly_usage_buckets")),
        stats_as_of=str(metadata.get("stats_as_of") or ""),
        generated_at=str(metadata.get("generated_at") or ""),
        fetched_at=fetched_at if fetched_at is not None else time.time(),
        raw=payload,
    )


def parse_reset_credits(payload: dict, *, fetched_at: float | None = None) -> CodexResetCredits:
    """Build the typed view of a ``/wham/rate-limit-reset-credits`` response."""
    if not isinstance(payload, dict):
        raise UsageError("reset-credits response was not an object")
    entries = []
    for entry in payload.get("credits") or []:
        if not isinstance(entry, dict):
            continue
        entries.append(ResetCredit(
            identifier=str(entry.get("id") or ""),
            reset_type=str(entry.get("reset_type") or ""),
            status=str(entry.get("status") or ""),
            granted_at=entry.get("granted_at"),
            expires_at=entry.get("expires_at"),
            redeemed_at=entry.get("redeemed_at"),
            title=str(entry.get("title") or ""),
            description=str(entry.get("description") or ""),
        ))
    return CodexResetCredits(
        available_count=int(payload.get("available_count") or 0),
        total_earned_count=int(payload.get("total_earned_count") or 0),
        purchase_eligible=bool(payload.get("immediate_reset_purchase_eligible", False)),
        credits=tuple(entries),
        fetched_at=fetched_at if fetched_at is not None else time.time(),
        raw=payload,
    )


def fetch_profile_stats(tokens: dict, *, url: str = PROFILE_URL) -> CodexProfileStats:
    """Read one account's lifetime statistics."""
    return parse_profile_stats(_get(url, tokens))


def fetch_reset_credits(tokens: dict, *, url: str = RESET_CREDITS_URL) -> CodexResetCredits:
    """Read one account's rate-limit reset credits."""
    return parse_reset_credits(_get(url, tokens))
