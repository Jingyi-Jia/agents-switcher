"""Install attested community artifacts only on disposable GitHub-hosted runners."""

import argparse
import json
import os
from pathlib import Path
import platform
import re
import runpy
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
REPOSITORY = "Jingyi-Jia/agents-switcher"


def validate_run(run_id, source_sha):
    if not re.fullmatch(r"[1-9][0-9]{0,19}", run_id) or not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ValueError("A release run ID and full source SHA are required")
    data = json.loads(subprocess.check_output([
        "gh", "api", f"repos/{REPOSITORY}/actions/runs/{run_id}",
    ], text=True, timeout=30))
    if (data.get("head_sha") != source_sha or data.get("head_branch") != "main"
            or data.get("path") != ".github/workflows/release.yml"
            or data.get("event") != "workflow_dispatch" or data.get("status") != "completed"
            or data.get("conclusion") != "success"):
        raise ValueError("Only a successful reviewed-main release run can supply installers")


def require_disposable(target, arch, confirmed):
    native = {"darwin": "mac", "win32": "win"}.get(sys.platform)
    native_arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64", "amd64": "x64"}.get(platform.machine().lower())
    if (confirmed is not True or os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
            or target != native or arch != native_arch or target not in {"mac", "win"}):
        raise ValueError("Installer checks require the matching disposable GitHub-hosted runner")


def smoke(application, target, arch, version, work):
    harness = work / "harness"
    (harness / "scripts").mkdir(parents=True)
    (harness / "package.json").write_text(json.dumps({"version": version}), encoding="utf-8")
    shutil.copyfile(ROOT / "desktop/scripts/smoke_app.cjs", harness / "scripts/smoke_app.cjs")
    if target == "mac":
        link = harness / "release" / ("mac-arm64" if arch == "arm64" else "mac") / "Agent Switch.app"
        helper = application / "Contents/Resources/backend/agent-switch-backend"
    else:
        link = harness / "release/win-unpacked"
        helper = application / "resources/backend/agent-switch-backend.exe"
    link.parent.mkdir(parents=True)
    subprocess.run([
        "node", "-e", "require('node:fs').symlinkSync(process.argv[1], process.argv[2], 'junction')",
        str(application), str(link),
    ], check=True, timeout=30)
    subprocess.run([
        sys.executable, str(ROOT / "desktop/scripts/smoke_backend.py"),
        "--executable", str(helper), "--disposable-runner", "--check-processes", "--check-tls",
    ], check=True, timeout=180)
    subprocess.run([
        "node", str(harness / "scripts/smoke_app.cjs"), target, arch, "community", "--disposable-runner",
    ], check=True, timeout=180)


def install_mac(directory, arch, version, work):
    destination = Path("/Applications/Agent Switch.app")
    for extension in ("dmg", "zip"):
        if destination.exists() or destination.is_symlink():
            raise ValueError("Refusing to replace an existing Applications installation")
        case = work / extension
        case.mkdir()
        unpacked = case / "unpacked"
        unpacked.mkdir()
        artifact = directory / f"Agent-Switch-{version}-mac-{arch}.{extension}"
        mounted = False
        try:
            if extension == "dmg":
                subprocess.run(["hdiutil", "verify", str(artifact)], check=True, timeout=90)
                subprocess.run([
                    "hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint", str(unpacked), str(artifact),
                ], check=True, timeout=90)
                mounted = True
            else:
                subprocess.run(["ditto", "-x", "-k", str(artifact), str(unpacked)], check=True, timeout=90)
            source = unpacked / "Agent Switch.app"
            if not source.is_dir() or source.is_symlink():
                raise ValueError("The distribution does not contain its expected app bundle")
            subprocess.run(["ditto", str(source), str(destination)], check=True, timeout=90)
            subprocess.run(["codesign", "--verify", "--deep", "--strict", str(destination)], check=True, timeout=90)
            smoke(destination, "mac", arch, version, case)
            print(f"Installed mac-{arch} {extension} into Applications and passed isolated first-launch checks", flush=True)
        finally:
            if destination.exists() and not destination.is_symlink():
                shutil.rmtree(destination)
            if mounted:
                subprocess.run(["hdiutil", "detach", str(unpacked)], check=True, timeout=30)


def install_windows(directory, version, work):
    environment = runpy.run_path(str(ROOT / "desktop/scripts/smoke_backend.py"))["isolated_environment"](work)
    destination = work / "Installed Agent Switch"
    artifact = directory / f"Agent-Switch-{version}-win-x64.exe"
    if destination.exists():
        raise ValueError("Refusing to replace an existing Windows installation")
    subprocess.run(f'"{artifact}" /S /currentuser /D={destination}', check=True, timeout=180, env=environment)
    if not (destination / "Agent Switch.exe").is_file():
        raise ValueError("NSIS did not install the app into the requested test directory")
    smoke(destination, "win", "x64", version, work)
    uninstaller = destination / "Uninstall Agent Switch.exe"
    if not uninstaller.is_file():
        raise ValueError("The installed NSIS uninstaller is missing")
    copied_uninstaller = work / "release-uninstaller.exe"
    shutil.copyfile(uninstaller, copied_uninstaller)
    subprocess.run(f'"{copied_uninstaller}" /S /currentuser _?={destination}', check=True, timeout=90, env=environment)
    if (destination / "Agent Switch.exe").exists():
        raise ValueError("The native uninstaller did not remove the app")
    print("NSIS installation, isolated first launch, and native uninstall passed", flush=True)


def verify_installers(directory, target, arch, source_sha, disposable):
    require_disposable(target, arch, disposable)
    release = runpy.run_path(str(ROOT / ".github/scripts/desktop_release.py"))
    version = release["source_version"]()
    directory = directory.resolve(strict=True)
    release["verify_group"](directory, target, arch, version, "community", source_sha)
    with tempfile.TemporaryDirectory(prefix="agent-switch-installers-") as temporary:
        work = Path(temporary)
        if target == "mac":
            install_mac(directory, arch, version, work)
        else:
            install_windows(directory, version, work)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--check-run", action="store_true")
    parser.add_argument("--platform", choices=("mac", "win"))
    parser.add_argument("--arch", choices=("arm64", "x64"))
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--disposable-runner", action="store_true")
    args = parser.parse_args()
    validate_run(args.run_id, args.source_sha)
    if args.check_run:
        print("Release run and reviewed main source verified")
        return
    if args.directory is None:
        parser.error("--directory is required for installation checks")
    verify_installers(args.directory, args.platform, args.arch, args.source_sha, args.disposable_runner)


if __name__ == "__main__":
    main()
