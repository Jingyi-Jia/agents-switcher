"""Multi-account switcher for Claude Code and Codex.

The import package stays ``claude_swap`` although the distribution is
``agents-switcher``: renaming it would touch every module and test and
conflict with every upstream merge. The desktop bundles its own runtime;
CLI tools installed with uv or pipx get separate environments. Do not install
this distribution and upstream claude-swap into the same Python environment,
because their import packages overlap even though their commands differ.
"""

from importlib.metadata import version

__version__ = version("agents-switcher")

from claude_swap.switcher import ClaudeAccountSwitcher

__all__ = ["ClaudeAccountSwitcher", "__version__"]
