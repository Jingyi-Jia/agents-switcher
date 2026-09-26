# Agent Switch desktop packaging

Agent Switch bundles an Electron window and a PyInstaller-frozen Python backend.
The installer contains the app runtime: users do **not** need Python, uv, Node.js,
or npm. Claude Code and Codex themselves are not bundled. Sign in through Claude
Code's CLI, or through Codex Desktop or CLI with a file-backed login; the dashboard
can then add that current login. The Codex CLI is not required for a supported
Desktop login. Claude Code CLI credentials are separate from Claude Desktop's
login.

## Downloads and installation

Use the assets on the [GitHub Releases page](https://github.com/jingyi-jia/agents-switcher/releases),
when desktop installers are available. Match both your operating system and CPU:

| Platform | Requirement | Download and install |
| --- | --- | --- |
| macOS, Apple Silicon | macOS 13 or newer, M-series Mac | `Agent-Switch-<version>-mac-arm64.dmg`; open it and drag **Agent Switch** to Applications. A `.zip` of the same app is also provided. |
| macOS, Intel | macOS 13 or newer, Intel Mac | `Agent-Switch-<version>-mac-x64.dmg`; open it and drag **Agent Switch** to Applications. |
| Windows | Windows 10 or newer, x64 | `Agent-Switch-<version>-win-x64.exe`; run the installer, then launch **Agent Switch** from Start. Windows ARM is not a native build target. |
| Linux | x64 desktop, Ubuntu 22.04 or newer as the build baseline | Use the `.deb` on Debian/Ubuntu, or the `.AppImage` on compatible distributions. A `.tar.gz` fallback contains the unpacked application. |

On Ubuntu/Debian, install the downloaded `.deb` with your graphical package
installer, or `sudo apt install ./Agent-Switch-<version>-linux-amd64.deb`. For an
AppImage, mark it executable in file properties and open it; AppImage needs the
distribution's FUSE 2 compatibility support. If FUSE is unavailable, prefer the
`.deb` or extract the `.tar.gz` and launch `agent-switch-desktop`. Linux needs a
working graphical session and Chromium's system libraries/sandbox support;
bundling Python does not make the app independent of the OS. Other Linux
distributions are not yet validated.

**Public macOS releases must be Developer ID signed and notarized; public Windows
releases must be code signed.** Signing credentials are not included in this
repository, and configuration alone does not establish release trust. PR and
manual-workflow artifacts are developer previews, not recommended downloads for
nontechnical users. Both macOS architectures use ad-hoc signatures to seal the
modified app bundle; these signatures do not identify an Apple-trusted developer
and are not notarization. Other preview platforms remain unsigned. Gatekeeper or
SmartScreen may still block previews. Do not disable these protections; wait for
a trusted release or build in a disposable development environment. Windows
reputation warnings can still occur for newly signed apps.

Older Mac previews skipped bundle signing and can report that Agent Switch
"is damaged and can't be opened." Use a newer build rather than removing
quarantine attributes or re-signing the downloaded app yourself. CI now verifies
the app's signatures in the build directory, extracted ZIP, and mounted DMG.
For a verified preview from this repository, an unidentified-developer warning
can still require an explicit **System Settings → Privacy & Security → Open
Anyway** approval. Only approve a preview if you trust its source and intend to
test it; see [Apple's guidance](https://support.apple.com/en-us/102445).

### In-app updates

Official stable releases include an updater in **Settings → App updates**.
**Help → Check for Updates** opens Settings and checks the public stable releases
of `Jingyi-Jia/agents-switcher`. Checking, downloading, and installing are separate
user actions: no background checks, automatic downloads, or installation on normal
Quit. Settings shows the running version, available version, download progress,
and safe error messages. Downloads can be cancelled. **Install and restart** asks
for native confirmation, then waits for a successful backend shutdown before
handing control to the installer. A forced or failed shutdown blocks installation.
The new version is confirmed only when the replacement app starts; handing off to
an installer is not proof that an update succeeded.

| Installed format | In-app update support |
| --- | --- |
| macOS, arm64 or x64 | Official Developer ID-signed app in Applications. The updater downloads the matching ZIP; macOS verifies its signature during the explicit install. Apps running from a DMG, ad-hoc previews, and modified bundles are unsupported. |
| Windows, x64 | Signed NSIS installation with the original publisher metadata. Both the updater's publisher verification and an additional fail-closed Authenticode check must pass. Missing/broken verification never permits installation. |
| Linux, x64 | A directly launched, writable `.AppImage` in a writable directory. Symlinked, extracted, read-only, DEB, and tar installations are unsupported; use your package installer or replace the file manually. |
| Development and CI previews | In-app updates are disabled, even if the version number matches a release. Install an official stable release first. |

The updater never selects prereleases or downgrades, accepts no custom feed, and
needs no GitHub token. Downloads use HTTPS and SHA-512 metadata integrity checks;
Linux does not gain macOS/Windows code-signature guarantees. If the latest release
is still building or has no desktop metadata, the check reports an error rather
than claiming the app is current. **Help → Download updates manually** remains
available for unsupported installations and recovery. Versions predating this
updater need one manual installation of an updater-enabled official release.

Quitting the application, including closing its only window, stops its backend and
session-owned auto-switching. It does not stop independently started CLI
automation. Updating never changes accounts or provider credentials; updating or
uninstalling does not remove them.

## Build locally

Build on each target OS and CPU architecture. PyInstaller is not a cross-compiler;
an x64 helper must not be placed in an arm64 Electron app. CI uses Python 3.12,
Node.js 24.18.0, uv 0.12.3, and the locked PyInstaller 6.22.3, Electron 44.4.5,
electron-builder 26.15.3, and electron-updater 6.8.9. The Python `desktop-build` dependency group is
optional and separate from normal runtime dependencies.

From the repository root:

```bash
uv sync --locked --python 3.12 --group desktop-build
npm ci --prefix desktop
uv run --no-sync pytest -m "not native_process" -o faulthandler_timeout=600
uv run --no-sync pytest -n 0 -m native_process -o faulthandler_timeout=600
npm test --prefix desktop
uv run --no-sync python desktop/scripts/build_backend.py
```

On Linux, the frozen-helper smoke test uses isolated file stores:

```bash
uv run --no-sync python desktop/scripts/smoke_backend.py
```

The smoke test uses temporary HOME, XDG, Claude, Codex, Windows app-data and temp
directories, does not inherit provider tokens or Python environment overrides,
and disables Python keyring discovery. It tests the private stdin handshake,
authentication, both provider state collectors with empty accounts, and shutdown
by both a control message and stdin EOF. It never prints the session token.
Add `--check-tls` to also require native certificate trust in the frozen helper
and make credential-free HTTPS HEAD requests to `chatgpt.com`, `auth.openai.com`,
`api.anthropic.com`, and `platform.claude.com`. This opt-in check needs network
access; HTTP denials are acceptable, but TLS verification failures are not.
CI enables it for both the newly frozen helper and the copy inside each app.
Normal app startup does not make these probes. The Python TLS tests separately
verify rejection of untrusted certificates and mismatched hostnames on loopback.
On macOS and Windows, HOME isolation does **not** isolate the system credential
store. Run the test only in a disposable VM/CI runner with
`--disposable-runner`; the script refuses other native runs by default. Do not
use that flag on your everyday account merely to bypass the check.

The helper lives at
`desktop/backend/agent-switch-backend/agent-switch-backend` (`.exe` on Windows),
alongside `_internal/`. Keep the entire directory together. Packaging copies
it to `resources/backend` outside the ASAR, or
`Agent Switch.app/Contents/Resources/backend` on macOS. Only application source,
explicit assets and installed dependencies are frozen; no account directory,
credential store or developer home is a build input.

Run the desktop app with `npm start --prefix desktop` after building the helper.
To build installers, choose exactly one matching native command:

```bash
cd desktop
npm run dist -- --linux --x64
npm run dist -- --mac --arm64
npm run dist -- --mac --x64
npm run dist -- --win --x64
```

`dist` always passes `--publish never`: local packaging cannot publish a release.
The default build also embeds `agentSwitchRelease: false`, so it cannot offer
in-app updates. The public-release workflow enables this capability only after
requiring its signing inputs; do not set it on unsigned or ad-hoc preview builds.
After packaging, verify the native target, for example:

```bash
node scripts/verify-updates.cjs linux x64
```

Use `mac arm64`, `mac x64`, or `win x64` on those native build machines. The
`--release` option verifies that a release build has the enabled capability, and
requires Windows publisher metadata. It does not sign an app or bypass any
signature gate.

Outputs are in `desktop/release/`. A local macOS build can discover your own
signing identity; for a preview without using that identity, run
`CSC_IDENTITY_AUTO_DISCOVERY=false npm run dist -- --mac --arm64 -c.mac.identity=- -c.mac.notarize=false`
(use `--x64` for Intel). Linux packaging needs the normal native build utilities
(`make`, C/C++ toolchain, `binutils`, `dpkg`, `fakeroot` and `rpm` as required by
electron-builder's package tooling), and access to npm/PyPI/GitHub downloads.
On macOS install Xcode Command Line Tools; Windows builds use the tools downloaded
by electron-builder. Use Ubuntu 22.04 for distributable Linux binaries: a helper
built on a newer glibc may not run on the baseline even if Electron does.

## CI and publishing

`.github/workflows/desktop.yml` builds natively on `macos-15` (arm64),
`macos-15-intel` (x64), `windows-2022` (x64), and `ubuntu-22.04` (x64).
It tests the Python suite and Electron shell, builds and smoke-tests the frozen
helper, packages installers, then smoke-tests the helper again from the actual
application resources. macOS jobs also verify bundle signatures in the packaged
app and both distributed formats before uploading. Each job verifies the packaged
version and updater capability, public feed, artifact sizes and SHA-512 hashes,
and external or embedded blockmaps. It uploads only installers, update metadata,
blockmaps, and SHA-256 checksums, not unpacked workspaces or accounts. CI has no display-driven installer test;
clean-machine installation and first-run checks remain a release prerequisite.

PR path changes and **Run workflow** produce preview artifacts in the workflow
run's Artifacts section. Neither path receives signing secrets or release write
permission. The macOS preview step explicitly enables the builder's PR signing
path only for an ad-hoc identity (`-`), with certificate discovery disabled and
certificate inputs removed from the environment. This seals the modified bundle without using a private
key; it does not enable trusted release signing for PRs. The workflow uses
`pull_request`, never `pull_request_target`, and does not check out a different
branch during the build.

A maintainer publishing an existing stable GitHub release triggers the release
build from that tag; the tag must be `v` followed by the desktop package version.
Prerelease publication is not supported by this stable-release workflow.
The workflow never creates tags or releases. All native builds
must pass before a separate, narrowly permissioned job attaches their artifacts
to that existing release. Re-running a release replaces matching artifact names.
Maintainers must review the tagged source and align Python and desktop package
versions before publishing. Published releases are the trusted trigger: restrict
who can create releases and protect release tags.

The update assets are `latest.yml` for Windows, `latest-linux.yml` for AppImage,
and `latest-arm64-mac.yml` / `latest-x64-mac.yml` for macOS. Separate macOS channels
prevent architecture jobs from overwriting the other's metadata. Windows NSIS
and macOS ZIP/DMG artifacts include `.blockmap` companions; the AppImage has an
embedded blockmap. DEB and tar files remain manual downloads. The upload job
attaches installers and blockmaps first, then the verified metadata, so clients
cannot be directed to an artifact that has not been uploaded. Build steps have
no release-write token; the narrowly scoped upload job is the only publisher.

Before enabling a public download, test an actual signed upgrade on disposable
native machines: check and download a higher stable version, cancel a download,
quit with a downloaded update (it must not install), and explicitly install and
restart. Confirm the new version after relaunch, that the old backend exited,
that saved accounts remain unchanged, and that unsigned/wrong-publisher updates
are rejected. Unit tests and a packaging verifier cannot establish those native
installation results without signed artifacts.

### Signing configuration

Add these **GitHub Actions secrets**, never files in the source tree:

| Secret | Purpose |
| --- | --- |
| `APPLE_CERTIFICATE_P12` | Base64-encoded Developer ID Application signing certificate and private key (`.p12`), supplied to electron-builder as `CSC_LINK`. |
| `APPLE_CERTIFICATE_PASSWORD` | Password for that certificate, supplied as `CSC_KEY_PASSWORD`. |
| `APPLE_API_KEY_P8` | App Store Connect team API private key contents for notarization. CI writes a mode-0600 temporary file and deletes it after the step. |
| `APPLE_API_KEY_ID` | ID of the notarization API key. |
| `APPLE_API_ISSUER` | Issuer UUID for that App Store Connect team API key. |
| `WINDOWS_CERTIFICATE_PFX` | Base64-encoded exportable code-signing certificate and private key (`.pfx`), supplied as `CSC_LINK`. |
| `WINDOWS_CERTIFICATE_PASSWORD` | Password for the Windows certificate, supplied as `CSC_KEY_PASSWORD`. |

These secrets are optional for preview builds and required for a published-release
build: missing credentials fail the macOS/Windows release jobs rather than
silently distributing unsigned installers. macOS release builds enable hardened
runtime and notarization, sign the embedded backend, and verify the app signature
and notarization staple. Windows release builds require signing through
electron-builder's supported certificate options and verify Authenticode on the
app, backend and installer. The installed app's generated `app-update.yml` must
contain its certificate-derived `publisherName`; absent metadata disables the
updater rather than bypassing signature verification. Keep the signing identity
compatible across upgrades. Organizations with hardware-
backed certificates should adapt that step to electron-builder's supported Azure
Trusted Signing or custom signing service, rather than trying to export a
non-exportable private key. No Windows cloud-signing tenant is preconfigured.

**A nontechnical public launch is not ready until credentials are provisioned,
all signed native CI builds pass, and the actual downloads are installed and
opened on clean target machines.** This includes verifying the nested helper's
signature/loading on macOS and Windows, provider CLI detection, and all installer
formats. The workflow cannot claim any of those signing results without running
with real authorized credentials.

### Pinned action provenance

Actions are pinned to full commit SHAs, resolved from their official published
release tags: `actions/checkout` v7.0.1, `actions/setup-node` v7.0.0,
`actions/upload-artifact` v7.0.1, `actions/download-artifact` v8.0.1, and
`astral-sh/setup-uv` v10.2.0. Update a SHA only after checking that repository's
release. PyInstaller 6.22.3 was verified against its PyPI release metadata.
Signing options follow the [electron-builder documentation](https://www.electron.build/code-signing.html);
the macOS 13 minimum follows [Electron 44's release notes](https://www.electronjs.org/blog/electron-44-0).
Updater behavior and supported installer formats follow the
[official updater guide](https://www.electron.build/auto-update.html) and
[AppUpdater API](https://www.electron.build/electron-updater.Class.AppUpdater.html).
The app deliberately restricts Linux support to AppImage and disables the
library's automatic download and install-on-quit defaults.

### Private renderer bridge

The sandboxed, context-isolated preload exposes only `window.agentSwitchUpdater`:
zero-argument `getState()`, `check()`, `download()`, `cancel()`, and `install()`
return promises of a state snapshot. `onState(callback)` returns an unsubscribe
function. A snapshot contains `schemaVersion: 1`, `supported`, `status`,
`currentVersion`, `availableVersion`, `message`, and optional `progress`
(`percent`, `transferred`, `total`, `bytesPerSecond`). `status` is one of
`unsupported`, `idle`, `checking`, `available`, `not-available`, `downloading`,
`cancelling`, `downloaded`, `installing`, or `error`; missing version/progress
values are `null`. There is no bridge in the regular browser dashboard.

All five IPC handlers accept only the current owned dashboard's main frame, and
reject arguments, foreign windows, subframes, and other URLs. State events strip
native IPC events and contain no release HTML, filesystem paths, backend token,
or raw updater errors. Neither renderer nor backend can choose an update feed,
executable, command, or file to install. This is a private UI contract, not a
public updater SDK.
