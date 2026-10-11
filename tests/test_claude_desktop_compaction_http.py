from __future__ import annotations

from unittest.mock import Mock

import pytest

from agents_switcher.providers import ProviderActionError
from tests.test_web_actions import request, web


PATH = "/api/claude-desktop/compact"
PAYLOAD = {"profileId": "a" * 32, "confirm": True}


@pytest.fixture
def compact(web):
    manager = web.state.claude_desktop
    manager.compact = Mock(return_value={
        "ok": True,
        "storage": {"logicalBytes": 8192, "beforeBytes": 8192, "afterBytes": 4096, "reclaimedBytes": 4096},
        "message": "Compaction verified; reclaimed 4096 bytes without changing the VM disk's bytes.",
    })
    return manager.compact


def test_compaction_dispatches_only_the_saved_profile_and_exact_confirmation(web, compact):
    code, result, headers = request(web, PATH, PAYLOAD)
    assert code == 200
    assert result == compact.return_value
    assert set(result) == {"ok", "storage", "message"}
    assert headers["Cache-Control"] == "no-store"
    compact.assert_called_once_with(profileId="a" * 32, confirm=True)
    assert web.state._claude.calls == [] and web.state._codex.calls == []


@pytest.mark.parametrize("auth", [False, "wrong", "test-web-auth "])
def test_compaction_requires_exact_header_auth(web, compact, auth):
    assert request(web, PATH, PAYLOAD, token=auth)[0] == 403
    compact.assert_not_called()


def test_query_auth_and_all_query_parameters_are_rejected(web, compact):
    assert request(web, PATH + "?token=" + web.token, PAYLOAD, token=False)[0] == 403
    for query in ("token=" + web.token, "path=private", "unexpected=1"):
        assert request(web, PATH + "?" + query, PAYLOAD)[0] == 400
    compact.assert_not_called()


@pytest.mark.parametrize("headers", [
    {"Origin": "https://example.test"},
    {"Origin": "null"},
    {"Referer": "https://example.test/"},
])
def test_cross_origin_headers_cannot_authorize_compaction(web, compact, headers):
    assert request(web, PATH, PAYLOAD, token=False, headers=headers)[0] == 403
    compact.assert_not_called()


def test_compaction_requires_json_content_type(web, compact):
    assert request(web, PATH, PAYLOAD, headers={"Content-Type": "text/plain"})[0] == 400
    compact.assert_not_called()


@pytest.mark.parametrize("field", ["path", "url", "pid", "executable", "token", "credentials", "name", "emailLabel", "unexpected"])
def test_compaction_rejects_extra_fields_before_dispatch(web, compact, field):
    assert request(web, PATH, {**PAYLOAD, field: "synthetic-private-value"})[0] == 400
    compact.assert_not_called()


@pytest.mark.parametrize("confirm", [None, False, 1, 0, "true", [], {}])
def test_compaction_requires_a_literal_true_before_dispatch(web, compact, confirm):
    assert request(web, PATH, {**PAYLOAD, "confirm": confirm})[0] == 400
    compact.assert_not_called()


@pytest.mark.parametrize("profile_id", ["default", "", "a" * 31, "A" * 32, "../rootfs.img", "https://example.test", None, 1, [], {}])
def test_compaction_rejects_invalid_profile_identifiers_before_dispatch(web, compact, profile_id):
    assert request(web, PATH, {**PAYLOAD, "profileId": profile_id})[0] == 400
    compact.assert_not_called()


def test_compaction_rejects_missing_duplicate_and_non_object_fields(web, compact):
    for payload in ({}, {"profileId": PAYLOAD["profileId"]}, {"confirm": True}):
        assert request(web, PATH, payload)[0] == 400
    for raw in (b'[]', b'null', b'{"profileId":"' + b'a' * 32 + b'","confirm":true,"confirm":true}'):
        assert request(web, PATH, raw=raw)[0] == 400
    compact.assert_not_called()


def test_compaction_preserves_safe_manager_failure(web, compact):
    compact.side_effect = ProviderActionError("Claude process status is unknown. Quit Claude and retry after a readiness check.")
    code, result, _ = request(web, PATH, PAYLOAD)
    assert code == 400 and result["ok"] is False
    assert "process status is unknown" in result["message"]
    compact.assert_called_once_with(**PAYLOAD)
