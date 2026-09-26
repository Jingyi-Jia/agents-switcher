"""Stage native build evidence and prepare a verified, never-published release draft."""

import argparse
import email
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tarfile
import tomllib
import urllib.error
import urllib.request
import zipfile


ROOT = Path(__file__).resolve().parents[2]
REPOSITORY = "Jingyi-Jia/agents-switcher"
TARGETS = {"desktop-mac-arm64": ("mac", "arm64"), "desktop-mac-x64": ("mac", "x64"),
           "desktop-win-x64": ("win", "x64"), "desktop-linux-x64": ("linux", "x64"),
           "python-dist": ("python", "any")}


def validate_context(version, mode, source_sha):
    if not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", version):
        raise ValueError("A stable version is required")
    if mode not in {"preview", "community", "signed"} or not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ValueError("Invalid build mode or full source SHA")


def source_version(root=ROOT):
    desktop = json.loads((root / "desktop/package.json").read_text())
    lock = json.loads((root / "desktop/package-lock.json").read_text())
    python = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    uv_lock = tomllib.loads((root / "uv.lock").read_text())
    versions = [desktop["version"], lock["version"], lock["packages"][""]["version"], python["version"]]
    versions.extend(package["version"] for package in uv_lock["package"] if package["name"] == "agents-switcher")
    if len(versions) != 5 or len(set(versions)) != 1:
        raise ValueError("Python, desktop, and lockfile versions must agree")
    return desktop["version"]


def validate_source(version, mode, source_sha):
    validate_context(version, mode, source_sha)
    if mode == "preview":
        return
    if (os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
            or os.environ.get("GITHUB_REF") != "refs/heads/main"
            or os.environ.get("GITHUB_REPOSITORY", "").lower() != REPOSITORY.lower()
            or source_sha != os.environ.get("GITHUB_SHA")):
        raise ValueError("Release preparation requires the reviewed SHA of the dispatched main ref")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if head != source_sha:
        raise ValueError("The checked-out source differs from the reviewed main SHA")


def artifact_names(platform, arch, version, mode):
    if mode not in {"preview", "community", "signed"} or (platform, arch) not in TARGETS.values():
        raise ValueError("Unsupported distribution target or mode")
    if platform == "python":
        return [f"agents_switcher-{version}-py3-none-any.whl", f"agents_switcher-{version}.tar.gz"]
    stem = f"Agent-Switch-{version}-{platform}"
    if platform == "mac":
        files = [f"{stem}-{arch}.dmg", f"{stem}-{arch}.zip"]
        metadata = f"latest-{arch}-mac.yml"
    elif platform == "win":
        files = [f"{stem}-x64.exe"]
        metadata = "latest.yml"
    else:
        files = [f"{stem}-x86_64.AppImage", f"{stem}-amd64.deb", f"{stem}-x64.tar.gz"]
        metadata = "latest-linux.yml"
    if mode != "community":
        if platform != "linux":
            files += [f"{name}.blockmap" for name in files]
        files.append(metadata)
    return sorted(files)


def file_record(file):
    if not stat.S_ISREG(file.lstat().st_mode) or file.stat().st_size == 0:
        raise ValueError(f"A nonempty regular artifact is required: {file.name}")
    with file.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"name": file.name, "size": file.stat().st_size, "sha256": digest}


def checksum_text(records):
    return "".join(f"{record['sha256']}  {record['name']}\n" for record in records)


def verify_python_distributions(directory, version):
    with zipfile.ZipFile(directory / f"agents_switcher-{version}-py3-none-any.whl") as wheel:
        wheel_info = email.message_from_bytes(wheel.read(f"agents_switcher-{version}.dist-info/WHEEL"))
        if wheel_info.get_all("Tag") != ["py3-none-any"]:
            raise ValueError("Unexpected Python wheel target")
        metadata = [wheel.read(f"agents_switcher-{version}.dist-info/METADATA")]
    with tarfile.open(directory / f"agents_switcher-{version}.tar.gz", "r:gz") as sdist:
        member = sdist.getmember(f"agents_switcher-{version}/PKG-INFO")
        if not member.isfile():
            raise ValueError("Invalid Python source distribution metadata")
        metadata.append(sdist.extractfile(member).read())
    for data in metadata:
        info = email.message_from_bytes(data)
        if info.get("Name", "").replace("_", "-") != "agents-switcher" or info.get("Version") != version:
            raise ValueError("Python distribution metadata disagrees with the release version")


def stage(directory, output, platform, arch, version, mode, source_sha):
    validate_context(version, mode, source_sha)
    names = artifact_names(platform, arch, version, mode)
    distributables = {file.name for file in directory.iterdir()
                     if file.name.endswith((".dmg", ".zip", ".exe", ".AppImage", ".deb", ".tar.gz", ".blockmap", ".whl"))
                     or (file.name.startswith("latest") and file.name.endswith(".yml"))}
    allowed = set(artifact_names(platform, arch, version, "signed"))
    if not set(names) <= distributables or not distributables <= allowed:
        raise ValueError("Build output contains missing or unexpected distribution artifacts")
    records = [file_record(directory / name) for name in names]
    if platform == "python":
        verify_python_distributions(directory, version)
    output.mkdir(parents=True, exist_ok=False)
    for name in names:
        shutil.copyfile(directory / name, output / name)
    manifest = {"schemaVersion": 1, "version": version, "mode": mode, "sourceSha": source_sha,
                "platform": platform, "arch": arch, "artifacts": records}
    (output / f"BUILD-{platform}-{arch}.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (output / f"SHA256SUMS-{platform}-{arch}.txt").write_text(checksum_text(records), encoding="utf-8")


def verify_group(directory, platform, arch, version, mode, source_sha):
    validate_context(version, mode, source_sha)
    if mode == "preview":
        raise ValueError("Preview artifacts cannot be used in a release draft")
    names = artifact_names(platform, arch, version, mode)
    manifest_name = f"BUILD-{platform}-{arch}.json"
    checksum_name = f"SHA256SUMS-{platform}-{arch}.txt"
    bundle_name = f"attestation-{platform}-{arch}.json"
    all_names = [*names, manifest_name, checksum_name, bundle_name]
    if directory.is_symlink() or {file.name for file in directory.iterdir()} != set(all_names):
        raise ValueError("Release artifact directory differs from the exact allowlist")
    for name in all_names:
        file_record(directory / name)
    records = [file_record(directory / name) for name in names]
    expected = {"schemaVersion": 1, "version": version, "mode": mode, "sourceSha": source_sha,
                "platform": platform, "arch": arch, "artifacts": records}
    if json.loads((directory / manifest_name).read_text()) != expected:
        raise ValueError("Release manifest, platform, source, or artifact digests do not match")
    if (directory / checksum_name).read_text() != checksum_text(records):
        raise ValueError("Release checksums do not match the final files")
    if platform == "python":
        verify_python_distributions(directory, version)
    workflow = "release.yml" if platform == "python" else "desktop-build.yml"
    for name in [*names, manifest_name, checksum_name]:
        subprocess.run([
            "gh", "attestation", "verify", str(directory / name), "--bundle", str(directory / bundle_name),
            "--repo", REPOSITORY, "--signer-workflow", f"{REPOSITORY}/.github/workflows/{workflow}",
            "--signer-digest", source_sha, "--source-digest", source_sha,
            "--source-ref", "refs/heads/main", "--deny-self-hosted-runners",
        ], check=True)
    return [directory / name for name in all_names]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, url):
        return None


class GitHub:
    def request(self, method, path, data=None, missing_ok=False):
        request = urllib.request.Request(
            f"https://api.github.com/repos/{REPOSITORY}/{path}", method=method,
            data=json.dumps(data).encode() if data is not None else None,
            headers={"Authorization": f"Bearer {os.environ['GH_TOKEN']}", "Accept": "application/vnd.github+json",
                     "Content-Type": "application/json", "X-GitHub-Api-Version": "2022-11-28"},
        )
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=60) as response:
                body = response.read()
                return json.loads(body) if body else None
        except urllib.error.HTTPError as error:
            if missing_ok and error.code == 404:
                return None
            raise RuntimeError(f"GitHub release request failed (HTTP {error.code})") from None


def marker(version, mode, source_sha):
    return "<!-- agent-switch-draft " + json.dumps({"version": version, "mode": mode, "sourceSha": source_sha}, sort_keys=True) + " -->"


def validate_draft(release, version, mode, source_sha):
    if (release.get("draft") is not True or release.get("prerelease") is not False
            or release.get("tag_name") != f"v{version}" or release.get("target_commitish") != source_sha
            or marker(version, mode, source_sha) not in (release.get("body") or "").splitlines()):
        raise ValueError("Existing release is published or is not a matching source/version/distribution draft")


def validate_tag(api, version, source_sha):
    ref = api.request("GET", f"git/ref/tags/v{version}", missing_ok=True)
    if ref is None:
        return
    target = ref["object"]
    for _ in range(5):
        if target.get("type") == "commit" and target.get("sha") == source_sha:
            return
        if target.get("type") != "tag" or not re.fullmatch(r"[0-9a-f]{40}", target.get("sha", "")):
            break
        target = api.request("GET", f"git/tags/{target['sha']}")["object"]
    raise ValueError("Existing release tag does not resolve to the reviewed source SHA")


def validate_public_release(api, version, source_sha):
    validate_tag(api, version, source_sha)
    release = api.request("GET", f"releases/tags/v{version}", missing_ok=True)
    if release is not None:
        raise ValueError("A published release already exists for this version")


def existing_draft(api, version, mode, source_sha):
    validate_public_release(api, version, source_sha)
    candidate = None
    page_number = 1
    seen_ids = set()
    while True:
        releases = api.request("GET", f"releases?per_page=100&page={page_number}")
        if not isinstance(releases, list) or len(releases) > 100:
            raise ValueError("Invalid release listing response")
        for release in releases:
            if (not isinstance(release, dict) or type(release.get("id")) is not int or release["id"] <= 0
                    or not isinstance(release.get("tag_name"), str)):
                raise ValueError("Invalid release listing entry")
            if release["id"] in seen_ids:
                raise ValueError("Release pagination returned duplicate entries")
            seen_ids.add(release["id"])
            if release["tag_name"] == f"v{version}":
                if candidate is not None:
                    raise ValueError("Multiple release candidates exist for this version")
                validate_draft(release, version, mode, source_sha)
                candidate = release
        if len(releases) < 100:
            return candidate
        page_number += 1


def prepare_draft(directory, version, mode, source_sha, api):
    if directory.is_symlink() or {file.name for file in directory.iterdir()} != set(TARGETS):
        raise ValueError("All four native targets and the Python distribution are required")
    files = []
    for group, (platform, arch) in TARGETS.items():
        files.extend(verify_group(directory / group, platform, arch, version, mode, source_sha))
    release = existing_draft(api, version, mode, source_sha)
    if release is None:
        notice = ("Community build: macOS uses ad-hoc signing without notarization; Windows is unsigned. "
                  "Updates are manual downloads, not in-app installation." if mode == "community" else
                  "Signed distribution: macOS notarization and Windows signature gates are required; update feeds are included.")
        release = api.request("POST", "releases", {
            "tag_name": f"v{version}", "target_commitish": source_sha, "name": f"Agent Switch {version} ({mode})",
            "draft": True, "prerelease": False,
            "body": f"{notice}\n\nPrepared from `{source_sha}`. Maintainer verification and manual publication are still required.\n\n"
                    + marker(version, mode, source_sha),
        })
    validate_draft(release, version, mode, source_sha)
    release_path = f"releases/{int(release['id'])}"
    assets = api.request("GET", f"{release_path}/assets?per_page=100")
    expected = {file.name: file_record(file)["sha256"] for file in files}
    if len(assets) != len({asset["name"] for asset in assets}) or not {asset["name"] for asset in assets} <= set(expected):
        raise ValueError("Existing draft contains unexpected assets")
    by_name = {asset["name"]: asset for asset in assets}
    for file in files:
        validate_tag(api, version, source_sha)
        validate_draft(api.request("GET", release_path), version, mode, source_sha)
        old = by_name.get(file.name)
        if old is not None:
            if old.get("digest") == f"sha256:{expected[file.name]}":
                continue
            api.request("DELETE", f"releases/assets/{int(old['id'])}")
        subprocess.run(["gh", "release", "upload", f"v{version}", str(file), "--repo", REPOSITORY], check=True)
    validate_tag(api, version, source_sha)
    validate_draft(api.request("GET", release_path), version, mode, source_sha)
    uploaded = api.request("GET", f"{release_path}/assets?per_page=100")
    if (len(uploaded) != len(expected)
            or {asset["name"]: asset.get("digest") for asset in uploaded} != {name: f"sha256:{digest}" for name, digest in expected.items()}):
        raise ValueError("Uploaded draft assets do not match the verified final file digests")
    print(f"Verified private draft v{version} at {source_sha}; publication remains a manual maintainer action.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["source", "preflight", "stage", "verify", "draft"])
    parser.add_argument("--mode", required=True, choices=["preview", "community", "signed"])
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--platform")
    parser.add_argument("--arch")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--directory", type=Path)
    args = parser.parse_args()
    version = source_version()
    validate_source(version, args.mode, args.source_sha)
    if args.command in {"preflight", "verify", "draft"} and args.mode == "preview":
        raise ValueError("Preview builds cannot prepare releases")
    if args.command == "preflight":
        validate_public_release(GitHub(), version, args.source_sha)
    elif args.command == "stage":
        stage(args.input, args.output, args.platform, args.arch, version, args.mode, args.source_sha)
    elif args.command == "verify":
        verify_group(args.directory, args.platform, args.arch, version, args.mode, args.source_sha)
    elif args.command == "draft":
        prepare_draft(args.directory, version, args.mode, args.source_sha, GitHub())


if __name__ == "__main__":
    main()
