"""Codex CLI account switching.

Parallel to the Claude Code support in the rest of this package, not layered on
it: the two providers share almost no mechanism. Claude Code re-reads its
credential file whenever it changes, so a swap lands on a running session; Codex
caches auth in-process and its only reload path refuses to cross account ids
(``AuthManager`` in codex-rs/login/src/auth/manager.rs), so a Codex switch is
only visible to the NEXT ``codex`` process. That difference is why this lives
beside the Claude switcher rather than behind a shared abstraction.

What the provider has to be careful about, none of which the Claude side faces:

- Codex offers no advisory lock to cooperate with. The mandatory restart is what
  makes that survivable: with no ``codex`` running there is no in-process token
  refresher to race, so process detection substitutes for the missing lock.
- Codex's own ``auth.json`` writer truncates in place (``FileAuthStorage::save``)
  with no temp+rename, so a reader can catch a torn file and a write interrupted
  on a soft-mounted network home can truncate it. We always write atomically and
  tolerate torn reads.
- ``auth.json`` carries fields this tool does not model (``auth_mode`` today).
  Every unknown key is round-tripped verbatim -- see ``auth_file``.
"""
