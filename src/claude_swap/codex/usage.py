"""Reading a Codex account's quota.

Source: ``GET https://chatgpt.com/backend-api/wham/usage``, authenticated with
the account's access token. It is a STATUS READ, not an inference call, so
polling it costs no quota -- which is what makes an auto-switcher possible at
all. (Codex itself learns the same numbers from ``x-codex-*`` response headers
on real API calls; that is free for Codex because it is already making them,
and useless here because we are not.)

WINDOWS ARE NOT NAMED, SO LABELS ARE DERIVED. The API returns
``primary_window`` and ``secondary_window``, and it is tempting to read those as
"5-hour" and "weekly". They are not: measured on a Pro account, ``primary`` came
back with ``limit_window_seconds`` of 604800 -- seven days -- and ``secondary``
was absent entirely. Only ``limit_window_seconds`` says how long a window is, so
every label here is derived from it and the primary/secondary naming is dropped
at the boundary rather than carried into the model.

EXHAUSTED IS NOT THE SAME AS UNUSABLE. An account at 100% with credits
remaining can still serve requests, and one under a spend control can be
blocked while well under its rate limit. ``usable`` is the question a switcher
actually asks, and it is not ``used_percent < 100``.

THE TYPED MODEL IS A VIEW, NOT THE TRUTH. The live response carries fields the
published OpenAPI model does not (``model_usage``, ``code_review_rate_limit``,
``promo``, ``credits.overage_limit_reached``, an inline
``rate_limit_reset_credits``), so the API is ahead of its own generated schema.
The whole payload is kept on ``raw`` and anything unmodelled stays reachable.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

#: Status endpoint. A GET; it does not consume quota.
USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
_TIMEOUT_S = 30


class UsageError(Exception):
    """Quota could not be read."""


class UsageAuthError(UsageError):
    """The token was rejected — refresh it and retry once."""


def _window_label(seconds: int | None) -> str:
    """A human label for a window length, derived because the API sends none."""
    if not seconds or seconds <= 0:
        return "?"
    if seconds % 86400 == 0:
        return f"{seconds // 86400}d"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    return f"{max(1, seconds // 60)}m"


@dataclass(frozen=True)
class CodexWindow:
    """One rate-limit window."""

    used_percent: int
    window_seconds: int
    reset_at: int | None = None
    reset_after_seconds: int | None = None

    @property
    def label(self) -> str:
        return _window_label(self.window_seconds)

    @property
    def is_exhausted(self) -> bool:
        return self.used_percent >= 100


@dataclass(frozen=True)
class CodexCredits:
    """Pay-as-you-go balance, which can outlive an exhausted rate limit."""

    has_credits: bool = False
    unlimited: bool = False
    balance: str | None = None
    overage_limit_reached: bool = False

    @property
    def can_cover_requests(self) -> bool:
        """Whether credits could serve a request the rate limit would refuse."""
        if self.overage_limit_reached:
            return False
        return self.unlimited or self.has_credits


@dataclass(frozen=True)
class CodexFeatureLimit:
    """A limit scoped to one feature rather than the account as a whole."""

    name: str
    metered_feature: str = ""
    allowed: bool = True
    limit_reached: bool = False
    windows: tuple[CodexWindow, ...] = ()


@dataclass(frozen=True)
class CodexUsage:
    """One account's quota state at ``fetched_at``."""

    account_id: str = ""
    email: str = ""
    plan: str = ""
    allowed: bool = True
    limit_reached: bool = False
    windows: tuple[CodexWindow, ...] = ()
    credits: CodexCredits | None = None
    spend_control_reached: bool = False
    reached_type: str = ""
    reached_details: str = ""
    reset_credits_available: int = 0
    model_availability: dict[str, bool] = field(default_factory=dict)
    feature_limits: tuple[CodexFeatureLimit, ...] = ()
    fetched_at: float = 0.0
    raw: dict = field(default_factory=dict)

    @property
    def binding_percent(self) -> int | None:
        """Utilisation of the window closest to its limit."""
        return max((w.used_percent for w in self.windows), default=None)

    @property
    def binding_window(self) -> CodexWindow | None:
        return max(self.windows, key=lambda w: w.used_percent, default=None)

    @property
    def next_reset_at(self) -> int | None:
        """Soonest epoch at which any exhausted window frees up."""
        resets = [w.reset_at for w in self.windows if w.is_exhausted and w.reset_at]
        return min(resets) if resets else None

    @property
    def usable(self) -> bool:
        """Whether this account can serve a request right now.

        NOT ``used_percent < 100``. The API's own ``allowed`` is authoritative
        for the rate limit, credits can cover requests past an exhausted one,
        and a spend control blocks regardless of either.
        """
        if self.spend_control_reached:
            return False
        if self.allowed:
            return True
        return bool(self.credits and self.credits.can_cover_requests)

    @property
    def summary(self) -> str:
        """One line for a human: utilisation, window, and why it is blocked."""
        window = self.binding_window
        if window is None:
            return "usage unknown"
        text = f"{window.used_percent}% of {window.label}"
        if self.usable and not self.allowed:
            text += " (on credits)"
        elif not self.usable:
            reason = "spend limit" if self.spend_control_reached else "limit reached"
            text += f" — {reason}"
        return text


def _window_from(payload: dict | None) -> CodexWindow | None:
    if not isinstance(payload, dict):
        return None
    used = payload.get("used_percent")
    if not isinstance(used, (int, float)):
        return None
    return CodexWindow(
        used_percent=int(used),
        window_seconds=int(payload.get("limit_window_seconds") or 0),
        reset_at=payload.get("reset_at"),
        reset_after_seconds=payload.get("reset_after_seconds"),
    )


def _windows_from(details: dict | None) -> tuple[CodexWindow, ...]:
    """Both windows of a rate-limit block, primary/secondary naming dropped."""
    if not isinstance(details, dict):
        return ()
    found = [
        _window_from(details.get(key))
        for key in ("primary_window", "secondary_window")
    ]
    return tuple(w for w in found if w is not None)


def _feature_limits_from(payload: dict) -> tuple[CodexFeatureLimit, ...]:
    limits: list[CodexFeatureLimit] = []
    for entry in payload.get("additional_rate_limits") or []:
        if not isinstance(entry, dict):
            continue
        details = entry.get("rate_limit") or {}
        limits.append(CodexFeatureLimit(
            name=str(entry.get("limit_name") or "unnamed"),
            metered_feature=str(entry.get("metered_feature") or ""),
            allowed=bool(details.get("allowed", True)),
            limit_reached=bool(details.get("limit_reached", False)),
            windows=_windows_from(details),
        ))
    # Not in the published OpenAPI model, but present in live responses.
    review = payload.get("code_review_rate_limit")
    if isinstance(review, dict):
        limits.append(CodexFeatureLimit(
            name="code_review",
            metered_feature="code_review",
            allowed=bool(review.get("allowed", True)),
            limit_reached=bool(review.get("limit_reached", False)),
            windows=_windows_from(review),
        ))
    return tuple(limits)


def parse_usage(payload: dict, *, fetched_at: float | None = None) -> CodexUsage:
    """Build the typed view of a ``/wham/usage`` response.

    Tolerant by construction: every field is optional and a missing or
    wrong-typed one falls back to a neutral default, because this payload has
    already been observed to carry keys its own published schema does not.
    """
    if not isinstance(payload, dict):
        raise UsageError("usage response was not an object")

    rate_limit = payload.get("rate_limit") or {}
    credits_payload = payload.get("credits")
    spend = payload.get("spend_control") or {}
    reached = payload.get("rate_limit_reached_type") or {}
    reset_credits = payload.get("rate_limit_reset_credits") or {}

    models: dict[str, bool] = {}
    for name, info in (payload.get("model_usage") or {}).items():
        if isinstance(info, dict):
            models[str(name)] = bool(info.get("available", True))

    credits = None
    if isinstance(credits_payload, dict):
        credits = CodexCredits(
            has_credits=bool(credits_payload.get("has_credits", False)),
            unlimited=bool(credits_payload.get("unlimited", False)),
            balance=credits_payload.get("balance"),
            overage_limit_reached=bool(
                credits_payload.get("overage_limit_reached", False)
            ),
        )

    return CodexUsage(
        account_id=str(payload.get("account_id") or ""),
        email=str(payload.get("email") or ""),
        plan=str(payload.get("plan_type") or ""),
        allowed=bool(rate_limit.get("allowed", True)),
        limit_reached=bool(rate_limit.get("limit_reached", False)),
        windows=_windows_from(rate_limit),
        credits=credits,
        spend_control_reached=bool(spend.get("reached", False)),
        reached_type=str(reached.get("type") or ""),
        reached_details=str(reached.get("details") or ""),
        reset_credits_available=int(reset_credits.get("available_count") or 0),
        model_availability=models,
        feature_limits=_feature_limits_from(payload),
        fetched_at=fetched_at if fetched_at is not None else time.time(),
        raw=payload,
    )


def fetch_usage(tokens: dict, *, url: str = USAGE_URL) -> CodexUsage:
    """Read one account's quota using its access token.

    Sends only what the endpoint needs. codex-switcher additionally sends
    browser-impersonation headers (``Origin``, ``Referer``, ``Sec-Fetch-*``) to
    satisfy Cloudflare; measured against the live endpoint, plain
    ``Authorization`` plus ``chatgpt-account-id`` returns 200, so pretending to
    be a browser is not required and is not done.

    Raises:
        UsageAuthError: The token was rejected (401/403). Refresh and retry once.
        UsageError: Anything else.
    """
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
            raise UsageAuthError(f"usage request rejected: HTTP {e.code}") from e
        if e.code == 429:
            raise UsageError("usage endpoint rate-limited (429)") from e
        raise UsageError(f"usage request failed: HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise UsageError(f"usage request failed: {e}") from e
    except ValueError as e:
        raise UsageError(f"usage response was not JSON: {e}") from e

    return parse_usage(payload)
