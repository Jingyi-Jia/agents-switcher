# Agent Switch desktop packaging

Agent Switch bundles an Electron window and a PyInstaller-frozen Python backend.
The installer contains the app runtime: users do **not** need Python, uv, Node.js,
or npm. Claude Code and Codex themselves are not bundled. Install the provider
CLI you use and sign in there first; the dashboard can then add that current
login. Claude Code CLI credentials are separate from Claude Desktop's login.

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
manual-workflow artifacts are unsigned developer previews (macOS arm64 may be
ad-hoc signed), not recommended downloads for nontechnical users. An unsigned
preview may be blocked by Gatekeeper or SmartScreen. Do not disable these
protections; wait for a trusted release or build in a disposable development
environment. Windows reputation warnings can still occur for newly signed apps.

There is no auto-updater. Use **Help → Download updates** to visit Releases and install
the newer version. Quitting the application, including closing its only window,
stops its backend and session-owned auto-switching. It does not stop independently
started CLI automation. Updating or uninstalling the app does not remove saved
provider accounts or provider CLI credentials.

## Build locally

Build on each target OS and CPU architecture. PyInstaller is not a cross-compiler;
an x64 helper must not be placed in an arm64 Electron app. CI uses Python 3.12,
Node.js 24.18.0, uv 0.12.3, and the locked PyInstaller 6.22.3, Electron 44.4.5,
and electron-builder 26.15.3. The Python `desktop-build` dependency group is
optional and separate from normal runtime dependencies.

From the repository root:

```bash
uv sync --locked --python 3.12 --group desktop-build
npm ci --prefix desktop
uv run --no-sync pytest -o faulthandler_timeout=600
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

Outputs are in `desktop/release/`. A local macOS build can discover your own
signing identity; set `CSC_IDENTITY_AUTO_DISCOVERY=false` for a preview without
using that identity. Linux packaging needs the normal native build utilities
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
application resources. Each job uploads only installers and SHA-256 checksums,
not unpacked workspaces or accounts. CI has no display-driven installer test;
clean-machine installation and first-run checks remain a release prerequisite.

PR path changes and **Run workflow** produce preview artifacts in the workflow
run's Artifacts section. Neither path receives signing secrets or release write
permission. The workflow uses `pull_request`, never `pull_request_target`, and
does not check out a different branch during the build.

A maintainer publishing an existing GitHub release triggers the release build
from that tag. The workflow never creates tags or releases. All native builds
must pass before a separate, narrowly permissioned job attaches their artifacts
to that existing release. Re-running a release replaces matching artifact names.
Maintainers must review the tagged source and align Python and desktop package
versions before publishing. Published releases are the trusted trigger: restrict
who can create releases and protect release tags.

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
app, backend and installer. Organizations with hardware-
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
