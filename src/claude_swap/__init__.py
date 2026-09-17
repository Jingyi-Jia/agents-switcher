"""Multi-account switcher for Claude Code and Codex.

The import package stays ``claude_swap`` although the distribution is
``agents-switcher``: renaming it would touch every module and test and
conflict with every upstream merge, for no user-visible gain. Installed
tools get their own environment, so the module name never collides in
practice; the COMMAND and the distribution are what had to differ.
"""

from importlib.metadata import version

__version__ = version("agents-switcher")

from claude_swap.switcher import ClaudeAccountSwitcher

__all__ = ["ClaudeAccountSwitcher", "__version__"]
