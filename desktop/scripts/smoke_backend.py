"""Exercise a frozen helper without using the developer's account directories."""

import argparse
import http.client
import json
import os
import queue
import re
import secrets
import subprocess
import sys
import tempfile
import threading
from pathlib import Path


def isolated_environment(root: Path) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "LANG", "LC_ALL", "TZ"}
    }
    for key, directory in {
        "HOME": "home",
        "USERPROFILE": "home",
        "XDG_CONFIG_HOME": "config",
        "XDG_DATA_HOME": "data",
        "XDG_CACHE_HOME": "cache",
        "XDG_STATE_HOME": "state",
        "XDG_RUNTIME_DIR": "runtime",
        "CLAUDE_CONFIG_DIR": "claude",
        "CODEX_HOME": "codex",
        "APPDATA": "appdata",
        "LOCALAPPDATA": "localappdata",
        "TMPDIR": "tmp",
        "TEMP": "tmp",
        "TMP": "tmp",
    }.items():
        path = root / directory
        path.mkdir(mode=0o700, exist_ok=True)
        env[key] = str(path)
    env["PYTHON_KEYRING_BACKEND"] = "keyring.backends.null.Keyring"
    return env


def exercise(executable: Path, shutdown: str) -> None:
    with tempfile.TemporaryDirectory(prefix="agent-switch-smoke-") as directory:
        root = Path(directory)
        with subprocess.Popen(
            [str(executable)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            cwd=root,
            env=isolated_environment(root),
        ) as process:
            try:
                token = secrets.token_urlsafe(32)
                process.stdin.write(json.dumps({"type": "start", "protocol": 1, "token": token}) + "\n")
                process.stdin.flush()
                lines: queue.Queue[str] = queue.Queue()
                threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True).start()
                try:
                    ready = json.loads(lines.get(timeout=45))
                except (queue.Empty, ValueError):
                    raise RuntimeError("Frozen helper did not return a valid ready message") from None
                if (
                    ready.get("type") != "ready"
                    or ready.get("protocol") != 1
                    or type(ready.get("port")) is not int
                    or not 0 < ready["port"] < 65536
                ):
                    raise RuntimeError("Frozen helper returned an invalid protocol or port")
                connection = http.client.HTTPConnection("127.0.0.1", ready["port"], timeout=30)
                try:
                    connection.request("GET", "/api/state")
                    response = connection.getresponse()
                    response.read()
                    if response.status != 403:
                        raise RuntimeError("Frozen helper allowed an unauthenticated state request")
                    connection.request("GET", "/api/state", headers={"X-Auth-Token": token})
                    response = connection.getresponse()
                    state = json.loads(response.read())
                    if response.status != 200 or not all(provider in state for provider in ("claude", "codex")):
                        raise RuntimeError("Authenticated state request failed")
                    for provider in ("claude", "codex"):
                        if state[provider].get("accounts") != [] or not state[provider].get("available"):
                            raise RuntimeError("Frozen provider failed or smoke test account isolation failed")
                finally:
                    connection.close()
                if shutdown == "message":
                    process.stdin.write(json.dumps({"type": "shutdown"}) + "\n")
                    process.stdin.flush()
                else:
                    process.stdin.close()
                if process.wait(timeout=15) != 0:
                    raise RuntimeError("Frozen helper exited unsuccessfully")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)
        print(f"Frozen helper smoke passed ({shutdown} shutdown)")


def check_tls(executable: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="agent-switch-tls-smoke-") as directory:
        root = Path(directory)
        try:
            result = subprocess.run(
                [str(executable), "--smoke-tls"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                cwd=root,
                env=isolated_environment(root),
                timeout=60,
                check=True,
            )
            if result.stdout.strip() != "Frozen helper TLS smoke passed":
                raise RuntimeError("Frozen helper did not confirm TLS smoke success")
        except subprocess.CalledProcessError as exc:
            diagnostic = (exc.stderr or "").strip()
            if re.fullmatch(
                r"Frozen helper TLS smoke failed \((?:initialization|chatgpt\.com|auth\.openai\.com|"
                r"api\.anthropic\.com|platform\.claude\.com): [A-Za-z_][A-Za-z0-9_]*\)",
                diagnostic,
            ):
                raise RuntimeError(diagnostic) from None
            raise RuntimeError("Frozen helper TLS smoke failed") from None
        except (OSError, ValueError, subprocess.SubprocessError):
            raise RuntimeError("Frozen helper TLS smoke failed") from None
    print("Frozen helper TLS smoke passed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--disposable-runner", action="store_true", help="Allow native credential-store access on a disposable macOS/Windows runner only")
    parser.add_argument("--check-tls", action="store_true", help="Also verify native TLS against fixed provider HTTPS hosts without credentials")
    args = parser.parse_args()
    if sys.platform in {"darwin", "win32"} and not args.disposable_runner:
        parser.error("HOME isolation cannot isolate the system credential store; use a disposable VM with --disposable-runner")
    executable = args.executable or (
        Path(__file__).resolve().parents[1]
        / "backend"
        / "agent-switch-backend"
        / ("agent-switch-backend.exe" if sys.platform == "win32" else "agent-switch-backend")
    )
    executable = executable.resolve(strict=True)
    if args.check_tls:
        check_tls(executable)
    for shutdown in ("message", "eof"):
        exercise(executable, shutdown)


if __name__ == "__main__":
    main()
