"""Early HTTP rejections preserve the response without trusting the request body."""

from __future__ import annotations

import http.client
import json
import socket
import threading
from http.client import HTTPMessage
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from claude_swap.web import server
from tests.test_web_actions import web


@pytest.fixture
def rejected_post():
    state = Mock()
    handler = object.__new__(server._make_handler(state, "test-web-auth"))
    handler.path = "/api/add"
    handler.headers = HTTPMessage()
    handler.headers["Content-Length"] = "8"
    handler.headers["Content-Type"] = "application/json"
    handler.connection = Mock()
    handler.rfile = Mock()
    handler.rfile.read1.return_value = b"not-json"
    handler.wfile = Mock()
    handler._json = Mock()
    handler.close_connection = False
    return SimpleNamespace(handler=handler, state=state)


@pytest.mark.parametrize("authorized,path,status,error", [
    (False, "/api/add", 403, "bad or missing token"),
    (True, "/api/not-a-route", 404, "not found"),
])
def test_early_rejection_flushes_before_discarding_unparsed_body(
    rejected_post, authorized, path, status, error,
):
    handler = rejected_post.handler
    handler.path = path
    if authorized:
        handler.headers["X-Auth-Token"] = "test-web-auth"
    handler._body = Mock(side_effect=AssertionError("Rejected bodies must not be parsed"))
    effects = Mock()
    effects.attach_mock(handler._json, "response")
    effects.attach_mock(handler.wfile.flush, "flush")
    effects.attach_mock(handler.rfile.read1, "discard")

    handler.do_POST()

    assert effects.mock_calls == [call.response(status, {"error": error}), call.flush(), call.discard(8)]
    assert handler.close_connection is True
    handler._body.assert_not_called()
    handler.rfile.read.assert_not_called()
    timeout = handler.connection.settimeout.call_args.args[0]
    assert 0 < timeout <= server.REJECTION_DRAIN_TIMEOUT_S
    assert rejected_post.state.mock_calls == []


@pytest.mark.parametrize("headers", [
    [],
    [("Content-Length", "0")],
    [("Content-Length", "-1")],
    [("Content-Length", "invalid")],
    [("Content-Length", "1, 1")],
    [("Content-Length", "\uff11")],
    [("Content-Length", "00000000001")],
    [("Content-Length", str(server.MAX_BODY_BYTES + 1))],
    [("Content-Length", "8"), ("Content-Length", "8")],
    [("Content-Length", "8"), ("Content-Length", "9")],
    [("Transfer-Encoding", "chunked")],
    [("Content-Length", "8"), ("Transfer-Encoding", "chunked")],
    [("Content-Length", "8"), ("Transfer-Encoding", "")],
])
def test_rejection_never_drains_ambiguous_or_unbounded_framing(rejected_post, headers):
    handler = rejected_post.handler
    handler.headers = HTTPMessage()
    for name, value in headers:
        handler.headers[name] = value

    handler.do_POST()

    handler._json.assert_called_once_with(403, {"error": "bad or missing token"})
    handler.wfile.flush.assert_called_once_with()
    handler.rfile.read.assert_not_called()
    handler.rfile.read1.assert_not_called()
    handler.connection.settimeout.assert_not_called()
    handler.connection.setblocking.assert_not_called()
    handler.connection.recv.assert_not_called()
    assert handler.close_connection is True
    assert rejected_post.state.mock_calls == []


@pytest.mark.parametrize("length", [1, server.MAX_BODY_BYTES])
def test_rejection_drain_accepts_only_the_declared_bounded_bytes(rejected_post, length):
    handler = rejected_post.handler
    handler.headers.replace_header("Content-Length", str(length))
    handler.rfile.read1.return_value = b"x" * length

    handler.do_POST()

    handler.rfile.read1.assert_called_once_with(length)
    handler.rfile.read.assert_not_called()


def test_rejection_drain_accounts_for_partial_reads(rejected_post):
    handler = rejected_post.handler
    handler.rfile.read1.side_effect = [b"not-", b"json"]

    handler.do_POST()

    assert handler.rfile.read1.call_args_list == [call(8), call(4)]


def test_rejection_drain_has_a_total_deadline_for_trickling_bodies(rejected_post, monkeypatch):
    handler = rejected_post.handler
    limit = server.REJECTION_DRAIN_TIMEOUT_S
    clock = Mock(side_effect=[0, limit / 4, limit / 2, limit + 0.01])
    monkeypatch.setattr(server, "time", SimpleNamespace(monotonic=clock))
    handler.rfile.read1.side_effect = [b"n", b"o"]

    handler.do_POST()

    assert handler.rfile.read1.call_args_list == [call(8), call(7)]
    assert handler.connection.settimeout.call_args_list == [call(limit * 3 / 4), call(limit / 2)]
    assert handler.close_connection is True


@pytest.mark.parametrize("outcome", [b"", TimeoutError(), ConnectionResetError(), OSError()])
def test_rejection_drain_stops_on_eof_or_socket_error(rejected_post, outcome):
    handler = rejected_post.handler
    handler.rfile.read1.side_effect = [outcome]

    handler.do_POST()

    handler._json.assert_called_once_with(403, {"error": "bad or missing token"})
    handler.rfile.read1.assert_called_once_with(8)
    assert handler.close_connection is True


def test_rejection_still_succeeds_if_socket_timeout_cannot_be_set(rejected_post):
    handler = rejected_post.handler
    handler.connection.settimeout.side_effect = OSError()

    handler.do_POST()

    handler._json.assert_called_once_with(403, {"error": "bad or missing token"})
    handler.rfile.read1.assert_not_called()


def test_malformed_framing_finish_discards_late_raw_transport_without_http_body_read(rejected_post):
    handler = rejected_post.handler
    handler.headers = HTTPMessage()
    handler.headers["Transfer-Encoding"] = "chunked"
    left, right = socket.socketpair()
    try:
        handler.connection = left

        handler.do_POST()
        right.sendall(b"8\r\nnot-json\r\n0\r\n\r\n")
        right.shutdown(socket.SHUT_WR)
        handler.finish()

        handler._json.assert_called_once_with(403, {"error": "bad or missing token"})
        handler.rfile.read.assert_not_called()
        handler.rfile.read1.assert_not_called()
        assert left.recv(1) == b""
        assert rejected_post.state.mock_calls == []
    finally:
        left.close()
        right.close()


def test_malformed_framing_finish_stops_on_peer_eof(rejected_post):
    handler = rejected_post.handler
    handler.headers = HTTPMessage()
    handler.headers["Transfer-Encoding"] = "chunked"
    left, right = socket.socketpair()
    right.close()
    try:
        handler.connection = left

        handler.do_POST()
        handler.finish()

        handler.rfile.read.assert_not_called()
        handler.rfile.read1.assert_not_called()
        assert rejected_post.state.mock_calls == []
    finally:
        left.close()


def test_malformed_framing_finish_ignores_socket_errors(rejected_post, monkeypatch):
    handler = rejected_post.handler
    handler.headers = HTTPMessage()
    handler.headers["Transfer-Encoding"] = "chunked"
    handler.connection.recv.side_effect = OSError()
    monkeypatch.setattr(server.select, "select", Mock(return_value=([handler.connection], [], [])))

    handler.do_POST()
    handler.finish()

    handler.rfile.read.assert_not_called()
    handler.rfile.read1.assert_not_called()
    handler.connection.recv.assert_called_once_with(8192)
    assert rejected_post.state.mock_calls == []


def test_malformed_framing_finish_handles_spurious_readiness(rejected_post, monkeypatch):
    handler = rejected_post.handler
    handler.headers = HTTPMessage()
    handler.headers["Transfer-Encoding"] = "chunked"
    handler.connection.recv.side_effect = [BlockingIOError(), b"x", b""]
    monkeypatch.setattr(server.select, "select", Mock(return_value=([handler.connection], [], [])))
    monkeypatch.setattr(
        server, "time", SimpleNamespace(monotonic=Mock(side_effect=[0.0, 0.05, 0.10, 0.15])),
    )

    handler.do_POST()
    handler.finish()

    handler.connection.setblocking.assert_called_once_with(False)
    assert handler.connection.recv.call_args_list == [call(8192), call(8192), call(8192)]
    handler.rfile.read.assert_not_called()
    handler.rfile.read1.assert_not_called()
    assert rejected_post.state.mock_calls == []


def test_malformed_framing_finish_shares_total_deadline(rejected_post, monkeypatch):
    handler = rejected_post.handler
    handler.headers = HTTPMessage()
    handler.headers["Transfer-Encoding"] = "chunked"
    select_mock = Mock(return_value=([handler.connection], [], []))
    monkeypatch.setattr(server.select, "select", select_mock)
    monkeypatch.setattr(
        server, "time", SimpleNamespace(
            monotonic=Mock(side_effect=[0.0, server.REJECTION_DRAIN_TIMEOUT_S + 0.01]),
        ),
    )

    handler.do_POST()
    handler.finish()

    select_mock.assert_not_called()
    handler.connection.recv.assert_not_called()
    handler.rfile.read.assert_not_called()
    handler.rfile.read1.assert_not_called()
    assert rejected_post.state.mock_calls == []


def test_declared_body_drain_and_finish_share_byte_budget(rejected_post, monkeypatch):
    handler = rejected_post.handler
    handler.headers.replace_header("Content-Length", "2")
    handler.rfile.read1.return_value = b"ab"
    handler.connection.recv.return_value = b"xyz"
    monkeypatch.setattr(server, "MAX_BODY_BYTES", 5)
    monkeypatch.setattr(server.select, "select", Mock(return_value=([handler.connection], [], [])))
    monkeypatch.setattr(
        server, "time", SimpleNamespace(monotonic=Mock(side_effect=[0.0, 0.01, 0.02])),
    )

    handler.do_POST()
    handler.finish()

    handler.rfile.read1.assert_called_once_with(2)
    handler.connection.recv.assert_called_once_with(3)
    assert handler._reject_transport_remaining == 0
    assert rejected_post.state.mock_calls == []


def test_consumed_body_rejection_finish_discards_unread_extra_bytes(rejected_post, monkeypatch):
    handler = rejected_post.handler
    handler.headers["X-Auth-Token"] = "test-web-auth"
    handler.headers.replace_header("Content-Length", "1")
    handler.rfile.read.return_value = b"{"
    handler.connection.recv.side_effect = [b"extra", b""]
    monkeypatch.setattr(server.select, "select", Mock(return_value=([handler.connection], [], [])))
    monkeypatch.setattr(
        server, "time", SimpleNamespace(monotonic=Mock(side_effect=[0.0, 0.01, 0.02])),
    )

    handler.do_POST()
    handler.finish()

    assert handler._json.call_args.args[0] == 400
    handler.rfile.read.assert_called_once_with(1)
    handler.rfile.read1.assert_not_called()
    assert handler.connection.recv.call_args_list == [call(8192), call(8192)]
    assert rejected_post.state.mock_calls == []


@pytest.mark.parametrize("failure", [TimeoutError(), OSError()])
@pytest.mark.parametrize("length", [8, server.MAX_BODY_BYTES])
def test_failed_body_read_reserves_its_budget_before_transport_cleanup(rejected_post, monkeypatch, failure, length):
    handler = rejected_post.handler
    handler.headers["X-Auth-Token"] = "test-web-auth"
    handler.headers.replace_header("Content-Length", str(length))
    handler.rfile.read.side_effect = failure
    handler.connection.recv.return_value = b""
    ready = Mock(return_value=([handler.connection], [], []))
    monkeypatch.setattr(server.select, "select", ready)

    handler.do_POST()

    assert handler._reject_transport_remaining == server.MAX_BODY_BYTES - length
    handler.finish()

    assert handler._json.call_args.args[0] == 400
    handler.rfile.read.assert_called_once_with(length)
    handler.rfile.read1.assert_not_called()
    if length == server.MAX_BODY_BYTES:
        ready.assert_not_called()
        handler.connection.recv.assert_not_called()
    else:
        handler.connection.recv.assert_called_once_with(8192)
    assert rejected_post.state.mock_calls == []


def test_content_type_rejection_drains_without_parsing(rejected_post, monkeypatch):
    handler = rejected_post.handler
    handler.headers["X-Auth-Token"] = "test-web-auth"
    handler.headers.replace_header("Content-Type", "text/plain")
    parse = Mock(side_effect=AssertionError("Unsupported content types must not be parsed"))
    monkeypatch.setattr(server, "json", SimpleNamespace(loads=parse))

    handler.do_POST()

    assert handler._json.call_args.args[0] == 400
    handler.rfile.read.assert_not_called()
    handler.rfile.read1.assert_called_once_with(8)
    parse.assert_not_called()
    assert rejected_post.state.mock_calls == []


@pytest.mark.parametrize("body", [b"{", b"null", b"{}", b'{"provider":"claude","extra":true}'])
def test_consumed_rejected_body_is_not_read_twice(rejected_post, body):
    handler = rejected_post.handler
    handler.headers["X-Auth-Token"] = "test-web-auth"
    handler.headers.replace_header("Content-Length", str(len(body)))
    handler.rfile.read.return_value = body

    handler.do_POST()

    assert handler._json.call_args.args[0] == 400
    handler.rfile.read.assert_called_once_with(len(body))
    handler.rfile.read1.assert_not_called()
    assert rejected_post.state.mock_calls == []


@pytest.mark.parametrize("failure", [TimeoutError(), OSError()])
def test_failed_body_read_is_not_retried_by_rejection(rejected_post, failure):
    handler = rejected_post.handler
    handler.headers["X-Auth-Token"] = "test-web-auth"
    handler.rfile.read.side_effect = failure

    handler.do_POST()

    assert handler._json.call_args.args[0] == 400
    handler.rfile.read.assert_called_once_with(8)
    handler.rfile.read1.assert_not_called()
    assert rejected_post.state.mock_calls == []


def test_successful_post_reads_and_dispatches_once_without_draining(rejected_post):
    handler = rejected_post.handler
    body = b'{"provider":"claude"}'
    handler.headers["X-Auth-Token"] = "test-web-auth"
    handler.headers.replace_header("Content-Length", str(len(body)))
    handler.rfile.read.return_value = body
    rejected_post.state.add_current.return_value = {"ok": True}

    handler.do_POST()

    handler._json.assert_called_once_with(200, {"ok": True})
    handler.rfile.read.assert_called_once_with(len(body))
    handler.rfile.read1.assert_not_called()
    rejected_post.state.add_current.assert_called_once_with(provider="claude")


@pytest.mark.parametrize("authorized,path,content_type,status", [
    (False, "/api/add", "application/json", 403),
    (True, "/api/not-a-route", "application/json", 404),
    (True, "/api/add", "text/plain", 400),
])
def test_rejection_reaches_client_before_delayed_body_is_drained(
    web, monkeypatch, authorized, path, content_type, status,
):
    body = b"not-json"
    received = bytearray()
    drained = threading.Event()
    handler_class = web.server.RequestHandlerClass
    original_setup = handler_class.setup

    def observe_body(handler):
        original_setup(handler)
        stream = handler.rfile

        def read1(length):
            chunk = stream.read1(length)
            received.extend(chunk)
            if len(received) == len(body):
                drained.set()
            return chunk

        handler.rfile = Mock(wraps=stream)
        handler.rfile.read1.side_effect = read1

    monkeypatch.setattr(handler_class, "setup", observe_body)
    headers = [
        f"POST {path} HTTP/1.1", "Host: 127.0.0.1",
        f"Content-Length: {len(body)}", f"Content-Type: {content_type}",
    ]
    if authorized:
        headers.append(f"X-Auth-Token: {web.token}")

    with socket.create_connection(("127.0.0.1", web.server.server_port), timeout=5) as connection:
        connection.sendall(("\r\n".join(headers) + "\r\n\r\n").encode("ascii"))
        response = http.client.HTTPResponse(connection)
        try:
            response.begin()
            assert response.status == status
            assert json.loads(response.read())
            connection.sendall(body)
            assert drained.wait(2)
            assert bytes(received) == body
            assert connection.recv(1) == b""
        finally:
            response.close()

    assert web.state._claude.calls == []
    assert web.state._codex.calls == []


def test_chunked_rejection_returns_400_before_transport_close(web):
    connection = http.client.HTTPConnection("127.0.0.1", web.server.server_port, timeout=5)
    try:
        connection.request(
            "POST", "/api/add", body=b'{"provider":"claude"}',
            headers={
                "X-Auth-Token": web.token,
                "Content-Type": "application/json",
                "Transfer-Encoding": "chunked",
            },
        )
        response = connection.getresponse()
        try:
            assert response.status == 400
            assert json.loads(response.read())
        finally:
            response.close()
    finally:
        connection.close()

    assert web.state._claude.calls == []
    assert web.state._codex.calls == []


def test_chunked_rejection_gracefully_discards_late_bytes_after_response(web):
    body = b"8\r\nnot-json\r\n0\r\n\r\n"
    headers = [
        "POST /api/add HTTP/1.1", "Host: 127.0.0.1",
        "Transfer-Encoding: chunked", "Content-Type: application/json",
        f"X-Auth-Token: {web.token}",
    ]

    with socket.create_connection(("127.0.0.1", web.server.server_port), timeout=5) as connection:
        connection.sendall(("\r\n".join(headers) + "\r\n\r\n").encode("ascii"))
        response = http.client.HTTPResponse(connection)
        try:
            response.begin()
            assert response.status == 400
            assert json.loads(response.read())
            connection.sendall(body)
            assert connection.recv(1) == b""
        finally:
            response.close()

    assert web.state._claude.calls == []
    assert web.state._codex.calls == []
