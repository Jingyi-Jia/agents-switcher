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
from dataclasses import dataclass, field

from claude_swap.codex.auth_file import has_live_login, read_auth, write_auth
from claude_swap.codex.identity import CodexIdentity, identity_from_auth
from claude_swap.codex.processes import CodexProcess, running_codex_processes
from claude_swap.codex.store import CodexAccount, CodexAccountStore
from claude_swap.exceptions import AccountNotFoundError, SwitchError, ValidationError

_logger = logging.getLogger("claude-swap")


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
            return CodexStatus(False, None, None, self.store.active_number())
        identity = identity_from_auth(live)
        account = (
            self.store.find_by_account_id(identity.account_id) if identity else None
        )
        return CodexStatus(True, identity, account, self.store.active_number())

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

    def add_current(self, *, alias: str = "") -> CodexAccount:
        """Capture the CURRENT Codex login into a new slot.

        Raises:
            ValidationError: Nothing is logged in, or the login is an API key
                (no id_token, so no derivable account identity).
            SwitchError: This account already occupies a slot.
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
                # Rotation capture: the live tokens supersede whatever was saved
                # when this slot was added. Guarded by identity, which is what
                # stops one account's tokens landing in another's slot.
                self.store.write_credentials(previous.number, live)
                synced_back = True
            else:
                synced_back = False

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
