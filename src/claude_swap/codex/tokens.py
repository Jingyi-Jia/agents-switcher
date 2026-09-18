"""Refreshing Codex OAuth tokens for accounts that are not the live one.

Polling a slot's quota needs a valid access token, and a stored slot's token
goes stale while that account sits unused. Refreshing it is the one operation
here that can destroy an account, for two reasons:

1. THE REFRESH TOKEN ROTATES. The response carries a NEW refresh token and
   invalidates the one used. A refresh whose result is not persisted leaves the
   slot holding a dead token -- the account then needs a full re-login. So a
   caller must persist before it does anything else with the result.
2. A RUNNING CODEX REFRESHES TOO. Codex's own AuthManager refreshes on 401. Two
   refreshes racing on one account means one of them is rotating a token the
   other just invalidated. There is no lock to coordinate with, so the rule is
   simply never to refresh an account a live ``codex`` process is using.

Both rules live with the CALLER (``usage``/``switcher``), because only it knows
the store and the process table. This module is the transport and nothing else.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import urllib.error
import urllib.parse
import urllib.request

#: Codex's own OAuth client id (codex-rs/login/src/auth/manager.rs). Hardcoded
#: rather than read from the token's ``aud`` claim: ``aud`` says who a token is
#: FOR, which today coincides with the refresh client but is not guaranteed to.
#: A mismatch is a signal that something changed, not a value to follow --
#: :func:`client_id_for` logs it and still uses this.
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
#: Codex honours this override, so respect it rather than forcing our own id.
CLIENT_ID_ENV_VAR = "CODEX_APP_SERVER_LOGIN_CLIENT_ID"
#: The OAuth issuer Codex authenticates against.
ISSUER = "https://auth.openai.com"
#: Refresh this long before expiry rather than at it, so a poll that starts
#: valid cannot finish expired.
EXPIRY_MARGIN_S = 300.0
_TIMEOUT_S = 30


class TokenRefreshError(Exception):
    """A refresh could not be completed."""


def _claims(jwt: str) -> dict | None:
    if not jwt or not isinstance(jwt, str):
        return None
    parts = jwt.split(".")
    if len(parts) != 3:
        return None
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(payload))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def access_token_expiry(tokens: dict) -> float | None:
    """Epoch seconds at which the access token expires, or ``None``.

    Read from the access token's own ``exp`` claim rather than from anything
    stored beside it: the token is the authority on its own lifetime, and a
    cached copy of that can be wrong after an external refresh.
    """
    claims = _claims((tokens or {}).get("access_token", ""))
    exp = (claims or {}).get("exp")
    return float(exp) if isinstance(exp, (int, float)) else None


def needs_refresh(tokens: dict, *, now: float, margin: float = EXPIRY_MARGIN_S) -> bool:
    """Whether the access token is expired or about to be.

    An UNREADABLE expiry answers True: an opaque token cannot be shown to be
    valid, and the cost of an unnecessary refresh is one rotation, while the
    cost of using a dead token is a failed poll reported as a real error.
    """
    expiry = access_token_expiry(tokens)
    if expiry is None:
        return True
    return expiry - margin <= now


def client_id_for(tokens: dict) -> str:
    """The OAuth client id to refresh with.

    Honours Codex's own override env var. Warns when the token's ``aud`` names
    a different client, which would mean this constant has drifted from what
    Codex is actually issuing.
    """
    override = os.environ.get(CLIENT_ID_ENV_VAR)
    if override:
        return override
    claims = _claims((tokens or {}).get("id_token", "")) or {}
    audience = claims.get("aud")
    if isinstance(audience, list):
        audience = audience[0] if audience else None
    if isinstance(audience, str) and audience and audience != CLIENT_ID:
        import logging

        logging.getLogger("claude-swap").warning(
            "Codex id_token aud (%s) differs from the known client id (%s); "
            "refreshing with the known id. If refresh fails, this constant may "
            "be out of date.",
            audience,
            CLIENT_ID,
        )
    return CLIENT_ID


def refresh_tokens(tokens: dict, *, issuer: str = ISSUER) -> dict:
    """Exchange the refresh token for a new set. Returns a NEW tokens dict.

    The caller MUST persist the result before using it -- the old refresh token
    is invalid from the moment this returns.

    Unrecognised fields in the response are ignored, but every field of the
    INPUT that the response does not replace is carried through, so a token set
    carrying something this tool does not model survives a refresh.

    Raises:
        TokenRefreshError: The refresh failed, including a permanently dead
            refresh token (``invalid_grant``), which callers should surface as
            "re-login needed" rather than retry.
    """
    refresh_token = (tokens or {}).get("refresh_token")
    if not refresh_token:
        raise TokenRefreshError("no refresh token stored for this account")

    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id_for(tokens),
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{issuer.rstrip('/')}/oauth/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as e:
        detail = e.read()[:400].decode("utf-8", "replace")
        if "invalid_grant" in detail:
            raise TokenRefreshError(
                "refresh token is no longer valid — this account needs "
                f"'codex login' again ({e.code})"
            ) from e
        raise TokenRefreshError(f"refresh failed: HTTP {e.code} {detail}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise TokenRefreshError(f"refresh request failed: {e}") from e
    except ValueError as e:
        raise TokenRefreshError(f"refresh returned unreadable JSON: {e}") from e

    if not isinstance(payload, dict):
        raise TokenRefreshError("refresh returned a non-object response")

    updated = dict(tokens)
    for source, destination in (
        ("access_token", "access_token"),
        ("refresh_token", "refresh_token"),
        ("id_token", "id_token"),
    ):
        value = payload.get(source)
        if isinstance(value, str) and value:
            updated[destination] = value
    if not updated.get("access_token"):
        raise TokenRefreshError("refresh response carried no access token")
    return updated
