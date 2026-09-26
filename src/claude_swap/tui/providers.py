"""Provider selection without initializing either account backend."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, ListView, Static

from claude_swap.tui.widgets import AppHeader, MenuItem


class ProviderScreen(Screen):
    BINDINGS = [
        Binding("c", "app.open_claude", "Claude Code"),
        Binding("x", "app.open_codex", "Codex"),
        Binding("escape", "back", "Back"),
        Binding("q", "app.quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        yield AppHeader()
        with Vertical(id="provider-content"):
            yield Static("Your accounts, in reach.", id="provider-title")
            yield Static(
                "Choose a provider to view usage and manage logins.", id="provider-prompt",
            )
            yield ListView(
                MenuItem(
                    "■  Claude Code", "claude",
                    description="CLI accounts · separate from Claude Desktop",
                ),
                MenuItem(
                    "■  Codex", "codex",
                    description="Shared auth.json · quit CLI and Desktop first",
                ),
                id="providers",
            )
            yield Static(
                "↑ ↓ choose   Enter open   ·   c Claude / x Codex",
                classes="navigation-hint",
            )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(ListView).focus()

    def on_screen_resume(self) -> None:
        self.app._claude_active = False

    def action_back(self) -> None:
        if len(self.app.screen_stack) > 2:
            self.app.pop_screen()
        else:
            self.app.exit()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item.action_id == "claude":
            self.app.action_open_claude()
        else:
            self.app.action_open_codex()
