"""Tests for deriving a Codex account identity from its id_token."""

from __future__ import annotations

import base64
import json

from claude_swap.codex.identity import (
    OPENAI_AUTH_CLAIM,
    CodexIdentity,
    decode_jwt_claims,
    identity_from_auth,
)


def make_id_token(claims: dict) -> str:
    """A syntactically valid, unsigned JWT carrying ``claims``.

    Signature is literal filler: nothing here verifies it, deliberately -- these
    claims label a credential the user already holds, they never authorize.
    """
    payload = base64.urlsafe_b64encode(
        json.dumps(claims).encode("utf-8")
    ).decode("utf-8").rstrip("=")
    return f"headersegment.{payload}.signaturesegment"


def auth_with(claims: dict, **token_fields) -> dict:
    tokens = {"id_token": make_id_token(claims), "access_token": "at"}
    tokens.update(token_fields)
    return {"tokens": tokens}


#: Mirrors the real claim shape observed in a live ChatGPT id_token.
FULL_CLAIMS = {
    "email": "person@example.com",
    OPENAI_AUTH_CLAIM: {
        "chatgpt_account_id": "acct-1234",
        "chatgpt_plan_type": "pro",
        "chatgpt_subscription_active_until": "2026-10-08T17:12:13+00:00",
    },
}


class TestDecodeJwtClaims:
    def test_decodes_unpadded_base64url(self):
        # JWT omits base64 padding; the stdlib decoder requires it.
        assert decode_jwt_claims(make_id_token({"a": 1})) == {"a": 1}

    def test_rejects_wrong_segment_count(self):
        assert decode_jwt_claims("only.two") is None
        assert decode_jwt_claims("a.b.c.d") is None

    def test_rejects_undecodable_payload(self):
        assert decode_jwt_claims("head.!!!not-base64!!!.sig") is None

    def test_rejects_non_object_payload(self):
        assert decode_jwt_claims(make_id_token([1, 2])) is None  # type: ignore[arg-type]

    def test_rejects_empty_and_non_string(self):
        assert decode_jwt_claims("") is None
        assert decode_jwt_claims(None) is None  # type: ignore[arg-type]


class TestIdentityFromAuth:
    def test_extracts_the_full_identity(self):
        ident = identity_from_auth(auth_with(FULL_CLAIMS))
        assert ident == CodexIdentity(
            email="person@example.com",
            account_id="acct-1234",
            plan="pro",
            subscription_expires_at="2026-10-08T17:12:13+00:00",
        )

    def test_claim_account_id_wins_over_the_stored_field(self):
        ident = identity_from_auth(auth_with(FULL_CLAIMS, account_id="stale-id"))
        assert ident.account_id == "acct-1234"

    def test_falls_back_to_the_stored_account_id(self):
        # A token whose claims carry no account id is still identifiable from
        # the field written beside it; without this such a slot is unlabelable.
        ident = identity_from_auth(
            auth_with({"email": "p@example.com"}, account_id="acct-fallback")
        )
        assert ident.account_id == "acct-fallback"

    def test_unreadable_id_token_still_yields_stored_identity(self):
        ident = identity_from_auth(
            {"tokens": {"id_token": "garbage", "account_id": "acct-9"}}
        )
        assert ident.account_id == "acct-9"
        assert ident.email == ""

    def test_api_key_only_credential_has_no_derivable_identity(self):
        assert identity_from_auth({"OPENAI_API_KEY": "sk-test"}) is None

    def test_returns_none_for_unusable_input(self):
        assert identity_from_auth(None) is None
        assert identity_from_auth("not a dict") is None  # type: ignore[arg-type]
        assert identity_from_auth({"tokens": "not a dict"}) is None
        assert identity_from_auth({"tokens": {}}) is None

    def test_tolerates_a_non_object_namespace_claim(self):
        ident = identity_from_auth(
            auth_with({"email": "p@example.com", OPENAI_AUTH_CLAIM: "unexpected"},
                      account_id="acct-1")
        )
        assert ident.plan == ""
        assert ident.account_id == "acct-1"


class TestIdentityMatching:
    def test_same_account_id_matches(self):
        a = CodexIdentity(email="a@x.com", account_id="acct-1", plan="pro")
        b = CodexIdentity(email="different@y.com", account_id="acct-1", plan="plus")
        assert a.matches(b)  # email and plan are labels, not identity

    def test_different_account_id_does_not_match(self):
        a = CodexIdentity(email="same@x.com", account_id="acct-1", plan="pro")
        b = CodexIdentity(email="same@x.com", account_id="acct-2", plan="pro")
        assert not a.matches(b)

    def test_unknown_account_id_never_matches(self):
        # This predicate gates writing a live credential into a stored slot; an
        # empty id is "unknown", and treating it as equal would file one
        # account's tokens under another.
        blank = CodexIdentity(email="a@x.com", account_id="", plan="")
        assert not blank.matches(CodexIdentity(email="a@x.com", account_id="", plan=""))

    def test_matching_none_is_false(self):
        assert not CodexIdentity(email="a@x.com", account_id="acct-1", plan="").matches(None)


class TestDisplayLabel:
    def test_email_and_plan(self):
        assert CodexIdentity("p@x.com", "acct-1", "pro").display_label == "p@x.com [pro]"

    def test_falls_back_to_account_id_without_email(self):
        assert "acct-abc" in CodexIdentity("", "acct-abcdef12", "").display_label

    def test_unknown_when_nothing_identifies_it(self):
        assert CodexIdentity("", "", "").display_label == "unknown"
