"""Who a Codex credential belongs to, derived from its ``id_token``.

An ``auth.json`` carries no plaintext account label -- the email, plan and
account id live inside the JWT ``id_token``'s claims. Reading them locally is
what lets accounts be listed, labelled and (critically) identity-checked before
a credential is filed against a slot, with no network call.

The JWT is NOT verified here, and must not be trusted as an authorization
decision. It is read only to label a credential the user already possesses, so
its signature is irrelevant: a forged token would only mislabel the forger's own
slot. Never gate access on these claims.

OpenAI namespaces its claims under ``https://api.openai.com/auth`` rather than
at the top level, so ``chatgpt_account_id`` and ``chatgpt_plan_type`` are nested
one level in. ``email`` is a standard OIDC claim and stays at the top.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass

#: OpenAI's private-claim namespace inside the id_token.
OPENAI_AUTH_CLAIM = "https://api.openai.com/auth"


@dataclass(frozen=True)
class CodexIdentity:
    """The account a Codex credential belongs to."""

    email: str
    account_id: str
    plan: str
    subscription_expires_at: str | None = None

    @property
    def display_label(self) -> str:
        """Display label: ``email [plan]``, falling back to the account id."""
        name = self.email or (f"account {self.account_id[:8]}" if self.account_id else "unknown")
        return f"{name} [{self.plan}]" if self.plan else name

    def matches(self, other: CodexIdentity | None) -> bool:
        """Whether two identities are the same ACCOUNT.

        Compares ``account_id`` alone, and only when both sides have one. The
        email is a label -- it can be absent from a token, and two credentials
        for the same account can disagree on it -- so it never decides identity.
        An empty account id on either side is "unknown", never a match: this
        predicate gates writing a live credential back into a stored slot, where
        a false positive files one account's tokens under another.
        """
        if other is None:
            return False
        return bool(self.account_id) and self.account_id == other.account_id


def decode_jwt_claims(token: str) -> dict | None:
    """Return a JWT's payload claims, or ``None`` if it is not a readable JWT.

    Tolerates every malformed shape seen in practice rather than raising: a
    truncated file, an opaque non-JWT token, or a future format all mean "cannot
    label this credential", which callers handle by falling back to the
    ``account_id`` stored beside the token in ``auth.json``.
    """
    if not token or not isinstance(token, str):
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    payload = parts[1]
    # JWT uses base64url WITHOUT padding; Python's decoder requires it.
    payload += "=" * (-len(payload) % 4)
    try:
        raw = base64.urlsafe_b64decode(payload)
        claims = json.loads(raw)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    return claims if isinstance(claims, dict) else None


def identity_from_auth(auth: dict | None) -> CodexIdentity | None:
    """Derive the identity of a parsed ``auth.json``, or ``None`` if unknowable.

    ``account_id`` prefers the id_token claim and falls back to the
    ``tokens.account_id`` field written beside it, because the two can disagree:
    codex-switcher observed tokens whose stored ``account_id`` was absent while
    the claim carried it, and backfilling from the claim is what makes such a
    credential identifiable at all.

    Returns ``None`` for an API-key credential -- it has no id_token and so no
    derivable account -- which callers must treat as "label this some other way",
    not as "no credential".
    """
    if not isinstance(auth, dict):
        return None
    tokens = auth.get("tokens")
    if not isinstance(tokens, dict):
        return None

    claims = decode_jwt_claims(tokens.get("id_token", "")) or {}
    ns = claims.get(OPENAI_AUTH_CLAIM)
    ns = ns if isinstance(ns, dict) else {}

    account_id = str(ns.get("chatgpt_account_id") or tokens.get("account_id") or "")
    email = str(claims.get("email") or "")
    plan = str(ns.get("chatgpt_plan_type") or "")
    expires = ns.get("chatgpt_subscription_active_until")

    if not (account_id or email):
        return None
    return CodexIdentity(
        email=email,
        account_id=account_id,
        plan=plan,
        subscription_expires_at=str(expires) if expires else None,
    )
