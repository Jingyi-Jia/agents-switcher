"""Private, pipe-controlled backend for the bundled desktop application."""

from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import signal
import sys
import threading
from contextlib import redirect_stdout
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import BinaryIO, TextIO

from claude_swap.tls import use_native_tls
from claude_swap.web.cli import _build_state
from claude_swap.web.server import DashboardState, serve

PROTOCOL_VERSION = 1
MAX_CONTROL_BYTES = 4096


def client_installed(name: str) -> bool:
    directories = [
        os.environ.get("PATH", ""),
        str(Path.home() / ".local" / "bin"),
        str(Path.home() / ".npm-global" / "bin"),
        str(Path.home() / ".volta" / "bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
    ]
    if sys.platform == "win32":
        directories.append(str(Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "npm"))
    node_versions = Path.home() / ".nvm" / "versions" / "node"
    try:
        directories.extend(str(path / "bin") for path in node_versions.iterdir() if path.is_dir())
    except OSError:
        pass
    return shutil.which(name, path=os.pathsep.join(directories)) is not None


class DesktopState(DashboardState):
    def get(self, *, force: bool = False) -> dict:
        result = super().get(force=force)
        try:
            app_version = version("agents-switcher")
        except PackageNotFoundError:
            app_version = "development"
        result["desktop"] = {
            "version": app_version,
            "platform": sys.platform,
            "windowClose": "quit",
            "providers": {
                "claude": {"installed": client_installed("claude")},
                "codex": {"installed": client_installed("codex")},
            },
        }
        return result


def _read_message(stream: BinaryIO) -> dict | None:
    line = stream.readline(MAX_CONTROL_BYTES + 1)
    if not line:
        return None
    if len(line) > MAX_CONTROL_BYTES or not line.endswith(b"\n"):
        raise ValueError("invalid-control-message")
    value = json.loads(line)
    if not isinstance(value, dict):
        raise ValueError("invalid-control-message")
    return value


def _emit(stream: TextIO, payload: dict) -> None:
    stream.write(json.dumps(payload) + "\n")
    stream.flush()


def _restore_external_library_paths() -> None:
    if not getattr(sys, "frozen", False):
        return
    for name in ("LD_LIBRARY_PATH", "LIBPATH"):
        original = os.environ.pop(name + "_ORIG", None)
        if original is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = original
    if sys.platform == "win32":
        ctypes.windll.kernel32.SetDllDirectoryW(None)


def run(control: BinaryIO, status: TextIO) -> int:
    state = server = thread = None
    ready = False
    try:
        initial = _read_message(control)
        if initial is None:
            return 0
        if (
            set(initial) != {"type", "protocol", "token"}
            or initial["type"] != "start"
            or type(initial["protocol"]) is not int
            or initial["protocol"] != PROTOCOL_VERSION
            or not isinstance(initial["token"], str)
            or re.fullmatch(r"[A-Za-z0-9_-]{32,128}", initial["token"]) is None
        ):
            raise ValueError("invalid-start-message")
        with redirect_stdout(sys.stderr):
            _restore_external_library_paths()
            use_native_tls()
            state = _build_state(state_class=DesktopState)
            server, _ = serve(state, host="127.0.0.1", port=0, token=initial["token"])
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
            thread.start()
            _emit(status, {"type": "ready", "protocol": PROTOCOL_VERSION, "port": server.server_port})
            ready = True
            while (message := _read_message(control)) is not None:
                if message != {"type": "shutdown"}:
                    raise ValueError("invalid-control-message")
                break
        return 0
    except (KeyboardInterrupt, SystemExit):
        return 0
    except Exception:
        try:
            _emit(status, {"type": "error", "code": "backend-failed" if ready else "startup-failed"})
        except (OSError, ValueError):
            pass
        return 1
    finally:
        if thread is not None and thread.is_alive():
            server.shutdown()
        if server is not None:
            server.server_close()
        elif state is not None:
            state.close()
        if thread is not None:
            thread.join()


def main() -> int:
    def stop(_signum, _frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        return run(sys.stdin.buffer, sys.stdout)
    except Exception:
        try:
            _emit(sys.stdout, {"type": "error", "code": "shutdown-failed"})
        except (OSError, ValueError):
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
