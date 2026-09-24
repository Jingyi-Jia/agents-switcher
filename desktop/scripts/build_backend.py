"""Freeze only the installed application, its dependencies, and source assets."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DESKTOP = ROOT / "desktop"


def main() -> None:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onedir",
            "--console",
            "--name=agent-switch-backend",
            f"--distpath={DESKTOP / 'backend'}",
            f"--workpath={DESKTOP / '.build' / 'pyinstaller'}",
            f"--specpath={DESKTOP / '.build'}",
            f"--paths={ROOT / 'src'}",
            "--hidden-import=claude_swap.desktop",
            "--collect-submodules=claude_swap",
            "--collect-all=textual",
            "--recursive-copy-metadata=agents-switcher",
            f"--add-data={ROOT / 'src' / 'claude_swap' / 'tui' / 'cswap.tcss'}:claude_swap/tui",
            str(DESKTOP / "scripts" / "backend_entry.py"),
        ],
        cwd=ROOT,
        check=True,
    )


if __name__ == "__main__":
    main()
