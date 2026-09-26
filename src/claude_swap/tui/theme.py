"""Desktop-aligned Textual themes and the palette for Rich renderables."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from textual.theme import Theme

ACCENT = "#79e5e0"
CLAUDE = "#bcabf4"
FOREGROUND = "#ecf0f8"
MUTED = "#9ba8bd"
BACKGROUND = "#14171f"
SURFACE = "#202531"
PANEL = "#1a1e28"
SEV_OK = ACCENT
SEV_WARN = "#ebc078"
SEV_CRIT = "#ff91a1"
TRACK = "#323b4d"

ACCENT_LIGHT = "#007888"
CLAUDE_LIGHT = "#7759c2"
FOREGROUND_LIGHT = "#20283a"
MUTED_LIGHT = "#59657b"
BACKGROUND_LIGHT = "#edf1f7"
SURFACE_LIGHT = "#ffffff"
PANEL_LIGHT = "#f5f7fb"
SEV_OK_LIGHT = ACCENT_LIGHT
SEV_WARN_LIGHT = "#846017"
SEV_CRIT_LIGHT = "#bb354e"
TRACK_LIGHT = "#dce3ee"

WARN_PCT = 70.0
CRIT_PCT = 90.0


@dataclass(frozen=True)
class Palette:
    """Resolve Rich colors directly from the theme, before deferred CSS refresh."""

    accent: str
    foreground: str
    muted: str
    sev_ok: str
    sev_warn: str
    sev_crit: str
    track: str
    active: str = ACCENT

    DARK: ClassVar["Palette"]

    def severity(self, pct: float | None) -> str:
        if pct is None:
            return self.muted
        if pct >= CRIT_PCT:
            return self.sev_crit
        if pct >= WARN_PCT:
            return self.sev_warn
        return self.sev_ok

    @classmethod
    def from_theme(cls, theme: Theme, provider: str | None = None) -> "Palette":
        accent = theme.variables["claude-accent"] if provider == "claude" else theme.primary
        return cls(
            accent=accent,
            foreground=theme.foreground,
            muted=theme.secondary,
            sev_ok=accent if provider else theme.success,
            sev_warn=theme.warning,
            sev_crit=theme.error,
            track=theme.variables.get("track", TRACK),
            active=theme.primary,
        )


CSWAP_DARK = Theme(
    name="cswap-mono-dark",
    primary=ACCENT,
    secondary=MUTED,
    accent=ACCENT,
    foreground=FOREGROUND,
    background=BACKGROUND,
    surface=SURFACE,
    panel=PANEL,
    success=SEV_OK,
    warning=SEV_WARN,
    error=SEV_CRIT,
    dark=True,
    variables={
        "footer-background": PANEL,
        "footer-key-foreground": ACCENT,
        "footer-description-foreground": MUTED,
        "block-cursor-background": PANEL,
        "block-cursor-foreground": FOREGROUND,
        "block-cursor-text-style": "none",
        "track": TRACK,
        "claude-accent": CLAUDE,
        "accent-soft": "#203e44",
        "active-border": "#52f3e3",
    },
)

CSWAP_LIGHT = Theme(
    name="cswap-mono-light",
    primary=ACCENT_LIGHT,
    secondary=MUTED_LIGHT,
    accent=ACCENT_LIGHT,
    foreground=FOREGROUND_LIGHT,
    background=BACKGROUND_LIGHT,
    surface=SURFACE_LIGHT,
    panel=PANEL_LIGHT,
    success=SEV_OK_LIGHT,
    warning=SEV_WARN_LIGHT,
    error=SEV_CRIT_LIGHT,
    dark=False,
    variables={
        "footer-background": PANEL_LIGHT,
        "footer-key-foreground": ACCENT_LIGHT,
        "footer-description-foreground": MUTED_LIGHT,
        "block-cursor-background": PANEL_LIGHT,
        "block-cursor-foreground": FOREGROUND_LIGHT,
        "block-cursor-text-style": "none",
        "track": TRACK_LIGHT,
        "claude-accent": CLAUDE_LIGHT,
        "accent-soft": "#e1f6f8",
        "active-border": "#009da9",
    },
)

Palette.DARK = Palette.from_theme(CSWAP_DARK)
