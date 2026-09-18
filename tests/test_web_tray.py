"""Tests for the tray/menu-bar readout.

The display logic is pure, so it is covered on every platform -- including the
ones whose toolkit cannot be installed in CI, which is the whole reason it was
written that way.
"""

from __future__ import annotations

import pytest

from claude_swap.web import tray
from claude_swap.web.tray import TrayEntry, build_model


def account(number="1", email="a@e.com", used=None, active=False, **extra):
    entry = {"number": number, "email": email, "active": active}
    if used is not None:
        entry["windows"] = [{"usedPercent": u} for u in (
            used if isinstance(used, (list, tuple)) else [used]
        )]
    entry.update(extra)
    return entry


def provider(accounts=(), available=True, live=None):
    state = {"available": available, "accounts": list(accounts)}
    if live:
        state["liveLogin"] = live
    return state


def state(claude=None, codex=None):
    return {"claude": claude or provider(), "codex": codex or provider()}


class TestTitle:
    def test_shows_both_providers(self):
        model = build_model(state(
            claude=provider([account(used=34, active=True)]),
            codex=provider([account(used=10, active=True)]),
        ))
        assert model.title == "C 66%  X 90%"

    def test_headroom_comes_from_the_WORST_window(self):
        # An account is limited by its tightest window; an average would hide it.
        model = build_model(state(claude=provider([account(used=[20, 95], active=True)])))
        assert "C 5%" in model.title

    def test_low_headroom_raises_attention(self):
        model = build_model(state(claude=provider([account(used=95, active=True)])))
        assert model.attention is True

    def test_comfortable_headroom_does_not(self):
        model = build_model(state(
            claude=provider([account(used=10, active=True)]),
            codex=provider([account(used=10, active=True)]),
        ))
        assert model.attention is False

    def test_an_account_on_credits_is_neither_zero_nor_healthy(self):
        """'0%' would read as broken and '100%' as healthy; it is neither, so it
        gets its own mark."""
        model = build_model(state(
            codex=provider([account(used=100, active=True, onCredits=True)])
        ))
        assert "X $" in model.title
        assert model.attention is True

    def test_an_unavailable_provider_is_a_dash_not_a_number(self):
        model = build_model(state(claude=provider(available=False)))
        assert "C —" in model.title

    def test_no_active_account_is_a_dash(self):
        model = build_model(state(claude=provider([account(used=10, active=False)])))
        assert "C —" in model.title

    def test_unknown_usage_is_a_question_mark(self):
        model = build_model(state(claude=provider([account(active=True)])))
        assert "C ?" in model.title


class TestMenu:
    def test_each_provider_gets_a_header(self):
        model = build_model(state(claude=provider([account(used=5, active=True)])))
        headers = [e.label for e in model.entries if not e.enabled and not e.is_action]
        assert "Claude Code" in headers and "Codex" in headers

    def test_accounts_are_clickable_except_the_active_one(self):
        model = build_model(state(claude=provider([
            account("1", "one@e.com", used=10, active=True),
            account("2", "two@e.com", used=20),
        ])))
        actions = [e for e in model.entries if e.is_action]
        assert actions[0].checked is True and actions[0].enabled is False
        assert actions[1].enabled is True and actions[1].number == "2"

    def test_an_account_line_shows_what_is_left(self):
        model = build_model(state(claude=provider([account(used=25, active=True)])))
        assert any("75% left" in e.label for e in model.entries)

    def test_a_credits_account_says_so_rather_than_showing_a_number(self):
        model = build_model(state(
            codex=provider([account(used=100, active=True, onCredits=True)])
        ))
        assert any("on credits" in e.label for e in model.entries)

    def test_a_failed_read_is_shown_as_an_error(self):
        model = build_model(state(claude=provider([account(active=True, error="boom")])))
        assert any("error" in e.label for e in model.entries)

    def test_an_unmanaged_live_login_is_offered_as_context(self):
        model = build_model(state(
            codex=provider([], live={"email": "live@example.com", "managed": False})
        ))
        assert any("not managed" in e.label for e in model.entries)

    def test_no_accounts_and_no_login_says_so(self):
        model = build_model(state())
        assert any("no accounts" in e.label for e in model.entries)

    def test_an_unavailable_provider_is_reported_in_the_menu(self):
        model = build_model(state(claude=provider(available=False)))
        assert any("unavailable" in e.label for e in model.entries)


class TestEmailShortening:
    def test_a_short_address_is_untouched(self):
        assert tray._short("a@b.com") == "a@b.com"

    def test_a_long_address_keeps_the_local_part(self):
        # Which account it is matters more than the domain.
        out = tray._short("someverylongname@example.com", limit=18)
        assert out.startswith("someverylongname@")
        assert len(out) <= 19

    def test_an_unsplittable_address_is_simply_trimmed(self):
        out = tray._short("x" * 40, limit=10)
        assert len(out) == 10 and out.endswith("…")


class TestBackendSelection:
    def test_macos_prefers_rumps(self, monkeypatch):
        """pystray's macOS backend puts the title in a TOOLTIP, and a number you
        must hover for is not an at-a-glance readout."""
        monkeypatch.setitem(__import__("sys").modules, "rumps", object())
        assert tray.available_backend("darwin") == "rumps"

    def test_macos_falls_back_to_pystray(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def no_rumps(name, *a, **k):
            if name == "rumps":
                raise ImportError("no rumps")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", no_rumps)
        assert tray.available_backend("darwin") in ("pystray", None)

    def test_other_platforms_use_pystray(self, monkeypatch):
        monkeypatch.setitem(__import__("sys").modules, "pystray", object())
        assert tray.available_backend("win32") == "pystray"
        assert tray.available_backend("linux") == "pystray"

    def test_nothing_installed_is_reported_not_guessed(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def nothing(name, *a, **k):
            if name in ("rumps", "pystray"):
                raise ImportError("absent")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", nothing)
        assert tray.available_backend("linux") is None

    def test_the_install_hint_is_platform_specific(self):
        assert "tray-macos" in tray.install_hint("darwin")
        assert "[tray]" in tray.install_hint("linux")

    def test_run_without_a_backend_raises_with_the_hint(self, monkeypatch):
        # backend=None means AUTO-DETECT, not "none", so the absence has to be
        # forced at the detector rather than passed in.
        monkeypatch.setattr(tray, "available_backend", lambda *a, **k: None)
        with pytest.raises(RuntimeError, match="install one with"):
            tray.run(lambda: None, lambda *a: None, lambda: None)
