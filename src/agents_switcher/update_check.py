"""Check this repository's releases and upgrade only agents-switcher."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

from agents_switcher.cache import CACHE_DIR, MISSING, read_cache, write_cache

CACHE_PATH = CACHE_DIR / "agents-switcher-update.json"
CACHE_TTL = 24 * 3600  # 24 hours
RELEASES_URL = "https://github.com/Jingyi-Jia/agents-switcher/releases"
LATEST_RELEASE_URL = "https://api.github.com/repos/Jingyi-Jia/agents-switcher/releases/latest"
SOURCE_URL = "git+https://github.com/Jingyi-Jia/agents-switcher.git"
MAX_RESPONSE_BYTES = 1024 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, url):
        return None


def _parse_version(v: str) -> tuple[int, ...]:
    if (not isinstance(v, str) or len(v) > 64
            or not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", v)):
        raise ValueError("A stable release version is required")
    return tuple(int(x) for x in v.split("."))


def _detect_install_method() -> str | None:
    """Return 'uv', 'pipx', or None if we can't tell."""
    prefix = Path(sys.prefix)
    parts = tuple(p.lower() for p in prefix.parts)
    pairs = list(zip(parts, parts[1:]))

    if ("uv", "tools") in pairs:
        return "uv"
    if ("pipx", "venvs") in pairs:
        return "pipx"

    # Env-var override: only trust if sys.prefix is actually under it.
    for env_var, name in (("UV_TOOL_DIR", "uv"), ("PIPX_HOME", "pipx")):
        root = os.environ.get(env_var)
        if root:
            try:
                if prefix.is_relative_to(Path(root)):
                    return name
            except (ValueError, OSError):
                pass
    return None


def check_for_update(current_version: str) -> str | None:
    """Return a notification string if a newer version exists, else None."""
    try:
        latest_version = None

        cached_data = read_cache(CACHE_PATH, CACHE_TTL)
        if cached_data is not MISSING:
            latest_version = cached_data
        else:
            try:
                req = urllib.request.Request(LATEST_RELEASE_URL, headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "agents-switcher",
                    "X-GitHub-Api-Version": "2022-11-28",
                })
                with urllib.request.build_opener(_NoRedirect()).open(req, timeout=2) as resp:
                    body = resp.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise ValueError("Release response exceeded its size limit")
                data = json.loads(body)
                tag = data["tag_name"]
                candidate = tag[1:] if isinstance(tag, str) and tag.startswith("v") else ""
                _parse_version(candidate)
                if (data.get("draft") is not False or data.get("prerelease") is not False
                        or data.get("html_url") != f"{RELEASES_URL}/tag/{tag}"):
                    raise ValueError("Unexpected release metadata")
                wheel = f"agents_switcher-{candidate}-py3-none-any.whl"
                assets = data.get("assets")
                if not isinstance(assets, list) or not 0 < len(assets) <= 100 or not any(
                    isinstance(asset, dict) and asset.get("name") == wheel
                    and asset.get("state") == "uploaded"
                    and type(asset.get("size")) is int and asset["size"] > 0
                    and asset.get("browser_download_url") == f"{RELEASES_URL}/download/{tag}/{wheel}"
                    for asset in assets
                ):
                    raise ValueError("Release has no compatible CLI distribution")
                latest_version = candidate
            except Exception:
                latest_version = None

            write_cache(CACHE_PATH, latest_version)

        if latest_version and _parse_version(latest_version) > _parse_version(current_version):
            method = _detect_install_method()
            direct = {
                "uv": "uv tool upgrade agents-switcher",
                "pipx": "pipx upgrade agents-switcher",
            }.get(method or "")
            if direct and sys.platform != "win32":
                hint = "Run `agent-switch upgrade` to update."
            elif direct:
                hint = f"Run `{direct}` to update."
            else:
                hint = "Run `agent-switch upgrade` for upgrade instructions."
            return (
                f"A newer version of agents-switcher is available ({latest_version}). "
                f"You are using {current_version}. {hint}"
            )
        return None
    except Exception:
        return None


def run_self_upgrade() -> int:
    """Run the appropriate upgrade command for the current install method.

    Returns the subprocess exit code, or 1 if detection failed or the package
    manager is missing from PATH.
    """
    from agents_switcher.printer import accent, error

    method = _detect_install_method()
    commands = {
        "uv": ["uv", "tool", "upgrade", "agents-switcher"],
        "pipx": ["pipx", "upgrade", "agents-switcher"],
    }
    cmd = commands.get(method or "")
    if cmd is None:
        error(
            "Could not detect install method (looked for uv tool / pipx).\n"
            f"  sys.prefix:     {sys.prefix}\n"
            f"  sys.executable: {sys.executable}\n"
            "To upgrade manually, run one of:\n"
            "  uv tool upgrade agents-switcher\n"
            "  pipx upgrade agents-switcher\n"
            f"  {sys.executable} -m pip install --upgrade {SOURCE_URL}\n"
            "If you installed with `pip install -e .`, use `git pull` instead."
        )
        return 1

    if sys.platform == "win32":
        print(f"To upgrade agents-switcher on Windows, run:\n  {accent(' '.join(cmd))}")
        return 1

    try:
        result = subprocess.run(cmd, check=False)
        return result.returncode
    except FileNotFoundError:
        error(
            f"Detected {method} install but `{cmd[0]}` is not on PATH. "
            "Run the upgrade manually from a shell where it is available."
        )
        return 1
