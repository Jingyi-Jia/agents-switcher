# Agent Switch

A local account manager for **Claude Code and Codex**. Save existing logins,
see quota, and choose which account to use from a desktop window, browser
dashboard, or terminal. Automatic switching is optional.

The app and browser dashboard also offer **experimental Claude Desktop profiles**
on macOS and Linux. These are separate from Claude Code accounts and automation.

The executable is **`agent-switch`**; the Python package is `agents-switcher`.
This fork does not install or replace upstream's `cswap` command.

## What it switches

| Provider | Supported today | Boundary |
| --- | --- | --- |
| Claude Code | Saved CLI logins, setup tokens and managed API keys; manual and quota-based switching | **Not Claude Desktop**, including its Code tab. Desktop has a separate sign-in; restarting it does not transfer a CLI login. |
| Codex | File-backed ChatGPT/OAuth accounts, quota, activity stats and automatic selection | Supports Codex CLI and Desktop when they use the same `auth.json`, not keyring-only or API-key logins. Quit both before switching; running processes keep their old account until restarted. |
| Claude Desktop profiles | Experimental, manual launcher for separate local profiles on macOS/Linux | Sign in inside each profile. Mac account persistence and Code/Cowork behavior are unverified; custom profiles disable local Claude-in-Chrome pairing. No quota-based switching or Windows support. |

Agent Switch does not install the provider CLIs or sign you in. Install
[Claude Code](https://code.claude.com/docs/en/setup) or
[Codex CLI](https://developers.openai.com/codex/cli), then sign in through that
provider. **Add existing login** in Agent Switch saves the current provider login;
it is not a login button. Claude API-key accounts have no subscription quota
readout and can incur per-token charges.

## Get the app

**There is no published installer release yet.** The repository currently has
only a draft `v1.0.0` release with no assets. The
[Releases page](https://github.com/jingyi-jia/agents-switcher/releases) is where
future release downloads will appear; a version in package metadata is not a
download announcement.

For developers, successful runs of
[Desktop installers](https://github.com/jingyi-jia/agents-switcher/actions/workflows/desktop.yml)
provide preview artifacts for macOS arm64/x64, Windows x64 and Linux x64.
Artifacts expire after **14 days**. macOS previews are **ad-hoc signed, not
Developer ID signed or notarized**; Windows and Linux previews are unsigned.
Gatekeeper or SmartScreen may block them. Do not disable OS protections to make
a preview run. See the [desktop guide](desktop/README.md) for preview handling,
platform requirements and native build instructions.

The standalone app bundles Electron and its Python backend: end users do not
need Python, uv, Node.js or npm, but still need their provider CLI and login.
The experimental Claude Desktop panel needs the official Claude Desktop app
instead of a CLI installation.
Public macOS/Windows release builds require signing credentials, and macOS also
requires notarization. The workflow exists; a trusted signed release still needs
successful builds and clean-machine installation checks. There is no auto-updater.

## Install the CLI from source

Use Python 3.12+ and [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/jingyi-jia/agents-switcher.git
cd agents-switcher
uv tool install .
agent-switch --help
```

Alternatively, use `uv sync --locked` in the checkout and prefix commands with
`uv run agent-switch`. This is a source installation, not a promise of a published
PyPI package or signed desktop download.

Choose an interface:

```bash
agent-switch tui          # terminal provider chooser
agent-switch claude tui   # Claude Code accounts
agent-switch codex tui    # Codex accounts
agent-switch web          # local browser dashboard; Ctrl-C stops its server
agent-switch web --no-open
```

`agent-switch app install` creates a shortcut that runs `agent-switch web`;
it is **not** the standalone Electron app and still needs the CLI installation.
`agent-switch app uninstall` removes that shortcut. `agent-switch tray` provides
a menu-bar/system-tray readout when the optional platform dependency is installed
(`uv tool install '.[tray-macos]'` on macOS, `uv tool install '.[tray]'` elsewhere).

## Save and switch Claude Code accounts

Sign in to Claude Code, then save that login. Repeat after signing in to each
additional account through Claude Code.

```bash
agent-switch claude add --alias work
agent-switch claude list
agent-switch claude status
agent-switch claude switch 2
agent-switch claude switch --strategy best
```

Unqualified commands such as `agent-switch list` and `agent-switch switch 2`
are Claude Code shortcuts. `disable 2` excludes a saved account from automatic
selection; `enable 2` restores eligibility. Neither deletes the saved login.

For setup tokens or managed API keys, use `agent-switch add-token` and its hidden
prompt rather than putting a secret in shell history. Experimental
`agent-switch run 2 -- --resume` launches a Claude Code account in a separate
terminal profile; it does not support API-key accounts and is not Desktop
profile switching. See `agent-switch run --help` for isolation and sharing options.

## Try Claude Desktop profiles (experimental)

Use the **Claude Desktop — Experimental profiles** panel in the app or browser
dashboard. It is not the Claude Code account list, and **Add existing login** does
not import a Desktop session.

1. Install official Claude Desktop in `/Applications/Claude.app` or
   `~/Applications/Claude.app` on macOS, or its official Linux package at
   `/usr/bin/claude-desktop`. Custom install locations and Windows are not supported.
2. Choose **Create empty profile**, give it a label such as “Work”, and acknowledge
   the experimental limitations. This only creates empty private directories;
   it works even if Claude is not detected or its process status is unknown.
   Those checks block opening profiles, not creating them.
3. Fully **quit Claude Desktop**. Closing its window may leave it running. Choose
   **Check again**, then **Open** beside the profile and confirm the launch.
4. Sign in directly inside Claude. Repeat with another empty profile for another
   account. To return to a saved profile, quit Claude first and open that profile
   from Agent Switch. **Verify the selected account inside Claude before working**;
   labels are yours, not identities checked by Agent Switch.
5. To use your original profile again, quit Claude and choose **Open usual Claude
   (default)**. Launching Claude normally from the Dock also uses its usual profile,
   not the last named profile chosen here.

This uses Claude's `--user-data-dir` launch flag without copying session cookies,
importing tokens, changing CLI credentials, modifying the official app, or
force-quitting it. The feature only confirms that a launch was requested, not
successful authentication. Account sign-in, expiry and refresh remain Claude's
responsibility. Opening profiles does not provide quota data or auto-switching.

**Known limitation:** the inspected Mac build disables local pairing with
**Claude in Chrome** for relocated profiles. Browser tools using that connection
can be unavailable. This is not evidence that all Code/Cowork features fail, but
their signed-in behavior and Mac account persistence have not been verified.
The completed official-app test used **Claude Desktop 2.7032.0 on Linux**, empty
profiles and A → B → A restarts; it verified separate data and saved window settings,
not real-account switching. Treat updates to Claude as requiring revalidation.

Profile labels and IDs are stored in `claude-desktop/profiles.json` beneath Agent
Switch's data directory; each `profiles/<id>/` contains private `desktop/` and
`claude-code/` directories. Claude owns the session data it creates there, which
can contain credentials and conversation data. Do not share or commit these
directories. No delete or export action is provided. Launching Claude can also
update its shared OS-integration metadata outside a named profile.

## Save and switch Codex accounts safely

Sign in with `codex login`, then save the login:

```bash
agent-switch codex add --alias work
agent-switch codex list
agent-switch codex status
agent-switch codex usage
agent-switch codex stats work
```

For every switch:

1. **Quit Codex completely**, including its desktop app and terminal sessions.
2. Run `agent-switch codex switch work` (or select a saved account in Agent Switch).
3. Reopen Codex and verify the account before continuing work.

Codex holds credentials in memory and rotates refresh tokens. Switching the file
does not move an already-running client to another account. The app/dashboard/TUI
refuses a Codex switch while detected processes are running; the CLI can still
switch and print a restart warning. Follow the quit-first sequence even when a
CLI switch succeeds. Do not use `--force` as a repair tool: it permits discarding
an unmanaged current login.

### If a login is already revoked

A revoked, expired or reused refresh token cannot be repaired by switching back
and forth. Stop auto-switching and quit Codex, then:

1. Run `codex login` and sign in as the **affected account**.
2. In Agent Switch, choose **Add existing login** (browser dashboard: **Add current**).
   This replaces that account's saved credentials while preserving its slot.
3. Refresh the account view, then reopen Codex and verify the login.

The CLI's `agent-switch codex add` currently rejects an already-managed account;
it is not the same recovery action as the UI button. Avoid deleting saved
accounts or copying old `auth.json` files around to fix a revoked token. For a
Claude Code re-login warning, sign in again through Claude Code and re-add that
current login; signing in to Claude Desktop will not repair CLI credentials.

### If usage fails with a certificate error

`CERTIFICATE_VERIFY_FAILED` is a TLS trust failure, not evidence that a login was
revoked. Update Agent Switch before deleting or re-adding accounts. The desktop
backend and CLI both use the operating system's certificate trust store, with
certificate and hostname verification enabled. If the error persists, check the
system clock and any organization-managed HTTPS proxy with your administrator;
do not disable certificate verification.

## Automatic switching: opt in, then keep it running

Start by observing decisions:

```bash
agent-switch auto --once --dry-run
agent-switch codex auto --once --dry-run
```

Remove `--once` for a foreground loop; remove `--dry-run` to allow switching.
Use Ctrl-C to stop. `agent-switch config` shows persistent settings, and
`agent-switch config set autoswitch.threshold 80` changes the shared threshold.
Explicit CLI flags override defaults; see each provider's `auto --help`.

**Dry-run means no account switch, not no side effects.** Usage collection can
contact providers, update local caches and safely refresh credentials. It is not
an offline test mode or a substitute for a disposable test account store.

Dashboard/TUI auto modes and threshold overrides belong to that session and are
not saved as rules. Closing a browser tab or pausing its view does not stop the
server or automation. Stop it in the UI or stop its server. Closing the standalone
app's only window quits its backend and session automation; exiting the TUI does
the same for its session. None of these stops independently launched CLI loops.

Codex auto-switch waits while Codex processes are running and never selects an
account that needs paid billing credits. Claude API-key accounts are excluded by
default; the explicit `--include-api-key-accounts` option permits a metered fallback.
Quota failures and exhausted accounts can block selection; auto-switching is not
a guarantee of uninterrupted work or a way around provider limits.

## JSON output for scripting

```bash
agent-switch claude list --json
agent-switch claude status --json
agent-switch codex list --json
agent-switch codex status --json
agent-switch codex usage --json
agent-switch config list --json
```

Claude list/status/switch payloads use `schemaVersion: 1`; Codex has its own
payload shapes. Claude `auto --json` emits JSON events, while Codex auto emits
decisions. For `auto --once`, exit codes are 0 for a switch decision (including
dry-run), 1 for an error, 2 for no change, and 3 for blocked/no viable target
(including Codex waiting for a restart). See [AGENTS.md](AGENTS.md) for source refs.

## Local data and privacy

Saved accounts use `~/.local/share/claude-swap` on Linux/WSL (or
`$XDG_DATA_HOME/claude-swap`), and `~/.claude-swap-backup` on macOS/Windows.
Codex backups occupy a separate `codex/` subdirectory. The historical names remain
for compatibility; do not assume this fork's data is isolated from upstream.

`CLAUDE_CONFIG_DIR` and `CODEX_HOME` affect which CLI profile is read and switched.
macOS Claude credentials can use Keychain; file backups are permission-restricted
but base64 encoding is **not encryption**. Treat backups and exports as secrets.
Do not attach tokens, auth files or full stores to bug reports.

The browser dashboard binds to loopback by default and uses a per-run access
token. Keep its URL private and use SSH forwarding for remote access instead of
exposing the server publicly. Quota and stats requests still contact providers.

## Development and credit

[AGENTS.md](AGENTS.md) covers the repository map, safe tests and contribution
checks; [desktop/README.md](desktop/README.md) covers packaging and signing.
Report this fork's bugs in [its issue tracker](https://github.com/jingyi-jia/agents-switcher/issues).

Agent Switch is a fork of [claude-swap](https://github.com/realiti4/claude-swap)
by **Onur Cetinkol (@realiti4)**. The inherited Claude Code switching is his work.
This repo focuses on shared Claude Code/Codex account management and a standalone
desktop app. Original history and attribution are retained.
Licensed under [MIT](LICENSE).
