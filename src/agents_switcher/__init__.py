"""Multi-account switcher for Claude Code and Codex.

The distribution is ``agents-switcher``, the command is ``agent-switch``, and
the import package is ``agents_switcher``. No compatibility package is installed
under upstream's Python namespace. Historical account-storage paths are kept
unchanged so upgrading does not move or lose saved credentials.
"""

from importlib.metadata import version

__version__ = version("agents-switcher")

from agents_switcher.switcher import ClaudeAccountSwitcher

__all__ = ["ClaudeAccountSwitcher", "__version__"]
