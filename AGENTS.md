# Working on Agent Switch

This is the contributor and coding-agent guide. The [README](README.md) is the
user guide; [desktop/README.md](desktop/README.md) is the native packaging guide.
Read the implementation and nearby tests before changing a provider's behavior.

## Project identity and scope

- Distribution: `agents-switcher`; executable: `agent-switch`; import package:
  `claude_swap`. Those names intentionally differ. Do not restore upstream's
  `cswap`/`claude-swap` entry points or silently rename persisted data paths.
- Python 3.12+ with Hatchling; dependencies and pytest configuration live in
  [pyproject.toml](pyproject.toml), with versions locked in [uv.lock](uv.lock).
- The current providers are Claude Code and file-backed Codex accounts. Codex CLI
  and Desktop are supported when they use the same `auth.json`; keyring-only and
  API-key logins are not. Quit both Codex clients before switching.
  Claude Desktop, including its Code tab, has a separate sign-in and is not
  switched by the CLI-account actions. The app/dashboard's experimental Desktop
  profile launcher is independent; it launches empty or saved local profiles,
  never imports CLI credentials or confirms a signed-in identity. Keep that boundary consistent with
  [client_support.py](src/claude_swap/client_support.py).
- The standalone Electron shell bundles a frozen backend, not provider CLIs.
  `agent-switch app install` is a browser-launcher shortcut, not that app.

## Repository map

| Area | Sources and responsibility |
| --- | --- |
| CLI | [cli.py](src/claude_swap/cli.py), [__main__.py](src/claude_swap/__main__.py): entry point, provider dispatch, Claude commands and settings. |
| Claude accounts | [switcher.py](src/claude_swap/switcher.py), [credentials.py](src/claude_swap/credentials.py), [oauth.py](src/claude_swap/oauth.py), [macos_keychain.py](src/claude_swap/macos_keychain.py): identities, capture, activation and refresh. |
| Paths and persistence | [paths.py](src/claude_swap/paths.py), [migrations.py](src/claude_swap/migrations.py), [dirlock.py](src/claude_swap/dirlock.py), [locking.py](src/claude_swap/locking.py), [fsutil.py](src/claude_swap/fsutil.py): platform paths, migrations, locks and atomic replacement. |
| Claude automation | [autoswitch.py](src/claude_swap/autoswitch.py), [settings.py](src/claude_swap/settings.py), [usage_store.py](src/claude_swap/usage_store.py), [poll_policy.py](src/claude_swap/poll_policy.py): decisions, settings, freshness and polling. |
| Claude sessions | [session.py](src/claude_swap/session.py), [mappings.py](src/claude_swap/mappings.py): experimental per-terminal profiles and directory mappings. |
| Codex | [codex/](src/claude_swap/codex/): separate CLI, roster, auth-file handling, identity, token refresh, process detection, quota/stats and automation. |
| Shared UI actions | [providers.py](src/claude_swap/providers.py): provider capabilities, serialized actions and session-owned automation. |
| Terminal UI | [tui/](src/claude_swap/tui/): Textual provider chooser, account dashboards and modals. |
| Browser and tray | [web/server.py](src/claude_swap/web/server.py), [web/page.py](src/claude_swap/web/page.py), [web/cli.py](src/claude_swap/web/cli.py), [web/tray.py](src/claude_swap/web/tray.py), [web/launcher.py](src/claude_swap/web/launcher.py). |
| Usage analytics and preferences | [analytics.py](src/claude_swap/analytics.py): account-specific Codex reports and local Claude stats-cache projection; [web/preferences.py](src/claude_swap/web/preferences.py): private appearance and versioned profile-notice acknowledgement. |
| Codex app assistance | [codex/desktop.py](src/claude_swap/codex/desktop.py): strict process readiness and explicitly requested macOS normal quit/reopen. Never writes credentials or kills terminal processes. |
| Standalone app | [desktop.py](src/claude_swap/desktop.py): private backend protocol; [desktop/src/](desktop/src/): Electron lifecycle and security; [desktop/scripts/](desktop/scripts/): freezing and smoke tests. |
| Claude Desktop profiles | [claude_desktop.py](src/claude_swap/claude_desktop.py): experimental macOS/Linux launcher, private label registry, process interlock and profile directories. Separate from provider accounts and automatic switching. |
| Tests and CI | [tests/](tests/), [desktop/test/](desktop/test/), [.github/workflows/](.github/workflows/). |

## Supported command and JSON entry points

The installed command routes to `claude_swap.cli:main`. Use
`uv run agent-switch --help` from the checkout. Some inherited help text still
says `cswap`; that is not this distribution's executable name. A successful
`COMMAND --help` alone does not prove `COMMAND` exists: the main parser handles
help before rejecting unknown arguments. Check dispatch and tests as well.

| Interface | Contract and source |
| --- | --- |
| `agent-switch claude list/status/switch` | Explicit Claude route; unqualified account commands remain shortcuts. `list`, `status`, `switch`, and `switch TARGET` support `--json`. Shapes and errors use `schemaVersion: 1`: [cli.py](src/claude_swap/cli.py), [json_output.py](src/claude_swap/json_output.py), [test_json_output.py](tests/test_json_output.py). |
| `agent-switch claude add`, `add-token`, `remove`, `disable`, `enable`, `alias`, `swap`, `move` | Claude account management. Do not assume every command accepts `--json`. `add-token` supports hidden input or stdin (`-`); never embed real secrets in examples. [cli.py](src/claude_swap/cli.py). |
| `agent-switch run`, `map`, `unmap` | Experimental Claude terminal sessions and directory mappings, not Desktop profiles. `run TARGET -- ...` forwards arguments to Claude; no API-key session support. [session.py](src/claude_swap/session.py). |
| `agent-switch codex status/list/add/switch/remove/enable/disable/alias/usage/stats` | Each accepts `--json`; identifiers are slot, email or alias where supported. Payloads are Codex-specific, not Claude's schema-v1 envelope. Errors are `{"error": "..."}`. [codex/cli.py](src/claude_swap/codex/cli.py), [test_codex_cli.py](tests/test_codex_cli.py). |
| `agent-switch auto --json` | Claude JSON event stream, one event per line. `--once` can emit multiple events. [autoswitch.py](src/claude_swap/autoswitch.py): `AutoSwitchEvent`, `TickOutcome`. |
| `agent-switch codex auto --json` | Codex decision objects; one per iteration, pretty-printed for `--once`. [codex/cli.py](src/claude_swap/codex/cli.py): `_decision_json`, `_AUTO_EXIT`. |
| `agent-switch config list --json`, `config get KEY --json` | Schema-v1 settings output. `set`, `unset` and `path` are supported but not JSON operations. [settings.py](src/claude_swap/settings.py), [test_config_cli.py](tests/test_config_cli.py). |
| `agent-switch tui`, `claude tui`, `codex tui`, `web`, `tray`, `app install/uninstall/status` | Human interfaces, not JSON APIs. Web options and launcher distinction: [web/cli.py](src/claude_swap/web/cli.py). |

Both `auto --once` variants use exit codes 0 for a switch decision, 1 for an
error, 2 for no action/cooldown and 3 for blocked/no target. Dry-run can return 0
without switching; Codex's dry-run JSON can say `switched: true` for the decision.
Do not infer a credential mutation from that field or exit code alone.

For UI work, the existing HTTP routes are implemented in
[web/server.py](src/claude_swap/web/server.py) and tested in
[test_web_actions.py](tests/test_web_actions.py). They are an authenticated local
UI surface, not a promised public SDK. The Electron stdin/stdout protocol in
[desktop.py](src/claude_swap/desktop.py) and
[backend.cjs](desktop/src/backend.cjs) is private; do not expose its token in logs,
command-line arguments or screenshots.

Both CLI and desktop startup initialize native TLS through
[tls.py](src/claude_swap/tls.py) before provider clients or workers run. Preserve
certificate and hostname verification, including when native trust is unavailable.

The experimental Desktop panel reads `claudeDesktop` in `/api/state`. Its private
POST routes are `/api/claude-desktop/create` (`name`, `confirm: true`) and
`/api/claude-desktop/open` (`profileId`, `confirm: true`); `profileId: "default"`
opens the usual Claude profile without a user-data override. `canCreate` is
independent of installation and process detection; creation does not launch.
Launch success never means authenticated or account-switched. There is
no Desktop-profile CLI/TUI command or automatic-switch policy.

The private analytics surface is `GET /api/analytics?provider=codex|claude`, with
optional `force=0|1`. It requires the header token, never query-token auth. Codex
analytics use stable saved-account identities, cached provider reports and
`allow_refresh=False`; reading charts must not rotate OAuth tokens. Claude
analytics read only the configured `stats-cache.json`, never transcripts,
credentials or another profile's cookies. Local history must not be assigned to
the current saved login. Missing buckets and unsupported fields remain unknown,
and reporting data never participates in automatic account selection.

`GET /api/codex/status` is a strict process-readiness check, independent of quota.
The `quit` and `open` POSTs under `/api/codex/` require exact `confirm: true` and
accept no supplied path, executable or PID. The frontend's assisted sequence must
remain cancellable before switching, check for confirmed exit, and reopen only
after the account action succeeds. Never kill a terminal session or force-close
Codex. Unknown process state blocks manual UI switch, switch-best, and the
dashboard's automatic-switch ticks. App quit/open requests share the credential
action lock; analytics use their own lock and never enter that action sequence.

`GET /api/preferences` is a fast header-authenticated read independent of provider
collection. `POST /api/preferences` persists only supported appearance and notice
fields; acknowledging notice version 1 additionally requires `confirm: true`.
Corrupt/unreadable preferences never imply consent. A remembered notice may remove
the repeated checkbox, but never the user's deliberate profile launch action,
server consent boolean, process check, or known Chrome-pairing limitation.

## Setup, tests and builds

From the repository root:

```bash
uv sync --locked
uv run agent-switch --help
uv run pytest -m "not native_process"
uv run pytest -n 0 -m native_process
npm test --prefix desktop
```

`uv sync` installs the default dev group. The Python suite uses pytest-asyncio
and pytest-xdist; parallelism defaults to `-n auto --dist loadgroup`. Run both
test phases: native process probes must run serially after the other tests
have exited, so their real process-table reads cannot race test-created children.
For a small focused run, override parallelism explicitly:

```bash
uv run pytest -n 0 tests/test_codex_switcher.py tests/test_codex_login_recovery.py
uv run pytest -n 0 tests/test_provider_actions.py tests/test_client_support.py
uv build
```

There is no configured repository-wide linter or typechecker. Use the existing
tests, syntax/build checks as appropriate, and `git diff --check`; do not report
an invented lint command as verification. Normal CI runs Python tests on Linux,
Windows and macOS; see [ci.yml](.github/workflows/ci.yml).

For the desktop runtime, use native target OS/architecture builds:

```bash
uv sync --locked --python 3.12 --group desktop-build
npm ci --prefix desktop
uv run --no-sync pytest -m "not native_process" -o faulthandler_timeout=600
uv run --no-sync pytest -n 0 -m native_process -o faulthandler_timeout=600
npm test --prefix desktop
uv run --no-sync python desktop/scripts/build_backend.py
```

On Linux, smoke-test the frozen helper before opening the app:

```bash
uv run --no-sync python desktop/scripts/smoke_backend.py --check-processes
npm start --prefix desktop
```

On macOS/Windows, the smoke test requires a **disposable VM/CI runner** and the
`--disposable-runner` flag. Changing HOME does not isolate the OS credential
store; that flag is an acknowledgement, not a sandbox. Do not use it to bypass
the guard on a personal workstation. Installer commands and signing requirements
are in [desktop/README.md](desktop/README.md) and
[desktop.yml](.github/workflows/desktop.yml). PyInstaller is not a cross-compiler.

## Provider and credential safety invariants

1. **Isolate tests from real accounts.** Use temporary directories and synthetic
   credentials. [tests/conftest.py](tests/conftest.py) provides home isolation,
   in-memory Keychain fakes, OAuth profile stubs and a process-global real-store
   write guard. Preserve those guards; a new transport needs its own mock.
   Never run login, switch, add, purge, export or quota polling against a user's
   live credentials merely to test a change.
2. **Resolve paths centrally.** Claude honors `CLAUDE_CONFIG_DIR` and its secure
   storage override; Codex honors `CODEX_HOME`. Backup paths retain upstream's
   `claude-swap` names, with Codex under a separate `codex/` namespace. Constructors
   and apparently read-only commands may initialize/migrate files or collect
   usage. For ad-hoc checks, isolate HOME, USERPROFILE, XDG directories, provider
   config directories and ambient credentials before invoking the process.
3. **Preserve identity before replacing credentials.** A malformed roster or
   unreadable auth file is not an empty account store. Keep the strict reads,
   bounded retries, identity checks, locked mutations, atomic replacement and
   private file permissions. Do not replace cross-node-safe directory locks with
   process-local or node-local locks. See [dirlock.py](src/claude_swap/dirlock.py),
   [codex/store.py](src/claude_swap/codex/store.py) and
   [codex/auth_file.py](src/claude_swap/codex/auth_file.py).
4. **Codex refresh tokens rotate.** Reconcile the live login to the matching
   account before switching; preserve unknown fields in the whole `auth.json`
   object. Persist rotated credentials before using them and retain the
   running-process refresh guard. Do not blindly retry a rejected refresh token.
   [codex/switcher.py](src/claude_swap/codex/switcher.py) owns this protocol;
   [codex/tokens.py](src/claude_swap/codex/tokens.py) is only its transport.
5. **Codex switching is quit-first, not hot switching.** Shared UI actions refuse
   while Codex processes run; the lower-level CLI reports `restartRequired` if
   they remain. Do not remove that distinction or imply switching the file moves
   a running process. Revoked logins require a fresh provider sign-in. The UI's
   `ProviderActions.add_current` calls `add_current(refresh_existing=True)` to
   refresh a saved slot; the CLI's `codex add` does not. Regression coverage:
   [test_codex_login_recovery.py](tests/test_codex_login_recovery.py).
   Display and selection decisions must use the live login reported by
   `CodexSwitcher.status()`, not the historical store active marker. An unmanaged
   or missing live login is not an active managed account; an unreadable auth
   file is an error, never permission to fall back to the saved marker.
6. **Dry-run is not a credential sandbox.** It prevents account switching, but
   usage collection may refresh tokens or write cache data. Keep automation
   selection based on eligible, sufficiently fresh data, not display-only
   last-good values. Codex automation excludes paid-credit fallback; Claude
   metered API-key fallback requires explicit opt-in. Session UI overrides are
   not saved rules; closing the server/TUI/app stops only its own automation.
7. **Never leak secrets.** File backups use base64, not encryption; exports also
   contain credentials. Avoid tokens in arguments, logs, exception bodies, test
   snapshots or attachments. Keep the dashboard loopback-bound by default and
   retain token/origin checks. Preserve Electron's sandbox, navigation and
   external-link restrictions in [security.cjs](desktop/src/security.cjs).
8. **Desktop profiles remain experimental.** Require explicit consent, reject
   malformed registries and linked profile directories, serialize mutations and
   launches, and fail closed when process detection fails. Never stop Claude,
   copy cookies, inspect its authentication files, spoof vendor test authorization,
   or patch the official app. Use fixed installed executable locations and
   validated stored IDs, not request-supplied paths or commands. Strip inherited
   Claude/API/debugging overrides and give each named profile its own
   `CLAUDE_CONFIG_DIR`. Preserve the local Chrome-pairing warning and distinguish
   Linux empty-profile validation from still-unverified Mac signed-in persistence
   and Code/Cowork behavior. Profile session data is sensitive even though its
   label registry contains no tokens.

## Verification and contribution checklist

- Add a regression test for changed behavior using this repo's fixtures. Useful
  groups are `test_codex_*`, `test_provider_*`, `test_web*`, `test_desktop_*`,
  `test_cli.py`, `test_json_output.py` and `desktop/test/*.test.cjs`. Run the groups
  covering the touched code, then the wider suite for cross-provider changes.
- Check both providers and the affected terminal/browser/desktop surfaces when
  shared actions change. Render UI changes with synthetic accounts; verify empty,
  error and restart-required states without showing real account information.
- Test JSON by parsing it and checking exit codes, not by comparing colored
  terminal text. Account-list and usage commands can make authenticated requests;
  mock transports or use empty disposable stores for documentation checks.
- Desktop profile coverage: [test_claude_desktop.py](tests/test_claude_desktop.py)
  mocks every app launch and checks registry, process, environment and HTTP
  boundaries; [test_claude_desktop_page.py](tests/test_claude_desktop_page.py)
  checks consent and rendering. Do not run native sign-ins as an unattended test.
  [test_claude_desktop_processes.py](tests/test_claude_desktop_processes.py) also
  covers macOS's `<defunct>` zombie marker and signed 32-bit UID formatting
  (`nobody` appears as `-2`). It creates a short-lived nobody-owned process only
  on disposable macOS CI, never on a developer's workstation. Normalize signed
  Mac UIDs before comparing ownership. Skip only confirmed zombies; missing
  commands on live rows must still block launch.
  Packaging CI runs `--check-processes` on the frozen and bundled helper even
  when provider apps are not installed, so installation detection cannot hide a
  scan failure. It checks strict Codex readiness on all three platforms and
  Claude Desktop scanning on macOS/Linux.
- Keep the human [README](README.md), this guide, CLI help and visible notices
  aligned with implemented behavior. Do not advertise Claude Desktop profile
  switching before it exists and has safety tests.
- Preserve upstream attribution and [LICENSE](LICENSE). Keep changes scoped;
  inherited names are not permission for broad compatibility-breaking renames.
- A release workflow is not evidence of a release. Verify actual assets and
  signing results before changing download claims. Preview artifacts last 14
  days; macOS previews are ad-hoc signed and not notarized. Public release jobs
  require real signing credentials, never committed keys. Keep Python and desktop
  versions aligned when preparing a release, and perform clean-machine checks.
