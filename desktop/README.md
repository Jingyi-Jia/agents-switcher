# Agent Switch desktop packaging

Agent Switch bundles an Electron window and a PyInstaller-frozen Python backend.
The installer contains the app runtime: users do **not** need Python, uv, Node.js,
or npm. Claude Code and Codex themselves are not bundled. Sign in through Claude
Code's CLI, or through Codex Desktop or CLI with a file-backed login; the dashboard
can then add that current login. The Codex CLI is not required for a supported
Desktop login. Claude Code CLI credentials are separate from Claude Desktop's
login.

## Downloads and installation

Use the assets on the [GitHub Releases page](https://github.com/jingyi-jia/agents-switcher/releases)
when a release is published. Draft artifacts and package versions are not
published downloads. Match both your operating system and CPU:

| Platform | Requirement | Download and install |
| --- | --- | --- |
| macOS, Apple Silicon | macOS 13 or newer, M-series Mac | `Agent-Switch-<version>-mac-arm64.dmg`; open it and drag **Agent Switch** to Applications. A `.zip` of the same app is also provided. |
| macOS, Intel | macOS 13 or newer, Intel Mac | `Agent-Switch-<version>-mac-x64.dmg`; open it and drag **Agent Switch** to Applications. |
| Windows | Windows 10 or newer, x64 | `Agent-Switch-<version>-win-x64.exe`; run the unsigned NSIS installer, then launch **Agent Switch** from Start. A no-install ZIP is deferred. Windows ARM is not a native build target. |
| Linux | x64 desktop, Ubuntu 22.04 or newer as the build baseline | Use the `.deb` on Debian/Ubuntu, or the `.AppImage` on compatible distributions. A `.tar.gz` fallback contains the unpacked application. |

On Ubuntu/Debian, install the downloaded `.deb` with your graphical package
installer, or `sudo apt install ./Agent-Switch-<version>-linux-amd64.deb`. For an
AppImage, mark it executable in file properties and open it; AppImage needs the
distribution's FUSE 2 compatibility support. If FUSE is unavailable, prefer the
`.deb` or extract the `.tar.gz` and launch `agent-switch-desktop`. Linux needs a
working graphical session and Chromium's system libraries/sandbox support;
bundling Python does not make the app independent of the OS. Other Linux
distributions are not yet validated.

The public community distribution does not require paid Apple or Windows signing
credentials. Both macOS architectures use free ad-hoc signatures to seal the
modified app bundle, with the existing hardened-runtime entitlements; these
signatures do not identify an Apple-trusted developer and are not notarization.
Community Windows releases are unsigned NSIS installers. Linux retains the
AppImage, deb, and tar formats. A separate signed distribution remains optional
and future: it requires Developer ID signing and notarization on macOS and
Authenticode signing on Windows. Signing credentials are not included in this
repository, and an explicitly signed build must fail rather than silently fall
back to an unsigned artifact when credentials are missing.

PR and manual-workflow artifacts remain developer previews, not recommended
downloads for nontechnical users. Fresh native first-launch and installation
testing is required for public community releases; CI helper checks are not
proof of a working native package. Gatekeeper or SmartScreen may still block
community downloads. Do not disable OS protections; use a disposable development
environment for preview testing.

Older Mac previews skipped bundle signing and can report that Agent Switch
"is damaged and can't be opened." Use a newer build rather than removing
quarantine attributes or re-signing the downloaded app yourself. CI verifies
the ad-hoc signature and existing hardened-runtime entitlements, but that is not
notarization or proof of safe code. If Gatekeeper offers it, follow [Apple's
official Open Anyway instructions](https://support.apple.com/en-us/102445) only
after choosing to trust the verified official download. Windows may show
SmartScreen; where offered, choose **More info → Run anyway** only after choosing
to trust the verified official download. Warnings can recur, and SmartScreen,
Smart App Control (SAC), Windows Defender Application Control, or enterprise
policy can fully block an app with no per-app override. Never disable Gatekeeper,
antivirus, SmartScreen, or Application Control, and never remove quarantine. A
damaged, malware, or
unexpected-signature alert needs investigation, not bypass instructions.

### In-app updates

Community releases expose a manual check in **Settings → App updates**.
**Help → Check for Updates** opens Settings and checks the fixed public
`Jingyi-Jia/agents-switcher` repository. If a newer release exists, **View
release** opens that corresponding GitHub release in the browser. It never
downloads an executable or self-installs, even when an IPC caller asks it to, and
it never performs automatic checks. A normal app quit never installs an update.

The runtime has three states: `manual` for a community release, `install` for a
signed release that passed all native gates, and `unsupported` for previews and
development builds. Community builds embed `agentSwitchRelease: false` and
`agentSwitchCommunityRelease: true`; signed builds embed the reverse. Defaults
are both `false`, so previews and development builds remain explicitly
unsupported. The community state exposes only a sanitized release link and safe
status; it accepts no renderer URL, feed, executable, token, or credential. There
is no HTTP update endpoint.

| Installed format | In-app update support |
| --- | --- |
| Community macOS, arm64 or x64 | Manual release-page installation of the matching ad-hoc-signed DMG/ZIP. Ad-hoc signing is not Developer ID signing or notarization. |
| Community Windows, x64 | Manual release-page installation of the unsigned NSIS installer. No-install ZIP is deferred. |
| Community Linux, x64 | Manual release-page installation of the AppImage, deb, or tar package. |
| Future signed macOS | Explicit download and install only for a Developer ID-signed, notarized app in Applications; architecture-specific feeds remain separate. |
| Future signed Windows | Explicit download and install only for a signed NSIS installation after publisher and fail-closed Authenticode checks pass. |
| Development and CI previews | Updates are unsupported, even if the version number matches a release. |

The signed path is retained as an optional future capability. It may download and
install only after explicit user action, strict signature verification, native
confirmation, and a confirmed clean backend shutdown; missing credentials or
verification must fail closed, never silently falling back to community behavior.
Its feeds remain fixed, architecture-specific where required, and free of
renderer-controlled URLs or credentials. A successful handoff to an installer is
not proof that the replacement app started; clean-machine installation and first
launch must be checked after publication.

Versions predating the community release flow need one manual installation from
the GitHub Releases page. No update mode installs during ordinary Quit.

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
The default build embeds `agentSwitchRelease: false` and
`agentSwitchCommunityRelease: false`, so previews and development builds cannot
offer update actions. The community release workflow explicitly enables only
`agentSwitchCommunityRelease`; the optional signed workflow enables only
`agentSwitchRelease` after requiring its signing inputs. Do not set the signed
flag on unsigned or ad-hoc builds.
After packaging, verify the native target, for example:

```bash
node scripts/verify-updates.cjs linux x64
```

Use `mac arm64`, `mac x64`, or `win x64` on those native build machines. The
`--release` option verifies the selected release capability on the native target.
It does not sign an app or bypass any signature gate. Keep exact workflow and
builder internals in the workflow and tests rather than treating this local
verification as proof of a published release.

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
application resources. Community packaging retains ad-hoc macOS signing on
arm64 and x64, existing hardened-runtime entitlements, an unsigned Windows NSIS
installer, and Linux AppImage/deb/tar outputs. CI has no display-driven installer
test; clean-machine installation and first-run checks remain a release
prerequisite.

The packaged-app startup smoke uses an isolated app and
the loopback debug protocol. That smoke checks renderer startup, the release
mode, normal quit, and screenshots; it does not approve browser quarantine,
Gatekeeper, SmartScreen, or installer UI behavior. Those OS approval prompts and
clean-machine installer checks still require fresh native testing.

PR path changes and the existing **Desktop installers** workflow remain
preview-only. Neither path receives signing secrets or release write permission.
The macOS preview step explicitly enables the builder's ad-hoc identity (`-`),
with certificate discovery disabled and certificate inputs removed from the
environment. This seals the modified bundle without using a private key; it does
not enable trusted release signing. The workflow uses `pull_request`, never
`pull_request_target`, and does not check out a different branch during the build.

The maintainer workflow `.github/workflows/release.yml` is displayed as
**Prepare desktop release**. It is a `workflow_dispatch` with a
`distribution` input of `community` or `signed`, defaulting to `community`. It
builds the reviewed `main` commit natively, produces desktop artifacts and the
Python distributions, and generates SHA256 checksums and provenance attestations
inside the actual build jobs. It then stages matching assets in a private draft;
the maintainer publishes only after reviewing the artifacts and attestations and
completing native installation checks. Keep Python and desktop package versions
aligned for each release; a package version or private preview is not evidence
that a public release exists.

Community releases upload no `latest*.yml` feed files and no `.blockmap` files.
The signed pipeline retains verified feeds, blockmaps, signature gates, clean
shutdown requirements, Developer ID/notarization checks, and Authenticode
verification. It must fail closed when an explicitly signed build lacks its
credentials; it must not silently become a community build. Neither workflow
configuration nor a draft artifact means that a public release has already been
published.

GitHub attestations are available without a paid plan. Verify each downloaded
file against the expected workflow and source, for example:

```bash
gh attestation verify Agent-Switch-<version>-linux-x86_64.AppImage \
  --repo Jingyi-Jia/agents-switcher
```

An attestation proves claimed provenance, not safe code or reproducible native
binaries. SHA256 checksums detect changes but do not establish publisher identity.

Before publishing a public download, install and launch every native artifact on
the target OS and architecture. For signed builds, also test an actual signed
upgrade on disposable native machines: check and download a higher stable
version, cancel a download, quit with a downloaded update (it must not install),
and explicitly install and restart. Confirm the new version after relaunch, that
the old backend exited, that saved accounts remain unchanged, and that
unsigned/wrong-publisher updates are rejected. Unit tests and a packaging
verifier cannot establish those native installation results without signed
artifacts.

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

These secrets are optional for community builds and required for the optional
signed distribution: missing credentials fail the signed macOS/Windows jobs
rather than silently distributing unsigned installers or changing the requested
mode. Signed macOS builds retain hardened runtime and notarization, sign the
embedded backend, and verify the app signature and notarization staple. Signed
Windows builds require signing through electron-builder's supported certificate
options and verify Authenticode on the app, backend, and installer. The installed
signed app's generated `app-update.yml` must contain its certificate-derived
`publisherName`; absent metadata disables signed installation rather than
bypassing signature verification. Keep the signing identity compatible across
upgrades. Organizations with hardware-
backed certificates should adapt that step to electron-builder's supported Azure
Trusted Signing or custom signing service, rather than trying to export a
non-exportable private key. No Windows cloud-signing tenant is preconfigured.

**A signed self-updating release is not ready until credentials are provisioned,
all signed native CI builds pass, and the actual downloads are installed and
opened on clean target machines.** This includes verifying the nested helper's
signature/loading on macOS and Windows, provider CLI detection, and all installer
formats. Community releases do not require these paid signing credentials. The
workflow cannot claim signed results without running with real authorized
credentials.

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
The self-installing update path deliberately restricts Linux support to AppImage
and disables the library's automatic download and install-on-quit defaults;
community manual release checks support AppImage, deb, and tar downloads.

### Private renderer bridge

The sandboxed, context-isolated preload exposes only `window.agentSwitchUpdater`:
zero-argument `getState()`, `check()`, `viewRelease()`, `download()`, `cancel()`,
and `install()` return promises of a state snapshot. `onState(callback)` returns
an unsubscribe function. A snapshot contains `schemaVersion: 1`, `supported`,
`mode` (`manual`, `install`, or `unsupported`), `status`, `currentVersion`,
`availableVersion`, `message`, and optional `progress` (`percent`, `transferred`,
`total`, `bytesPerSecond`). Community builds use `mode: "manual"`; their
download and install operations remain unavailable even if invoked through IPC.
Signed builds use `mode: "install"` only after all native gates pass. Development
and preview defaults are `mode: "unsupported"`. There is no bridge in the
regular browser dashboard.

All six IPC handlers accept only the current owned dashboard's main frame, and
reject arguments, foreign windows, subframes, and other URLs. State events strip
native IPC events and contain no release HTML, filesystem paths, backend token,
or raw updater errors. `viewRelease()` joins the existing zero-argument methods;
it resolves the fixed repository release in the browser and accepts no renderer
URL. Neither renderer nor backend can choose an update feed, executable, command,
or file to install. This is a private UI contract, not a public updater SDK.
