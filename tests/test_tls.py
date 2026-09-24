from __future__ import annotations

import http.client
import importlib.util
import os
import ssl
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from claude_swap import cli, tls


_LOCALHOST_TEST_CERTIFICATE = """-----BEGIN CERTIFICATE-----
MIIBuzCCAWKgAwIBAgIUYDgsma1cx1EAMEMBaPHa6aOjHowwCgYIKoZIzj0EAwIw
FDESMBAGA1UEAwwJbG9jYWxob3N0MCAXDTI2MDkyNDA1Mjc1MloYDzIxMjYwODMx
MDUyNzUyWjAUMRIwEAYDVQQDDAlsb2NhbGhvc3QwWTATBgcqhkjOPQIBBggqhkjO
PQMBBwNCAARe/1G7ktIUY253PEQg+NsRVSEJylgcHki/FqrK64baWErpUmBOpVdz
uLU/ggJCofGR7IrqPuKES2C6+k+1RVT5o4GPMIGMMB0GA1UdDgQWBBRevvLe4T0I
ToDxRW9EchwwdLK4yDAfBgNVHSMEGDAWgBRevvLe4T0IToDxRW9EchwwdLK4yDAU
BgNVHREEDTALgglsb2NhbGhvc3QwDwYDVR0TAQH/BAUwAwEB/zAOBgNVHQ8BAf8E
BAMCAoQwEwYDVR0lBAwwCgYIKwYBBQUHAwEwCgYIKoZIzj0EAwIDRwAwRAIgB2l/
PZ2HO8EvbFcJTZqG/XdYNItJgWk7yENW/AlRvFoCIBc8F579bj3btPRWoXmQ0tmZ
HYe0rUbzqwhYOF5oHvbi
-----END CERTIFICATE-----
"""
_LOCALHOST_TEST_KEY = """-----BEGIN PRIVATE KEY-----
MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQg79qeOIPMWOOwnTVA
DSbv1nQpnpyYBXHQVyncg0szebKhRANCAARe/1G7ktIUY253PEQg+NsRVSEJylgc
Hki/FqrK64baWErpUmBOpVdzuLU/ggJCofGR7IrqPuKES2C6+k+1RVT5
-----END PRIVATE KEY-----
"""


@pytest.fixture
def packaging_script():
    def load(name):
        path = Path(__file__).resolve().parents[1] / "desktop" / "scripts" / f"{name}.py"
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    return load


def test_native_tls_injects_truststore(monkeypatch):
    inject = Mock()
    monkeypatch.setitem(sys.modules, "truststore", SimpleNamespace(inject_into_ssl=inject))

    tls.use_native_tls()

    inject.assert_called_once_with()


@pytest.mark.parametrize("failure", [ImportError("unavailable"), RuntimeError("unavailable")])
def test_native_tls_initialization_is_best_effort(monkeypatch, failure):
    inject = Mock(side_effect=failure)
    monkeypatch.setitem(sys.modules, "truststore", SimpleNamespace(inject_into_ssl=inject))

    tls.use_native_tls()

    inject.assert_called_once_with()


@pytest.mark.parametrize("mode", ["native", "missing", "broken"])
def test_native_tls_and_fallback_preserve_https_verification(mode):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    result = subprocess.run(
        [sys.executable, "-c", """
import http.client
import ssl
import sys
from types import SimpleNamespace

from claude_swap.tls import use_native_tls

original_context = ssl.SSLContext
if sys.argv[1] == "missing":
    sys.modules["truststore"] = None
elif sys.argv[1] == "broken":
    def fail():
        raise RuntimeError("native verifier unavailable")
    sys.modules["truststore"] = SimpleNamespace(inject_into_ssl=fail)

use_native_tls()

if sys.argv[1] == "native":
    import truststore
    assert ssl.SSLContext is truststore.SSLContext
else:
    assert ssl.SSLContext is original_context

for context in (
    ssl.create_default_context(),
    ssl._create_default_https_context(),
    http.client.HTTPSConnection("localhost")._context,
):
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
""", mode],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("mode", ["native", "fallback"])
@pytest.mark.parametrize("scenario", ["trusted", "untrusted", "wrong-hostname"])
def test_local_tls_rejects_untrusted_certificates_and_wrong_hostnames(tmp_path, mode, scenario):
    certificate = tmp_path / "localhost.pem"
    key = tmp_path / "localhost-key.pem"
    certificate.write_text(_LOCALHOST_TEST_CERTIFICATE)
    key.write_text(_LOCALHOST_TEST_KEY)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    result = subprocess.run(
        [sys.executable, "-c", """
import socket
import ssl
import sys
import threading

from claude_swap.tls import use_native_tls

mode, scenario, certificate, key = sys.argv[1:]
server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
server_context.load_cert_chain(certificate, key)
listener = socket.socket()
listener.bind(("127.0.0.1", 0))
listener.listen(1)
listener.settimeout(5)
port = listener.getsockname()[1]

def serve():
    try:
        with listener.accept()[0] as connection:
            connection.settimeout(5)
            with server_context.wrap_socket(connection, server_side=True) as secure:
                secure.recv(1)
    except (ssl.SSLError, OSError):
        pass

thread = threading.Thread(target=serve, daemon=True)
thread.start()
if mode == "fallback":
    sys.modules["truststore"] = None
use_native_tls()
context = ssl.create_default_context(cafile=None if scenario == "untrusted" else certificate)
hostname = "wrong.invalid" if scenario == "wrong-hostname" else "localhost"
try:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
        with context.wrap_socket(connection, server_hostname=hostname) as secure:
            secure.sendall(b"x")
except ssl.SSLCertVerificationError:
    assert scenario != "trusted", "A trusted localhost certificate was rejected"
else:
    assert scenario == "trusted", "An invalid certificate or hostname was accepted"
finally:
    listener.close()
    thread.join(timeout=5)
    assert not thread.is_alive()
""", mode, scenario, str(certificate), str(key)],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr


def test_cli_initializes_native_tls_before_provider_dispatch(monkeypatch):
    from claude_swap.codex import cli as codex_cli

    events = []
    monkeypatch.setattr(cli, "use_native_tls", lambda: events.append("tls"))
    monkeypatch.setattr(sys, "argv", ["agent-switch", "codex", "status"])

    def dispatch(argv):
        assert argv == ["status"]
        assert events == ["tls"]
        events.append("codex")

    monkeypatch.setattr(codex_cli, "codex_command", dispatch)

    cli.main()

    assert events == ["tls", "codex"]


@pytest.mark.parametrize("argv", [[], ["--smoke-tls"]])
def test_packaging_entry_dispatches_tls_only_when_explicit(monkeypatch, packaging_script, argv):
    entry = packaging_script("backend_entry")
    smoke = Mock(return_value=0)
    normal = Mock()
    monkeypatch.setattr(entry, "smoke_tls", smoke)
    monkeypatch.setattr(entry.runpy, "run_module", normal)
    monkeypatch.setattr(sys, "argv", ["agent-switch-backend", *argv])

    assert entry.main() == 0

    if argv:
        smoke.assert_called_once_with()
        normal.assert_not_called()
    else:
        normal.assert_called_once_with("claude_swap.desktop", run_name="__main__")
        smoke.assert_not_called()


@pytest.fixture
def tls_probe(monkeypatch, packaging_script):
    entry = packaging_script("backend_entry")
    initialize = Mock()
    context = SimpleNamespace(verify_mode=ssl.CERT_REQUIRED, check_hostname=True)
    monkeypatch.setattr(tls, "use_native_tls", initialize)
    monkeypatch.setattr(ssl, "create_default_context", Mock(return_value=context))
    monkeypatch.setitem(sys.modules, "truststore", SimpleNamespace(SSLContext=ssl.SSLContext))
    connection = Mock()
    monkeypatch.setattr(http.client, "HTTPSConnection", connection)
    return entry, initialize, context, connection


@pytest.mark.parametrize("status", [200, 301, 401, 403, 405, 500])
def test_packaging_tls_probe_accepts_http_denials_without_following_redirects(tls_probe, capsys, status):
    entry, initialize, context, connection = tls_probe
    connection.return_value.getresponse.return_value.status = status

    assert entry.smoke_tls() == 0

    initialize.assert_called_once_with()
    assert [call.args for call in connection.call_args_list] == [
        ("chatgpt.com",), ("auth.openai.com",), ("api.anthropic.com",), ("platform.claude.com",),
    ]
    assert all(call.kwargs == {"timeout": 10, "context": context} for call in connection.call_args_list)
    assert [call.args for call in connection.return_value.request.call_args_list] == [("HEAD", "/")] * 4
    assert all(not call.kwargs for call in connection.return_value.request.call_args_list)
    assert connection.return_value.close.call_count == 4
    assert connection.return_value.getresponse.return_value.close.call_count == 4
    assert capsys.readouterr().out == "Frozen helper TLS smoke passed\n"


@pytest.mark.parametrize("failure", ["missing", "inactive", "certificate-check", "hostname-check"])
def test_packaging_tls_probe_requires_verified_native_context(monkeypatch, tls_probe, capsys, failure):
    entry, initialize, context, connection = tls_probe
    if failure == "missing":
        monkeypatch.setitem(sys.modules, "truststore", None)
    elif failure == "inactive":
        monkeypatch.setitem(sys.modules, "truststore", SimpleNamespace(SSLContext=object))
    elif failure == "certificate-check":
        context.verify_mode = ssl.CERT_NONE
    else:
        context.check_hostname = False

    assert entry.smoke_tls() == 1

    connection.assert_not_called()
    output = capsys.readouterr()
    assert output.out == ""
    exception = "ModuleNotFoundError" if failure == "missing" else "RuntimeError"
    assert output.err == f"Frozen helper TLS smoke failed (initialization: {exception})\n"


@pytest.mark.parametrize("failure", [ssl.SSLCertVerificationError("sensitive"), TimeoutError("sensitive"), OSError("sensitive")])
def test_packaging_tls_probe_redacts_network_failures_and_closes_connection(tls_probe, capsys, failure):
    entry, initialize, context, connection = tls_probe
    connection.return_value.request.side_effect = failure

    assert entry.smoke_tls() == 1

    connection.return_value.close.assert_called_once_with()
    assert connection.call_count == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == f"Frozen helper TLS smoke failed (chatgpt.com: {type(failure).__name__})\n"


@pytest.mark.parametrize("enabled", [False, True])
def test_smoke_script_checks_tls_only_when_requested(monkeypatch, packaging_script, tmp_path, enabled):
    smoke = packaging_script("smoke_backend")
    executable = tmp_path / "helper"
    executable.touch()
    check = Mock()
    exercise = Mock()
    monkeypatch.setattr(smoke, "check_tls", check)
    monkeypatch.setattr(smoke, "exercise", exercise)
    monkeypatch.setattr(sys, "argv", [
        "smoke_backend.py", "--executable", str(executable), "--disposable-runner",
        *(["--check-tls"] if enabled else []),
    ])

    smoke.main()

    if enabled:
        check.assert_called_once_with(executable)
    else:
        check.assert_not_called()
    assert [call.args for call in exercise.call_args_list] == [(executable, "message"), (executable, "eof")]


def test_smoke_script_uses_isolated_bounded_tls_subprocess(monkeypatch, packaging_script, capsys):
    smoke = packaging_script("smoke_backend")
    run = Mock(return_value=SimpleNamespace(stdout="Frozen helper TLS smoke passed\n"))
    monkeypatch.setattr(smoke.subprocess, "run", run)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-do-not-forward")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-do-not-forward")
    monkeypatch.setenv("SSL_CERT_FILE", "synthetic-do-not-forward")

    smoke.check_tls(Path("helper"))

    assert run.call_args.args == (["helper", "--smoke-tls"],)
    options = run.call_args.kwargs
    assert options["timeout"] == 60 and options["check"] is True
    assert options["stdin"] == subprocess.DEVNULL
    assert options["stderr"] == subprocess.PIPE
    assert options["stdout"] == subprocess.PIPE
    assert not {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "SSL_CERT_FILE"}.intersection(options["env"])
    for key in ("HOME", "CLAUDE_CONFIG_DIR", "CODEX_HOME"):
        assert Path(options["env"][key]).is_relative_to(options["cwd"])
    assert capsys.readouterr().out == "Frozen helper TLS smoke passed\n"


@pytest.mark.parametrize("failure", [
    subprocess.TimeoutExpired("helper", 60, output="sensitive"),
    subprocess.CalledProcessError(1, "helper", output="sensitive"),
    OSError("sensitive"),
])
def test_smoke_script_redacts_tls_subprocess_failures(monkeypatch, packaging_script, failure):
    smoke = packaging_script("smoke_backend")
    monkeypatch.setattr(smoke.subprocess, "run", Mock(side_effect=failure))

    with pytest.raises(RuntimeError, match="^Frozen helper TLS smoke failed$") as raised:
        smoke.check_tls(Path("helper"))

    assert raised.value.__suppress_context__


@pytest.mark.parametrize("diagnostic", [
    "Frozen helper TLS smoke failed (initialization: ModuleNotFoundError)",
    "Frozen helper TLS smoke failed (chatgpt.com: SSLCertVerificationError)",
    "Frozen helper TLS smoke failed (auth.openai.com: TimeoutError)",
    "Frozen helper TLS smoke failed (api.anthropic.com: ConnectionResetError)",
    "Frozen helper TLS smoke failed (platform.claude.com: OSError)",
])
def test_smoke_script_preserves_only_safe_tls_diagnostics(monkeypatch, packaging_script, diagnostic):
    smoke = packaging_script("smoke_backend")
    failure = subprocess.CalledProcessError(1, "helper", stderr=diagnostic + "\n")
    monkeypatch.setattr(smoke.subprocess, "run", Mock(side_effect=failure))

    with pytest.raises(RuntimeError) as raised:
        smoke.check_tls(Path("helper"))

    assert str(raised.value) == diagnostic
    assert raised.value.__suppress_context__


@pytest.mark.parametrize("diagnostic", [
    "sensitive",
    "Frozen helper TLS smoke failed (untrusted.example: OSError)",
    "Frozen helper TLS smoke failed (chatgpt.com: OSError: sensitive)",
    "Frozen helper TLS smoke failed (chatgpt.com: OSError)\nsensitive",
])
def test_smoke_script_discards_unexpected_tls_diagnostics(monkeypatch, packaging_script, diagnostic):
    smoke = packaging_script("smoke_backend")
    failure = subprocess.CalledProcessError(1, "helper", stderr=diagnostic)
    monkeypatch.setattr(smoke.subprocess, "run", Mock(side_effect=failure))

    with pytest.raises(RuntimeError, match="^Frozen helper TLS smoke failed$") as raised:
        smoke.check_tls(Path("helper"))

    assert raised.value.__suppress_context__


@pytest.mark.parametrize("output", ["", "unexpected sensitive output"])
def test_smoke_script_rejects_helpers_without_explicit_tls_success(monkeypatch, packaging_script, output):
    smoke = packaging_script("smoke_backend")
    monkeypatch.setattr(smoke.subprocess, "run", Mock(return_value=SimpleNamespace(stdout=output)))

    with pytest.raises(RuntimeError, match="^Frozen helper did not confirm TLS smoke success$"):
        smoke.check_tls(Path("helper"))
