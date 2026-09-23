"""Tests for refreshing Codex OAuth tokens.

Refresh is the one operation that can destroy an account: the refresh token
rotates, so a result that is not persisted leaves the slot holding a dead token.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error

import pytest

from claude_swap.codex import tokens as tokens_mod
from claude_swap.codex.tokens import (
    CLIENT_ID,
    CLIENT_ID_ENV_VAR,
    TokenRefreshError,
    access_token_expiry,
    client_id_for,
    needs_refresh,
    refresh_tokens,
)


def jwt(claims: dict) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"hdr.{payload}.sig"


class TestExpiry:
    def test_read_from_the_tokens_own_claim(self):
        assert access_token_expiry({"access_token": jwt({"exp": 1790539102})}) == 1790539102

    def test_unreadable_token_has_no_known_expiry(self):
        for token in ("", "garbage", "a.b", jwt({})):
            assert access_token_expiry({"access_token": token}) is None

    def test_valid_token_needs_no_refresh(self):
        now = time.time()
        tok = {"access_token": jwt({"exp": now + 86400})}
        assert needs_refresh(tok, now=now) is False

    def test_expired_token_needs_refresh(self):
        now = time.time()
        assert needs_refresh({"access_token": jwt({"exp": now - 1})}, now=now) is True

    def test_refreshes_before_expiry_not_at_it(self):
        # A poll that starts valid must not finish expired.
        now = time.time()
        tok = {"access_token": jwt({"exp": now + 60})}
        assert needs_refresh(tok, now=now, margin=300) is True

    def test_unreadable_expiry_errs_toward_refreshing(self):
        # One unnecessary rotation beats a poll failing with a dead token.
        assert needs_refresh({"access_token": "opaque"}, now=time.time()) is True


class TestClientId:
    def test_defaults_to_codexs_own_id(self):
        assert client_id_for({}) == CLIENT_ID

    def test_env_override_is_honoured(self, monkeypatch):
        monkeypatch.setenv(CLIENT_ID_ENV_VAR, "app_custom")
        assert client_id_for({}) == "app_custom"

    def test_a_differing_audience_warns_but_does_not_win(self, caplog):
        # aud says who a token is FOR; it is not guaranteed to be the refresh
        # client, so a mismatch is a signal, not a value to follow.
        tok = {"id_token": jwt({"aud": ["app_something_else"]})}
        with caplog.at_level("WARNING"):
            assert client_id_for(tok) == CLIENT_ID
        assert "aud" in caplog.text

    def test_a_matching_audience_is_silent(self, caplog):
        tok = {"id_token": jwt({"aud": [CLIENT_ID]})}
        with caplog.at_level("WARNING"):
            client_id_for(tok)
        assert caplog.text == ""


class TestRefresh:
    def _respond(self, monkeypatch, payload):
        class Response:
            def read(self): return json.dumps(payload).encode()
            def __enter__(self): return self
            def __exit__(self, *a): return False

        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["url"] = request.full_url
            captured["body"] = request.data.decode()
            return Response()

        monkeypatch.setattr(tokens_mod.urllib.request, "urlopen", fake_urlopen)
        return captured

    def test_returns_the_rotated_tokens(self, monkeypatch):
        self._respond(monkeypatch, {
            "access_token": "at-new", "refresh_token": "rt-new", "id_token": "id-new",
        })
        result = refresh_tokens({"refresh_token": "rt-old", "access_token": "at-old"})
        assert result["refresh_token"] == "rt-new"
        assert result["access_token"] == "at-new"

    def test_posts_the_grant_to_the_issuer(self, monkeypatch):
        captured = self._respond(monkeypatch, {"access_token": "at"})
        refresh_tokens({"refresh_token": "rt"})
        assert captured["url"].endswith("/oauth/token")
        assert "grant_type=refresh_token" in captured["body"]
        assert CLIENT_ID in captured["body"]

    def test_carries_through_fields_the_response_does_not_replace(self, monkeypatch):
        # A token set carrying something unmodelled must survive a refresh.
        self._respond(monkeypatch, {"access_token": "at-new"})
        result = refresh_tokens({
            "refresh_token": "rt", "account_id": "acct-1", "future_field": 7,
        })
        assert result["account_id"] == "acct-1"
        assert result["future_field"] == 7
        assert result["refresh_token"] == "rt"  # unchanged, not dropped

    def test_missing_refresh_token(self):
        with pytest.raises(TokenRefreshError, match="no refresh token"):
            refresh_tokens({})

    def test_dead_refresh_token_says_re_login(self, monkeypatch):
        # Distinguished from a transient failure: retrying will never help.
        def boom(*a, **k):
            raise urllib.error.HTTPError(
                "u", 400, "Bad Request", {},
                __import__("io").BytesIO(b'{"error":"invalid_grant"}'),
            )

        monkeypatch.setattr(tokens_mod.urllib.request, "urlopen", boom)
        with pytest.raises(TokenRefreshError, match="codex login"):
            refresh_tokens({"refresh_token": "rt-dead"})

    def test_network_failure_is_reported(self, monkeypatch):
        monkeypatch.setattr(
            tokens_mod.urllib.request, "urlopen",
            lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("down")),
        )
        with pytest.raises(TokenRefreshError):
            refresh_tokens({"refresh_token": "rt"})

    def test_response_without_an_access_token_is_an_error(self, monkeypatch):
        self._respond(monkeypatch, {"token_type": "Bearer"})
        with pytest.raises(TokenRefreshError, match="no access token"):
            refresh_tokens({"refresh_token": "rt"})
