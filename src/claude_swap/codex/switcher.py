"""Switching the active Codex account.

The operation itself is small -- write a saved ``auth.json`` back into place --
and almost all of the code here is about the three ways doing that naively
loses a credential.

1. ROTATION. Codex rotates its refresh token. The copy saved when a slot was
   added goes stale the moment that account is used, so switching away without
   first writing the LIVE tokens back into the outgoing slot means switching
   back later restores a superseded token: the account looks dead and has to be
   re-authenticated. Sync-back is not an optimisation, it is what makes
   switching reversible.
2. MISFILING. Sync-back is only safe when the live credential really belongs to
   the slot being written. Identity is compared on ``account_id`` alone, and an
   unknown id never matches -- filing one account's tokens under another's slot
   produces a slot that silently authenticates as the wrong user.
3. DISCARDING AN UNMANAGED LOGIN. If the live credential belongs to no managed
   slot, overwriting it destroys a login this tool never saved. That refuses
   rather than proceeds, because the user can always opt in with ``force``, and
   cannot un-destroy a credential.

And the switch is never silently complete: Codex caches auth in-process, so any
running ``codex`` keeps the old account until it restarts. The result says so.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from claude_swap.codex.auth_file import has_live_login, read_auth, write_auth
from claude_swap.codex.identity import CodexIdentity, identity_from_auth
from claude_swap.codex.processes import CodexProcess, running_codex_processes
from claude_swap.codex.stats import (
    CodexProfileStats,
    CodexResetCredits,
    fetch_profile_stats,
    fetch_reset_credits,
)
from claude_swap.codex.store import CodexAccount, CodexAccountStore
from claude_swap.codex.tokens import (
    TokenRefreshError,
    access_token_expiry,
    needs_refresh,
    refresh_tokens,
)
from claude_swap.codex.usage import CodexUsage, UsageAuthError, UsageError, fetch_usage
from claude_swap.exceptions import AccountNotFoundError, SwitchError, ValidationError

_logger = logging.getLogger("claude-swap")


def _live_login_is_older(saved: dict, live: dict) -> bool:
    refreshed = []
    for credentials in (saved, live):
        try:
            at = datetime.fromisoformat(credentials.get("last_refresh", ""))
        except (TypeError, ValueError):
            at = None
        refreshed.append(at if at is not None and at.tzinfo is not None else None)
    saved_at, live_at = refreshed
    if saved_at is not None and live_at is not None:
        return saved_at > live_at
    saved_expiry = access_token_expiry(saved.get("tokens") or {})
    live_expiry = access_token_expiry(live.get("tokens") or {})
    if (
        saved_expiry is not None and live_expiry is not None
        and math.isfinite(saved_expiry) and math.isfinite(live_expiry)
        and saved_expiry != live_expiry
    ):
        return saved_expiry > live_expiry
    return saved_at is not None and live_at is None and saved.get("tokens") != live.get("tokens")


@dataclass(frozen=True)
class CodexStatus:
    """What Codex is currently logged in as, and whether we manage it."""

    logged_in: bool
    identity: CodexIdentity | None
    account: CodexAccount | None  # the managed slot, when the live login is one
    active_number: str | None

    @property
    def is_managed(self) -> bool:
        return self.account is not None


@dataclass(frozen=True)
class SwitchResult:
    """Outcome of a switch, including what the user must still do."""

    account: CodexAccount
    previous: CodexAccount | None
    synced_back: bool
    processes: tuple[CodexProcess, ...] = field(default_factory=tuple)

    @property
    def restart_required(self) -> bool:
        """Whether a running Codex is still serving the OLD account.

        Always true when something is running: Codex reads ``auth.json`` once at
        startup and its 401-recovery reload refuses to cross account ids, so
        nothing short of a restart moves a live process to the new account.
        """
        return bool(self.processes)


class CodexSwitcher:
    """Add, list, switch and remove managed Codex accounts."""

    def __init__(self, store: CodexAccountStore | None = None) -> None:
        self.store = store if store is not None else CodexAccountStore()

    # -- inspection ------------------------------------------------------

    def status(self) -> CodexStatus:
        """What Codex is logged in as right now."""
        live = read_auth()
        if not has_live_login(live):
            return CodexStatus(False, None, None, None)
        identity = identity_from_auth(live)
        account = (
            self.store.find_by_account_id(identity.account_id) if identity else None
        )
        return CodexStatus(True, identity, account, account.number if account else None)

    def list_accounts(self) -> list[CodexAccount]:
        return list(self.store.accounts().values())

    def resolve(self, identifier: str) -> CodexAccount:
        """Find an account by slot number, alias, or email.

        Raises:
            AccountNotFoundError: Nothing matches ``identifier``.
        """
        wanted = str(identifier).strip()
        accounts = self.store.accounts()
        if wanted in accounts:
            return accounts[wanted]
        lowered = wanted.lower()
        for account in accounts.values():
            if account.alias and account.alias.lower() == lowered:
                return account
        for account in accounts.values():
            if account.email and account.email.lower() == lowered:
                return account
        raise AccountNotFoundError(f"no managed Codex account matches '{identifier}'")

    # -- mutation --------------------------------------------------------

    def add_current(self, *, alias: str = "", refresh_existing: bool = False) -> CodexAccount:
        """Capture the CURRENT Codex login into a new slot.

        Raises:
            ValidationError: Nothing is logged in, or the login is an API key
                (no id_token, so no derivable account identity).
            SwitchError: This account already occupies a slot and
                ``refresh_existing`` was not requested.
        """
        with self.store.lock():
            live = read_auth()
            if not has_live_login(live):
                raise ValidationError(
                    "Codex is not logged in — run 'codex login' first."
                )
            identity = identity_from_auth(live)
            if identity is None:
                raise ValidationError(
                    "This Codex login carries no id_token (an API-key login), so "
                    "its account cannot be identified. API-key accounts are not "
                    "managed yet."
                )
            existing = self.store.find_by_account_id(identity.account_id)
            if existing is not None:
                if refresh_existing:
                    updated = replace(
                        existing, email=identity.email or existing.email,
                        plan=identity.plan or existing.plan,
                    )
                    self.store.write_credentials(existing.number, live)
                    self.store.update(updated)
                    self.store.set_active(existing.number)
                    return updated
                raise SwitchError(
                    f"{identity.display_label} is already managed as slot "
                    f"{existing.number}."
                )
            account = self.store.add(identity, live, alias=alias)
            # The credential we just captured IS the live one, so this slot is
            # active by construction -- recording anything else would make the
            # very next switch sync-back into the wrong slot.
            self.store.set_active(account.number)
            return account

    def switch_to(self, identifier: str, *, force: bool = False) -> SwitchResult:
        """Make a managed account the live Codex login.

        Args:
            force: Proceed even when the current login belongs to no managed
                slot, discarding it.

        Raises:
            AccountNotFoundError: No such managed account.
            SwitchError: The target has no saved credential, is already active,
                or the live login would be discarded without ``force``.
        """
        with self.store.lock():
            target = self.resolve(identifier)
            target_credentials = self.store.read_credentials(target.number)
            if target_credentials is None:
                raise SwitchError(
                    f"Slot {target.number} ({target.display_label}) has no saved "
                    "credential. Log in as that account and re-add it."
                )
            if not has_live_login(target_credentials) or not target.identity.matches(
                identity_from_auth(target_credentials)
            ):
                raise SwitchError(
                    f"Slot {target.number} has an invalid or mismatched saved login. "
                    "Sign in to that Codex account and add the existing login again."
                )

            live = read_auth()
            live_identity = identity_from_auth(live) if has_live_login(live) else None
            previous = (
                self.store.find_by_account_id(live_identity.account_id)
                if live_identity
                else None
            )

            if live_identity is not None and previous is None and not force:
                raise SwitchError(
                    f"The current Codex login ({live_identity.display_label}) is "
                    "not managed, and switching would discard it. Add it first, "
                    "or pass force to overwrite it."
                )

            if live_identity is not None and previous is not None:
                if previous.number == target.number:
                    raise SwitchError(
                        f"{target.display_label} is already the active Codex account."
                    )
                previous_credentials = self.store.read_credentials(previous.number)
                synced_back = not _live_login_is_older(previous_credentials or {}, live)
                if synced_back:
                    self.store.write_credentials(previous.number, live)
            else:
                synced_back = False

            target_tokens = target_credentials.get("tokens") or {}
            if (
                access_token_expiry(target_tokens) is not None
                and needs_refresh(target_tokens, now=time.time())
            ):
                if not self._refresh_is_safe(target):
                    raise SwitchError("Quit Codex before refreshing this account and switching.")
                try:
                    target_credentials = self._refresh_and_persist(target, target_credentials)
                except UsageError as error:
                    raise SwitchError(
                        f"Cannot switch; your current login was not replaced. {error}"
                    ) from error
            write_auth(target_credentials)
            self.store.set_active(target.number)

        processes = tuple(running_codex_processes())
        if processes:
            _logger.info(
                "Codex switch to slot %s needs a restart: %d process(es) still "
                "hold the previous account in memory",
                target.number,
                len(processes),
            )
        return SwitchResult(
            account=target,
            previous=previous,
            synced_back=synced_back,
            processes=processes,
        )

    def remove_account(self, identifier: str) -> CodexAccount:
        """Forget a managed account and delete its saved credential."""
        with self.store.lock():
            target = self.resolve(identifier)
            removed = self.store.remove(target.number)
            if removed is None:  # pragma: no cover - resolve just found it
                raise AccountNotFoundError(f"slot {target.number} vanished")
            return removed

    def set_alias(self, identifier: str, alias: str) -> CodexAccount:
        """Give an account a memorable name."""
        from claude_swap.models import normalize_alias

        normalized = normalize_alias(alias) if alias else ""
        with self.store.lock():
            target = self.resolve(identifier)
            for other in self.store.accounts().values():
                if other.number != target.number and other.alias == normalized and normalized:
                    raise ValidationError(
                        f"alias '{normalized}' already belongs to slot {other.number}"
                    )
            updated = CodexAccount(
                number=target.number,
                email=target.email,
                account_id=target.account_id,
                plan=target.plan,
                added=target.added,
                alias=normalized,
                disabled=target.disabled,
            )
            self.store.update(updated)
            return updated

    def set_account_disabled(self, identifier: str, disabled: bool) -> CodexAccount:
        """Change automatic-selection eligibility without touching credentials."""
        if type(disabled) is not bool:
            raise ValidationError("disabled must be a boolean")
        with self.store.lock():
            target = self.resolve(identifier)
            updated = replace(target, disabled=disabled)
            self.store.update(updated)
            return updated

    # -- quota -----------------------------------------------------------

    def _live_account_id(self) -> str | None:
        """The account id of the credential Codex is currently holding."""
        live = read_auth()
        identity = identity_from_auth(live) if has_live_login(live) else None
        return identity.account_id if identity else None

    def _refresh_is_safe(self, account: CodexAccount) -> bool:
        """Whether this slot's token can be refreshed without racing Codex.

        A refresh ROTATES the refresh token, and Codex's own AuthManager
        refreshes on 401. Two refreshes on one account means one rotates a
        token the other just invalidated, and there is no lock to coordinate
        with -- so a slot that a running ``codex`` is currently using is simply
        never refreshed here. Idle slots are unaffected, which is the case that
        matters: they are the ones whose tokens go stale.
        """
        if not running_codex_processes():
            return True
        return self._live_account_id() != account.account_id

    def _call_with_tokens(self, account: CodexAccount, fetcher, *, allow_refresh: bool):
        """Run ``fetcher(tokens)`` with a valid token for ``account``.

        One place for the refresh protocol, because every reader needs it and
        three copies would drift: check expiry first so a healthy token is never
        rotated for nothing, refresh only when no running Codex is using this
        account, persist before use, and allow EXACTLY one refresh-and-retry on
        a rejection -- never a loop, because each attempt rotates the token.

        Raises:
            SwitchError: The slot has no saved credential.
            UsageError: The call failed, including a refresh that could not be
                completed.
        """
        refreshed = False
        with self.store.lock():
            credentials = self._credentials_with_live_updates(account)
            tokens = credentials.get("tokens") or {}
            if (
                allow_refresh
                and needs_refresh(tokens, now=time.time())
                and self._refresh_is_safe(account)
            ):
                credentials = self._refresh_and_persist(account, credentials)
                tokens = credentials["tokens"]
                refreshed = True

        try:
            return fetcher(tokens)
        except UsageAuthError:
            with self.store.lock():
                latest = self._credentials_with_live_updates(account)
                if latest.get("tokens") != tokens:
                    credentials = latest
                elif refreshed or not allow_refresh or not self._refresh_is_safe(account):
                    raise
                else:
                    credentials = self._refresh_and_persist(account, latest)
            return fetcher(credentials.get("tokens") or {})

    def _credentials_with_live_updates(self, account: CodexAccount) -> dict:
        """Reconcile a saved login while the caller holds the store lock."""
        current = self.resolve(account.number)
        if current.account_id != account.account_id:
            raise SwitchError("This saved Codex account changed. Refresh the account list and try again.")
        credentials = self.store.read_credentials(account.number)
        if credentials is None:
            raise SwitchError(
                f"Slot {account.number} ({account.display_label}) has no saved "
                "credential."
            )
        live = read_auth()
        if has_live_login(live) and account.identity.matches(identity_from_auth(live)):
            if _live_login_is_older(credentials, live):
                return credentials
            if live != credentials:
                self.store.write_credentials(account.number, live)
            credentials = live
        return credentials

    def usage_for(self, identifier: str, *, allow_refresh: bool = True) -> CodexUsage:
        """Read one managed account's quota.

        Raises:
            AccountNotFoundError: No such managed account.
            SwitchError: The slot has no saved credential.
            UsageError: The quota could not be read.
        """
        account = self.resolve(identifier)
        return self._call_with_tokens(account, fetch_usage, allow_refresh=allow_refresh)

    def stats_for(self, identifier: str, *, allow_refresh: bool = True) -> CodexProfileStats:
        """Read one managed account's lifetime statistics. Display only."""
        account = self.resolve(identifier)
        return self._call_with_tokens(
            account, fetch_profile_stats, allow_refresh=allow_refresh
        )

    def reset_credits_for(
        self, identifier: str, *, allow_refresh: bool = True
    ) -> CodexResetCredits:
        """Read the credits that can clear an exhausted window early.

        NOT the same as the billing credits in the quota response: those let
        requests continue past an exhausted window and are charged per request,
        while these clear the window itself.
        """
        account = self.resolve(identifier)
        return self._call_with_tokens(
            account, fetch_reset_credits, allow_refresh=allow_refresh
        )

    def _refresh_and_persist(
        self, account: CodexAccount, credentials: dict
    ) -> dict:
        """Rotate and persist credentials while the caller holds the store lock.

        Persisting first is the whole point: the previous refresh token is dead
        as soon as the new one is issued, so anything that happens between the
        refresh and the write costs the account.
        """
        tokens = credentials.get("tokens") or {}
        try:
            rotated = refresh_tokens(tokens)
        except TokenRefreshError as e:
            raise UsageError(f"{account.display_label}: {e}") from e
        updated = dict(credentials)
        updated["tokens"] = rotated
        updated["last_refresh"] = datetime.now(UTC).isoformat()
        self.store.write_credentials(account.number, updated)
        live = read_auth()
        if (
            account.identity.matches(identity_from_auth(live))
            and live.get("tokens") == tokens
        ):
            write_auth(updated)
        _logger.info("Refreshed Codex tokens for slot %s", account.number)
        return updated

    def usage_all(self) -> dict[str, CodexUsage | Exception]:
        """Quota for every managed account, keyed by slot.

        Failures are RETURNED rather than raised: one dead account must not
        hide the quota of every healthy one, which is the whole point of
        looking at them together.
        """
        results: dict[str, CodexUsage | Exception] = {}
        for number in self.store.accounts():
            try:
                results[number] = self.usage_for(number)
            except Exception as e:  # noqa: BLE001 - reported per account
                results[number] = e
        return results
