"""Provider-explicit account actions and session-owned automation."""

from __future__ import annotations

import math
import threading
from collections import deque
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from claude_swap.client_support import CLAUDE_SWITCH_NOTICE
from claude_swap.codex.processes import running_codex_processes
from claude_swap.exceptions import ClaudeSwitchError
from claude_swap.settings import SETTING_SPECS, AutoSwitchSettings, load_settings


class ProviderActionError(ValueError):
    """A safe, user-facing action refusal."""


@dataclass(frozen=True)
class ProviderSpec:
    capabilities: tuple[str, ...]
    switch_notice: str


_COMMON_CAPABILITIES = ("switch", "add", "remove", "disable", "switch-best", "auto")
PROVIDERS = {
    "claude": ProviderSpec((*_COMMON_CAPABILITIES, "token"), CLAUDE_SWITCH_NOTICE),
    "codex": ProviderSpec(
        _COMMON_CAPABILITIES,
        "Switches Codex CLI credentials. Running Codex processes keep their previous "
        "account until restarted; automatic switching waits until they exit.",
    ),
}
AUTO_SESSION_NOTICE = (
    "Auto mode and thresholds last only until the dashboard server or TUI exits "
    "(closing a browser tab does not stop the server). No rules are saved. "
    "Stop controls only this session, not independently started CLI auto loops. "
    "Dry-run is watching only and won't switch "
    "accounts; normal safe token refreshes for usage may still occur."
)
MAX_TOKEN_LENGTH = 16384
MAX_EVENTS = 100


def account_number(value) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ProviderActionError("number must be a positive account slot")
    text = str(value)
    if not text.isascii() or not text.isdigit() or len(text) > 10:
        raise ProviderActionError("number must be a positive account slot")
    if not 1 <= int(text) <= 2147483647:
        raise ProviderActionError("number must be a positive account slot")
    return str(int(text))


def validate_threshold(value) -> float:
    spec = SETTING_SPECS["autoswitch.threshold"]
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not spec.lo <= value <= spec.hi
        or not math.isfinite(value)
    ):
        raise ProviderActionError(f"threshold must be between {spec.lo:g} and {spec.hi:g}")
    return float(value)


def safe_error(error: Exception, *, include_detail=False) -> str:
    if include_detail or isinstance(error, (ProviderActionError, ClaudeSwitchError)):
        text = str(error)
        if not any(marker in text.lower() for marker in (
            "sk-ant-", "access_token", "accesstoken", "refresh_token", "refreshtoken",
            "bearer ", "eyj",
        )):
            return text[:1000]
    return "The action failed. Check the provider's local login and try again."


class ProviderActions:
    """Serialize credential-adjacent work without replacing provider safeguards.

    Collection callers share ``lock`` with these actions and auto ticks. Lifecycle
    calls (``auto.configure`` and ``close``) must be made outside that lock so
    they can join an in-flight worker safely.
    """

    def __init__(self, claude=None, codex=None):
        self._switchers = {"claude": claude, "codex": codex}
        self.lock = threading.RLock()
        self.revision = 0
        self._closed = False
        self.auto = AutoController(self)

    def _spec(self, provider: str) -> ProviderSpec:
        if not isinstance(provider, str) or provider not in PROVIDERS:
            raise ProviderActionError("unknown provider; choose claude or codex")
        return PROVIDERS[provider]

    def capabilities(self, provider: str) -> list[str]:
        return list(self._spec(provider).capabilities)

    def switch_notice(self, provider: str) -> str:
        return self._spec(provider).switch_notice

    def _require(self, provider: str, capability: str):
        spec = self._spec(provider)
        if capability not in spec.capabilities:
            raise ProviderActionError(f"{provider} does not support {capability}")
        if self._closed:
            raise ProviderActionError("This session has closed")
        switcher = self._switchers[provider]
        if switcher is None:
            raise ProviderActionError(f"{provider.capitalize()} is not configured")
        return switcher

    def _claude_result(self, result) -> dict:
        if not isinstance(result, dict) or not isinstance(result.get("switched"), bool):
            raise ProviderActionError("Claude did not report a switch outcome")
        return {
            **result,
            "ok": result["switched"] or result.get("reason") in ("already-active", "activated"),
            "restartRequired": False,
            "switchNotice": self.switch_notice("claude"),
        }

    def switch(self, provider: str, number) -> dict:
        number = account_number(number)
        with self.lock:
            switcher = self._require(provider, "switch")
            try:
                if provider == "claude":
                    return self._claude_result(switcher.switch_to(number, json_output=True))
                if running_codex_processes():
                    raise ProviderActionError(
                        "Quit Codex completely before switching, including its desktop app "
                        "and terminal sessions. Then switch here and reopen Codex; "
                        "your current login has not been changed."
                    )
                result = switcher.switch_to(number)
                message = f"Switched to {result.account.display_label}."
                if result.restart_required:
                    message += (
                        f" Restart Codex — {len(result.processes)} process(es) still "
                        "hold the previous account."
                    )
                return {"ok": True, "switched": True, "reason": "switched",
                        "message": message, "restartRequired": result.restart_required,
                        "switchNotice": self.switch_notice(provider)}
            finally:
                self.revision += 1

    def add_current(self, provider: str) -> dict:
        with self.lock:
            switcher = self._require(provider, "add")
            try:
                if provider == "claude":
                    switcher.add_account(assume_yes=True)
                    message = "Now managing the current Claude account."
                else:
                    account = switcher.add_current(refresh_existing=True)
                    message = f"Now managing {account.display_label} as slot {account.number}."
                return {"ok": True, "message": message}
            finally:
                self.revision += 1

    def set_disabled(self, provider: str, number, disabled: bool) -> dict:
        number = account_number(number)
        if type(disabled) is not bool:
            raise ProviderActionError("disabled must be a boolean")
        with self.lock:
            switcher = self._require(provider, "disable")
            try:
                switcher.set_account_disabled(number, disabled)
                verb = "Disabled" if disabled else "Enabled"
                return {"ok": True, "message": f"{verb} account {number} for automatic selection."}
            finally:
                self.revision += 1

    def remove(self, provider: str, number, *, confirm: bool = False) -> dict:
        number = account_number(number)
        if confirm is not True:
            raise ProviderActionError("Removing an account requires confirm: true")
        with self.lock:
            switcher = self._require(provider, "remove")
            try:
                if provider == "claude":
                    switcher.remove_account(number, assume_yes=True)
                else:
                    switcher.remove_account(number)
                return {"ok": True, "message": f"Removed saved account {number}."}
            finally:
                self.revision += 1

    def switch_best(self, provider: str) -> dict:
        with self.lock:
            switcher = self._require(provider, "switch-best")
            try:
                if provider == "claude":
                    return self._claude_result(switcher.switch(strategy="best", json_output=True))
                from claude_swap.codex.autoswitch import collect_states

                states = collect_states(switcher)
                eligible = [s for s in states if s.eligible and not s.account.disabled]
                if not eligible:
                    return {"ok": False, "switched": False, "reason": "no-target",
                            "message": "No enabled Codex account has known included quota left."}
                active = switcher.store.active_number()
                best = max(eligible, key=lambda s: (s.headroom or 0.0, s.account.number == active))
                if best.account.number == active:
                    return {"ok": True, "switched": False, "reason": "already-active",
                            "message": "The active Codex account already has the most included quota."}
                return self.switch(provider, best.account.number)
            finally:
                self.revision += 1

    def add_token(self, provider: str, token: str, email=None, slot=None, *, confirm=False) -> dict:
        self._require(provider, "token")
        if (
            not isinstance(token, str) or not token.strip() or token.strip() == "-"
            or len(token) > MAX_TOKEN_LENGTH
            or any(char.isspace() or ord(char) < 32 for char in token.strip())
        ):
            raise ProviderActionError("Provide a non-empty token, not an interactive-input marker")
        if email is not None and (not isinstance(email, str) or len(email) > 254):
            raise ProviderActionError("email must be a string of at most 254 characters")
        if slot is not None:
            if type(slot) is not int:
                raise ProviderActionError("slot must be null or a positive integer")
            account_number(slot)
        if type(confirm) is not bool:
            raise ProviderActionError("confirm must be a boolean")
        with self.lock:
            switcher = self._require(provider, "token")
            try:
                accounts = switcher.accounts_snapshot(fetch=set()).accounts
                overwrites = any(
                    (slot is not None and str(account.number) == str(slot))
                    or (email and account.email == email)
                    for account in accounts
                )
                if overwrites and not confirm:
                    raise ProviderActionError("Replacing a saved token or slot requires confirm: true")
                switcher.add_account_from_token(
                    token.strip(), email=email or None, slot=slot, assume_yes=True
                )
            except ProviderActionError:
                raise
            except Exception:
                raise ProviderActionError(
                    "Could not add the token. Check the token, email and slot; existing account safety rules still apply."
                ) from None
            finally:
                self.revision += 1
            return {"ok": True, "message": "Saved the Claude token account. It is not activated until you switch."}

    def close(self) -> None:
        self.auto.close()
        with self.lock:
            self._closed = True


class AutoController:
    """Own one stoppable worker and a bounded event log per provider."""

    def __init__(self, actions: ProviderActions):
        self._actions = actions
        self._lifecycle = threading.Lock()
        self._state_lock = threading.Lock()
        self._closed = False
        self._workers: dict[str, tuple[threading.Thread, threading.Event]] = {}
        self._settings = {}
        self._states = {}
        for provider, switcher in actions._switchers.items():
            root = getattr(switcher, "backup_dir", None)
            if provider == "codex":
                root = getattr(getattr(switcher, "store", None), "root", None)
                root = root.parent if root is not None else None
            settings = load_settings(root) if root is not None else AutoSwitchSettings()
            self._settings[provider] = settings
            self._states[provider] = {
                "mode": "stopped", "threshold": settings.threshold,
                "events": deque(maxlen=MAX_EVENTS),
            }

    def status(self, provider: str) -> dict:
        self._actions._spec(provider)
        with self._state_lock:
            state = self._states[provider]
            return {**state, "events": [dict(event) for event in state["events"]],
                    "sessionNotice": AUTO_SESSION_NOTICE}

    def _event(self, provider: str, message: str, kind: str, at=None) -> None:
        with self._state_lock:
            self._states[provider]["events"].append({
                "message": message[:1500], "kind": kind,
                "at": at or datetime.now(UTC).isoformat(timespec="seconds"),
            })

    def _claude_event(self, event) -> None:
        message = "Claude auto evaluation failed; check the local login." if event.kind == "error" else event.human()
        self._event("claude", message, event.kind, event.ts)
        with self._state_lock:
            if event.kind == "error":
                self._states["claude"]["error"] = message
            elif event.kind == "poll":
                self._states["claude"].pop("error", None)

    def configure(self, provider: str, mode: str, *, threshold=None, confirm=False) -> dict:
        self._actions._require(provider, "auto")
        if mode not in ("dry-run", "live", "stopped"):
            raise ProviderActionError("mode must be dry-run, live or stopped")
        if type(confirm) is not bool:
            raise ProviderActionError("confirm must be a boolean")
        if mode == "live" and not confirm:
            raise ProviderActionError("Live auto-switching requires confirm: true")
        if threshold is not None:
            threshold = validate_threshold(threshold)
        with self._lifecycle:
            if self._closed:
                raise ProviderActionError("This auto session has closed")
            self._stop(provider)
            with self._actions.lock:
                switcher = self._actions._require(provider, "auto")
                settings = self._settings[provider]
                if threshold is not None:
                    settings = replace(settings, threshold=threshold)
                    self._settings[provider] = settings
                tick = None
                if mode != "stopped":
                    if provider == "claude":
                        from claude_swap.autoswitch import AutoSwitchEngine

                        engine = AutoSwitchEngine(
                            switcher, settings, self._claude_event, dry_run=mode == "dry-run"
                        )
                        def tick():
                            return engine._next_delay(engine.tick())
                    else:
                        from claude_swap.codex.autoswitch import AutoSettings, run_once

                        codex_settings = AutoSettings(
                            threshold=settings.threshold, hysteresis_pct=settings.hysteresis_pct,
                            cooldown_seconds=settings.cooldown_seconds,
                            interval_seconds=settings.interval_seconds,
                        )

                        def tick():
                            decision = run_once(
                                switcher, settings=codex_settings, dry_run=mode == "dry-run"
                            )
                            prefix = "[dry-run] " if mode == "dry-run" else ""
                            self._event(provider, prefix + decision.reason, decision.action.value)
                            return settings.interval_seconds

                with self._state_lock:
                    self._states[provider].update(mode=mode, threshold=settings.threshold)
                    self._states[provider].pop("error", None)
                self._actions.revision += 1
                message = f"{provider.capitalize()} auto-switch is {mode}."
                self._event(provider, message, "mode")
                if tick is not None:
                    stop = threading.Event()
                    worker = threading.Thread(
                        target=self._run, args=(provider, tick, settings.interval_seconds, stop),
                        name=f"provider-auto-{provider}", daemon=True,
                    )
                    self._workers[provider] = (worker, stop)
                    worker.start()
        return {"ok": True, "message": message,
                "auto": self.status(provider)}

    def _run(self, provider, tick, interval, stop) -> None:
        while not stop.is_set():
            with self._actions.lock:
                if stop.is_set():
                    break
                try:
                    delay = tick()
                except Exception:
                    delay = interval
                    message = "Auto evaluation failed; check the provider's local login."
                    with self._state_lock:
                        self._states[provider]["error"] = message
                    self._event(provider, message, "error")
                else:
                    if provider == "codex":
                        with self._state_lock:
                            self._states[provider].pop("error", None)
                self._actions.revision += 1
            stop.wait(delay)

    def _stop(self, provider) -> None:
        previous = self._workers.pop(provider, None)
        if previous is not None:
            worker, stop = previous
            stop.set()
            worker.join()
            if provider == "claude":
                with self._actions.lock:
                    self._actions._switchers[provider].clear_poll_policy_inputs()
        with self._state_lock:
            self._states[provider]["mode"] = "stopped"

    def close(self) -> None:
        with self._lifecycle:
            self._closed = True
            for _, stop in self._workers.values():
                stop.set()
            for provider in PROVIDERS:
                self._stop(provider)
