"""Refresh failures expose recovery instructions, never issuer diagnostics."""

from __future__ import annotations

import http.client
import io
import json
import urllib.error

import pytest

from claude_swap.codex import tokens as tokens_mod
from claude_swap.codex.tokens import TokenRefreshError, refresh_tokens
from claude_swap.providers import safe_error

_SECRET = "test-only-opaque-credential"
_ISSUER = f"https://issuer.invalid/private/{_SECRET}"
_TOKENS = {"refresh_token": _SECRET, "access_token": "test-only-access-credential"}
_REAUTH_MESSAGE = (
    "Codex needs a fresh login for this account. Run 'codex login' "
    "as the affected account, then add the existing login again."
)


@pytest.fixture
def reject_refresh(monkeypatch):
    def reject(body, status=400):
        def urlopen(request, timeout):
            raise urllib.error.HTTPError(
                request.full_url,
                status,
                f"Server diagnostic: {_SECRET}",
                {},
                io.BytesIO(body),
            )

        monkeypatch.setattr(tokens_mod.urllib.request, "urlopen", urlopen)

    return reject


@pytest.mark.parametrize("code", [
    "invalid_grant",
    "refresh_token_reused",
    "refresh_token_expired",
    "refresh_token_invalidated",
    "REFRESH_TOKEN_REUSED",
])
@pytest.mark.parametrize("shape", ["oauth", "nested", "top-level"])
@pytest.mark.parametrize("status", [400, 401])
def test_reauth_codes_survive_safe_error_without_diagnostics(
    reject_refresh, code, shape, status
):
    payload = {
        "error_description": f"refresh_token={_SECRET}; access_token={_SECRET}",
        "refresh_token": _SECRET,
        "access_token": _SECRET,
    }
    if shape == "oauth":
        payload["error"] = code
    elif shape == "nested":
        payload["error"] = {"code": code, "message": f"Bearer {_SECRET}"}
    else:
        payload["code"] = code
    reject_refresh(json.dumps(payload).encode(), status)

    with pytest.raises(TokenRefreshError) as caught:
        refresh_tokens(_TOKENS, issuer=_ISSUER)

    assert str(caught.value) == _REAUTH_MESSAGE
    assert safe_error(caught.value, include_detail=True) == _REAUTH_MESSAGE
    assert _SECRET not in safe_error(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


def test_reauth_code_after_long_diagnostic_is_still_classified(reject_refresh):
    reject_refresh(json.dumps({
        "error_description": _SECRET * 100,
        "error": {"code": "refresh_token_reused"},
    }).encode())

    with pytest.raises(TokenRefreshError) as caught:
        refresh_tokens(_TOKENS, issuer=_ISSUER)

    assert safe_error(caught.value, include_detail=True) == _REAUTH_MESSAGE


@pytest.mark.parametrize("body", [
    b"",
    b"not JSON: invalid_grant test-only-opaque-credential",
    b'\xfftest-only-opaque-credential',
    b'{"error":"refresh_token_reused",',
    b"null",
    b"[]",
    b'"refresh_token_reused"',
    b'{"error":null}',
    b'{"error":["invalid_grant"]}',
    b'{"error":{"code":["invalid_grant"]}}',
    b'{"code":{"error":"invalid_grant"}}',
    b'{"error":"invalid_client","error_description":"invalid_grant"}',
    b'{"error":{"message":"refresh_token_reused test-only-opaque-credential"}}',
    b'{"error":"server_error","refresh_token":"invalid_grant"}',
    b'{"error":"invalid_grant_test-only-opaque-credential"}',
    b'{"error":"unsupported_grant_type","access_token":"test-only-opaque-credential"}',
    b'{"error":"test-only-opaque-credential"}',
])
def test_unknown_or_malformed_errors_do_not_leak_or_demand_login(reject_refresh, body):
    reject_refresh(body)

    with pytest.raises(TokenRefreshError) as caught:
        refresh_tokens(_TOKENS, issuer=_ISSUER)

    expected = "refresh failed: HTTP 400; try again later"
    assert str(caught.value) == expected
    assert safe_error(caught.value, include_detail=True) == expected


@pytest.mark.parametrize("status", [403, 408, 429, 500, 502, 503])
def test_transient_http_failures_keep_only_status(reject_refresh, status):
    reject_refresh(json.dumps({
        "error": "temporarily_unavailable",
        "error_description": _SECRET,
        "request": {"refresh_token": _SECRET, "url": _ISSUER},
    }).encode(), status)

    with pytest.raises(TokenRefreshError) as caught:
        refresh_tokens(_TOKENS, issuer=_ISSUER)

    expected = f"refresh failed: HTTP {status}; try again later"
    assert str(caught.value) == expected
    assert safe_error(caught.value, include_detail=True) == expected
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("error", [
    urllib.error.URLError(f"request to {_ISSUER}: {_SECRET}"),
    TimeoutError(_SECRET),
    OSError(_SECRET),
    http.client.InvalidURL(_ISSUER),
    http.client.IncompleteRead(_SECRET.encode()),
])
def test_network_failures_never_echo_exception_details(monkeypatch, error):
    def urlopen(request, timeout):
        raise error

    monkeypatch.setattr(tokens_mod.urllib.request, "urlopen", urlopen)

    with pytest.raises(TokenRefreshError) as caught:
        refresh_tokens(_TOKENS, issuer=_ISSUER)

    expected = "refresh request failed; check your connection and try again"
    assert str(caught.value) == expected
    assert safe_error(caught.value, include_detail=True) == expected
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("error", [
    OSError(_SECRET),
    http.client.IncompleteRead(_SECRET.encode()),
])
def test_unreadable_http_error_body_keeps_controlled_status(monkeypatch, error):
    class Body(io.BytesIO):
        def read(self, *args):
            raise error

    def urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 503, _SECRET, {}, Body())

    monkeypatch.setattr(tokens_mod.urllib.request, "urlopen", urlopen)

    with pytest.raises(TokenRefreshError) as caught:
        refresh_tokens(_TOKENS, issuer=_ISSUER)

    expected = "refresh failed: HTTP 503; try again later"
    assert str(caught.value) == expected
    assert safe_error(caught.value, include_detail=True) == expected


@pytest.mark.parametrize("error", [
    ValueError(_SECRET),
    json.JSONDecodeError(_SECRET, _SECRET, 0),
])
def test_unreadable_response_never_echoes_decoder_details(monkeypatch, error):
    def urlopen(request, timeout):
        raise error

    monkeypatch.setattr(tokens_mod.urllib.request, "urlopen", urlopen)

    with pytest.raises(TokenRefreshError) as caught:
        refresh_tokens(_TOKENS, issuer=_ISSUER)

    expected = "refresh returned unreadable JSON"
    assert str(caught.value) == expected
    assert safe_error(caught.value, include_detail=True) == expected
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__
