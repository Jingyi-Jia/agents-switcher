import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import urllib.error
from unittest.mock import Mock
import zipfile

import pytest


VERSION = "3.2.1"
SHA = "a" * 40
OTHER_SHA = "b" * 40


@pytest.fixture
def release_script():
    script = Path(__file__).resolve().parents[1] / ".github/scripts/desktop_release.py"
    spec = importlib.util.spec_from_file_location("desktop_release", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def distributions(directory, version=VERSION, metadata_version=VERSION):
    metadata = f"Name: agents-switcher\nVersion: {metadata_version}\n".encode()
    with zipfile.ZipFile(directory / f"agents_switcher-{version}-py3-none-any.whl", "w") as wheel:
        wheel.writestr(f"agents_switcher-{version}.dist-info/METADATA", metadata)
        wheel.writestr(f"agents_switcher-{version}.dist-info/WHEEL", "Wheel-Version: 1.0\nTag: py3-none-any\n")
    with tarfile.open(directory / f"agents_switcher-{version}.tar.gz", "w:gz") as sdist:
        member = tarfile.TarInfo(f"agents_switcher-{version}/PKG-INFO")
        member.size = len(metadata)
        sdist.addfile(member, io.BytesIO(metadata))


def staged(module, tmp_path, platform="win", arch="x64", mode="community"):
    raw, output = tmp_path / "raw", tmp_path / "output"
    raw.mkdir(parents=True)
    if platform == "python":
        distributions(raw)
    else:
        for name in module.artifact_names(platform, arch, VERSION, "signed"):
            (raw / name).write_bytes(b"synthetic artifact")
        (raw / "builder-effective-config.yaml").write_text("private build configuration")
    module.stage(raw, output, platform, arch, VERSION, mode, SHA)
    return raw, output


@pytest.fixture
def gh_verifier(release_script, monkeypatch):
    run = Mock()
    monkeypatch.setattr(release_script.subprocess, "run", run)
    return run


@pytest.mark.parametrize("platform,arch", [("mac", "arm64"), ("mac", "x64"), ("win", "x64"), ("linux", "x64"), ("python", "any")])
@pytest.mark.parametrize("mode", ["community", "signed"])
def test_staging_and_attestation_cover_exact_final_native_files(release_script, tmp_path, gh_verifier, platform, arch, mode):
    _, output = staged(release_script, tmp_path, platform, arch, mode)
    (output / f"attestation-{platform}-{arch}.json").write_text("synthetic bundle")
    files = release_script.verify_group(output, platform, arch, VERSION, mode, SHA)
    names = {file.name for file in files}
    expected = set(release_script.artifact_names(platform, arch, VERSION, mode))
    assert names == expected | {f"BUILD-{platform}-{arch}.json", f"SHA256SUMS-{platform}-{arch}.txt", f"attestation-{platform}-{arch}.json"}
    assert "builder-effective-config.yaml" not in names
    if mode == "community":
        assert not any(name.endswith(".blockmap") or name.startswith("latest") for name in names)
    elif platform != "python":
        assert any(name.startswith("latest") for name in names)
    assert gh_verifier.call_count == len(names) - 1
    for call in gh_verifier.call_args_list:
        command = call.args[0]
        assert command[:3] == ["gh", "attestation", "verify"]
        assert command[command.index("--repo") + 1] == release_script.REPOSITORY
        assert command[command.index("--source-digest") + 1] == SHA
        assert command[command.index("--signer-digest") + 1] == SHA
        assert command[command.index("--source-ref") + 1] == "refs/heads/main"
        workflow = "release.yml" if platform == "python" else "desktop-build.yml"
        assert command[command.index("--signer-workflow") + 1].endswith(f"/.github/workflows/{workflow}")
        assert "--deny-self-hosted-runners" in command
        assert call.kwargs == {"check": True}


def test_missing_unknown_or_wrong_architecture_artifacts_block_staging(release_script, tmp_path):
    raw, _ = staged(release_script, tmp_path)
    installer = raw / f"Agent-Switch-{VERSION}-win-x64.exe"
    installer.rename(raw / f"Agent-Switch-{VERSION}-win-arm64.exe")
    with pytest.raises(ValueError, match="missing or unexpected"):
        release_script.stage(raw, tmp_path / "another", "win", "x64", VERSION, "community", SHA)


def test_python_metadata_must_match_both_distribution_names(release_script, tmp_path):
    distributions(tmp_path, metadata_version="0.0.1")
    with pytest.raises(ValueError, match="metadata disagrees"):
        release_script.stage(tmp_path, tmp_path / "output", "python", "any", VERSION, "community", SHA)


@pytest.mark.parametrize("mutation", ["digest", "checksum", "mode", "platform", "arch", "source", "version", "extra", "feed", "missing-bundle", "symlink", "empty"])
def test_unverified_or_unexpected_files_never_reach_attestation_verification(release_script, tmp_path, gh_verifier, mutation):
    _, output = staged(release_script, tmp_path)
    bundle = output / "attestation-win-x64.json"
    bundle.write_text("synthetic bundle")
    installer = output / f"Agent-Switch-{VERSION}-win-x64.exe"
    manifest_path = output / "BUILD-win-x64.json"
    manifest = json.loads(manifest_path.read_text())
    if mutation == "digest":
        installer.write_bytes(b"tampered")
    elif mutation == "checksum":
        (output / "SHA256SUMS-win-x64.txt").write_text("bad digest")
    elif mutation in {"mode", "platform", "arch", "source", "version"}:
        manifest[{"source": "sourceSha"}.get(mutation, mutation)] = "wrong"
        manifest_path.write_text(json.dumps(manifest))
    elif mutation in {"extra", "feed"}:
        (output / ("latest.yml" if mutation == "feed" else "secret.txt")).write_text("unexpected")
    elif mutation == "missing-bundle":
        bundle.unlink()
    elif mutation == "symlink":
        installer.unlink()
        installer.symlink_to(bundle)
    elif mutation == "empty":
        installer.write_bytes(b"")
    with pytest.raises(ValueError):
        release_script.verify_group(output, "win", "x64", VERSION, "community", SHA)
    gh_verifier.assert_not_called()


def test_failed_attestation_is_fatal(release_script, tmp_path, gh_verifier):
    _, output = staged(release_script, tmp_path)
    (output / "attestation-win-x64.json").write_text("invalid bundle")
    gh_verifier.side_effect = subprocess.CalledProcessError(1, "gh")
    with pytest.raises(subprocess.CalledProcessError):
        release_script.verify_group(output, "win", "x64", VERSION, "community", SHA)


def test_previews_have_no_release_capability(release_script, tmp_path):
    _, output = staged(release_script, tmp_path, mode="preview")
    with pytest.raises(ValueError, match="Preview artifacts"):
        release_script.verify_group(output, "win", "x64", VERSION, "preview", SHA)


@pytest.mark.parametrize("version", ["1.2.3-beta.1", "1.2.3+build", "01.2.3", "v1.2.3", "1.2"])
def test_only_stable_versions_can_be_released(release_script, version):
    with pytest.raises(ValueError, match="stable"):
        release_script.validate_context(version, "community", SHA)


def test_only_the_dispatched_reviewed_main_sha_can_be_built(release_script, monkeypatch):
    values = {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/heads/main",
              "GITHUB_REPOSITORY": release_script.REPOSITORY, "GITHUB_SHA": SHA}
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    checkout = Mock(return_value=SHA + "\n")
    monkeypatch.setattr(release_script.subprocess, "check_output", checkout)
    release_script.validate_source(VERSION, "community", SHA)
    for key, invalid in {"GITHUB_EVENT_NAME": "pull_request", "GITHUB_REF": "refs/heads/feature",
                         "GITHUB_REPOSITORY": "attacker/fork", "GITHUB_SHA": OTHER_SHA}.items():
        monkeypatch.setenv(key, invalid)
        with pytest.raises(ValueError, match="reviewed SHA"):
            release_script.validate_source(VERSION, "signed", SHA)
        monkeypatch.setenv(key, values[key])
    checkout.return_value = OTHER_SHA
    with pytest.raises(ValueError, match="checked-out source"):
        release_script.validate_source(VERSION, "community", SHA)


def test_python_desktop_and_lock_versions_must_agree(release_script, tmp_path):
    (tmp_path / "desktop").mkdir()
    (tmp_path / "desktop/package.json").write_text(json.dumps({"version": VERSION}))
    (tmp_path / "desktop/package-lock.json").write_text(json.dumps({"version": VERSION, "packages": {"": {"version": VERSION}}}))
    (tmp_path / "pyproject.toml").write_text(f'[project]\nversion = "{VERSION}"\n')
    (tmp_path / "uv.lock").write_text(f'[[package]]\nname = "agents-switcher"\nversion = "{VERSION}"\n')
    assert release_script.source_version(tmp_path) == VERSION
    (tmp_path / "uv.lock").write_text('[[package]]\nname = "agents-switcher"\nversion = "0.0.1"\n')
    with pytest.raises(ValueError, match="versions must agree"):
        release_script.source_version(tmp_path)


def draft(module, **changes):
    return {"id": 42, "tag_name": f"v{VERSION}", "draft": True, "prerelease": False,
            "target_commitish": SHA, "body": module.marker(VERSION, "community", SHA), **changes}


@pytest.mark.parametrize("changes", [{"draft": False}, {"draft": 1}, {"prerelease": True}, {"target_commitish": "main"},
                                    {"target_commitish": OTHER_SHA}, {"tag_name": "v0.0.1"}, {"body": "unowned draft"}])
def test_existing_published_or_unmatched_drafts_are_never_modified(release_script, changes):
    with pytest.raises(ValueError, match="published or is not a matching"):
        release_script.validate_draft(draft(release_script, **changes), VERSION, "community", SHA)


def test_distribution_cannot_be_changed_on_retry(release_script):
    release_script.validate_draft(draft(release_script), VERSION, "community", SHA)
    with pytest.raises(ValueError, match="not a matching"):
        release_script.validate_draft(draft(release_script), VERSION, "signed", SHA)


@pytest.mark.parametrize("annotated", [False, True])
def test_existing_tags_must_resolve_to_the_exact_source(release_script, annotated):
    api = Mock()
    for sha in [SHA, OTHER_SHA]:
        api.request.side_effect = ([{"object": {"type": "tag", "sha": "c" * 40}}, {"object": {"type": "commit", "sha": sha}}]
                                   if annotated else [{"object": {"type": "commit", "sha": sha}}])
        if sha == SHA:
            release_script.validate_tag(api, VERSION, SHA)
        else:
            with pytest.raises(ValueError, match="tag does not resolve"):
                release_script.validate_tag(api, VERSION, SHA)


def release_page(start=1000, count=100):
    return [{"id": number, "tag_name": f"v0.0.{number}"} for number in range(start, start + count)]


def discovery_api(pages, published=None):
    def request(method, path, **kwargs):
        assert method == "GET"
        if path.startswith("git/ref/tags/"):
            assert kwargs == {"missing_ok": True}
            return None
        if path.startswith("releases/tags/"):
            assert kwargs == {"missing_ok": True}
            if isinstance(published, Exception):
                raise published
            return published
        assert not kwargs
        assert path.startswith("releases?per_page=100&page=")
        page = pages[int(path.split("page=")[-1]) - 1]
        if isinstance(page, Exception):
            raise page
        return page

    return Mock(request=Mock(side_effect=request))


@pytest.mark.parametrize("pages", [[], release_page()])
def test_release_listing_paginates_until_a_short_page_not_a_missing_tag(release_script, pages):
    responses = [pages, []] if pages else [[]]
    api = discovery_api(responses)
    assert release_script.existing_draft(api, VERSION, "community", SHA) is None
    list_calls = [call for call in api.request.call_args_list if call.args[1].startswith("releases?")]
    assert [call.args[1] for call in list_calls] == [f"releases?per_page=100&page={page}" for page in range(1, len(responses) + 1)]


@pytest.mark.parametrize("changes", [{"target_commitish": OTHER_SHA}, {"body": "unmatched marker"}, {"draft": False},
                                    {"prerelease": True}, {"target_commitish": "main"}])
def test_draft_listing_refuses_unmatched_or_published_candidates(release_script, changes):
    api = discovery_api([[draft(release_script, **changes)]])
    with pytest.raises(ValueError, match="published or is not a matching"):
        release_script.existing_draft(api, VERSION, "community", SHA)


@pytest.mark.parametrize("second_page,same_id", [(False, False), (True, False), (True, True)])
def test_draft_discovery_checks_all_pages_and_refuses_duplicate_candidates(release_script, second_page, same_id):
    first = draft(release_script)
    second = draft(release_script, id=first["id"] if same_id else 43)
    pages = [[first, *release_page(count=99)], [second]] if second_page else [[first, second]]
    api = discovery_api(pages)
    with pytest.raises(ValueError, match="Multiple release candidates|duplicate entries"):
        release_script.existing_draft(api, VERSION, "community", SHA)


@pytest.mark.parametrize("page", [None, {}, release_page(count=101), [None], [{"id": True, "tag_name": "v3.2.1"}],
                                [{"id": 1}], [{"id": -1, "tag_name": "v3.2.1"}]])
def test_invalid_release_listings_fail_closed(release_script, page):
    api = discovery_api([page])
    with pytest.raises(ValueError, match="Invalid release listing"):
        release_script.existing_draft(api, VERSION, "community", SHA)


def test_repeated_pages_fail_instead_of_looping_or_claiming_no_draft(release_script):
    api = discovery_api([release_page(), release_page()])
    with pytest.raises(ValueError, match="pagination returned duplicate"):
        release_script.existing_draft(api, VERSION, "community", SHA)


@pytest.mark.parametrize("status", [403, 404, 500])
def test_draft_discovery_never_treats_a_pagination_error_as_an_empty_page(release_script, status):
    api = discovery_api([[draft(release_script), *release_page(count=99)], RuntimeError(f"HTTP {status}")])
    with pytest.raises(RuntimeError, match=f"HTTP {status}"):
        release_script.existing_draft(api, VERSION, "community", SHA)


@pytest.mark.parametrize("published", ["published", "api-error"])
def test_public_tag_lookup_conflicts_or_errors_stop_draft_discovery(release_script, published):
    response = draft(release_script, draft=False) if published == "published" else RuntimeError("HTTP 403")
    api = discovery_api([], published=response)
    with pytest.raises((ValueError, RuntimeError), match="published release|HTTP 403"):
        release_script.existing_draft(api, VERSION, "community", SHA)
    assert not any(call.args[1].startswith("releases?") for call in api.request.call_args_list)


def test_read_only_preflight_checks_public_conflicts_without_claiming_draft_visibility(release_script, monkeypatch):
    api = discovery_api([])
    monkeypatch.setattr(release_script, "GitHub", lambda: api)
    monkeypatch.setattr(release_script, "source_version", lambda: VERSION)
    monkeypatch.setattr(release_script, "validate_source", Mock())
    monkeypatch.setattr("sys.argv", ["desktop_release.py", "preflight", "--mode", "community", "--source-sha", SHA])
    release_script.main()
    assert [call.args[1] for call in api.request.call_args_list] == [f"git/ref/tags/v{VERSION}", f"releases/tags/v{VERSION}"]


def publisher_fixture(module, tmp_path, monkeypatch, existing=None, assets=None):
    groups = tmp_path / "groups"
    groups.mkdir()
    for group in module.TARGETS:
        (groups / group).mkdir()
        (groups / group / f"{group}.synthetic").write_text("verified final bytes")
    verified = Mock(side_effect=lambda directory, *args: list(directory.iterdir()))
    monkeypatch.setattr(module, "verify_group", verified)
    stored = {"release": copy.deepcopy(existing), "assets": assets or [], "calls": [], "uploads": []}

    def request(method, path, data=None, missing_ok=False):
        stored["calls"].append((method, path, data))
        if path.startswith("git/"):
            return None
        if method == "GET" and path.startswith("releases/tags/"):
            return stored["release"] if stored["release"] and not stored["release"]["draft"] else None
        if method == "GET" and path == "releases?per_page=100&page=1":
            return [stored["release"]] if stored["release"] else []
        if method == "POST":
            assert path == "releases" and data["draft"] is True and data["target_commitish"] == SHA
            stored["release"] = {"id": 42, **data}
            return stored["release"]
        if method == "GET" and path == "releases/42":
            return stored["release"]
        if method == "GET" and path == "releases/42/assets?per_page=100":
            return stored["assets"]
        if method == "DELETE":
            stored["assets"] = [asset for asset in stored["assets"] if str(asset["id"]) != path.split("/")[-1]]
            return None
        raise AssertionError((method, path))

    def upload(command, **kwargs):
        assert verified.call_count == len(module.TARGETS)
        assert command[:4] == ["gh", "release", "upload", f"v{VERSION}"]
        assert "--clobber" not in command and kwargs == {"check": True}
        file = Path(command[4])
        stored["uploads"].append(file.name)
        stored["assets"].append({"id": len(stored["assets"]) + 100, "name": file.name,
                                 "digest": "sha256:" + hashlib.sha256(file.read_bytes()).hexdigest()})

    monkeypatch.setattr(module.subprocess, "run", upload)
    return groups, Mock(request=Mock(side_effect=request)), stored, verified


def test_publisher_creates_only_a_verified_private_draft_and_checks_remote_digests(release_script, tmp_path, monkeypatch):
    groups, api, stored, _ = publisher_fixture(release_script, tmp_path, monkeypatch)
    release_script.prepare_draft(groups, VERSION, "community", SHA, api)
    assert stored["release"]["draft"] is True
    assert len(stored["uploads"]) == len(release_script.TARGETS)
    assert all(method != "PATCH" and not path.startswith("git/") for method, path, _ in stored["calls"] if method != "GET")
    release_script.prepare_draft(groups, VERSION, "community", SHA, api)
    assert len(stored["uploads"]) == len(release_script.TARGETS)
    assert sum(method == "POST" for method, _, _ in stored["calls"]) == 1


def test_matching_draft_retries_can_replace_only_allowlisted_assets(release_script, tmp_path, monkeypatch):
    old = {"id": 99, "name": "desktop-win-x64.synthetic", "digest": "sha256:" + "0" * 64}
    groups, api, stored, _ = publisher_fixture(release_script, tmp_path, monkeypatch, draft(release_script), [old])
    release_script.prepare_draft(groups, VERSION, "community", SHA, api)
    assert ("DELETE", "releases/assets/99", None) in stored["calls"]
    assert not any(method == "POST" for method, _, _ in stored["calls"])


def test_matching_draft_after_tag_lookup_404_is_reused_from_a_later_list_page(release_script, tmp_path, monkeypatch):
    groups, api, stored, _ = publisher_fixture(release_script, tmp_path, monkeypatch, draft(release_script))
    original = api.request.side_effect

    def paginate(method, path, *args, **kwargs):
        if path == "releases?per_page=100&page=1":
            return release_page()
        if path == "releases?per_page=100&page=2":
            return [stored["release"]]
        return original(method, path, *args, **kwargs)

    api.request.side_effect = paginate
    release_script.prepare_draft(groups, VERSION, "community", SHA, api)
    assert stored["release"]["id"] == 42 and stored["release"]["draft"] is True
    assert not any(method == "POST" for method, _, _ in stored["calls"])
    assert len(stored["uploads"]) == len(release_script.TARGETS)
    assert any(call.args[1] == "releases?per_page=100&page=2" for call in api.request.call_args_list)


@pytest.mark.parametrize("error", ["duplicate", "unmatched", "pagination-error"])
def test_draft_listing_failures_never_reach_release_mutations(release_script, tmp_path, monkeypatch, error):
    groups, api, stored, _ = publisher_fixture(release_script, tmp_path, monkeypatch, draft(release_script))
    original = api.request.side_effect

    def conflict_on_second_page(method, path, *args, **kwargs):
        if path == "releases?per_page=100&page=1":
            return release_page() if error == "unmatched" else [stored["release"], *release_page(count=99)]
        if path == "releases?per_page=100&page=2":
            if error == "pagination-error":
                raise RuntimeError("HTTP 500")
            return [draft(release_script, id=43, target_commitish=OTHER_SHA if error == "unmatched" else SHA)]
        return original(method, path, *args, **kwargs)

    api.request.side_effect = conflict_on_second_page
    with pytest.raises((ValueError, RuntimeError)):
        release_script.prepare_draft(groups, VERSION, "community", SHA, api)
    assert not stored["uploads"]
    assert all(method == "GET" for method, _, _ in stored["calls"])


@pytest.mark.parametrize("case", ["published", "wrong-draft", "unknown-assets", "attestation-failed", "missing-target"])
def test_publisher_fails_before_any_release_mutation(release_script, tmp_path, monkeypatch, case):
    current = draft(release_script, draft=False) if case == "published" else draft(release_script)
    if case == "wrong-draft":
        current["target_commitish"] = OTHER_SHA
    assets = [{"name": "unexpected.exe", "id": 9}] if case == "unknown-assets" else []
    groups, api, stored, verifier = publisher_fixture(release_script, tmp_path, monkeypatch, current, assets)
    if case == "attestation-failed":
        verifier.side_effect = ValueError("attestation refused")
    if case == "missing-target":
        directory = groups / "desktop-mac-arm64"
        next(directory.iterdir()).unlink()
        directory.rmdir()
    with pytest.raises(ValueError):
        release_script.prepare_draft(groups, VERSION, "community", SHA, api)
    assert stored["uploads"] == []
    assert all(method == "GET" for method, _, _ in stored["calls"])


def test_publisher_rechecks_draft_state_before_replacing_an_asset(release_script, tmp_path, monkeypatch):
    groups, api, stored, _ = publisher_fixture(release_script, tmp_path, monkeypatch, draft(release_script))
    original = api.request.side_effect

    def publish_during_run(method, path, *args, **kwargs):
        if path == "releases/42":
            stored["release"]["draft"] = False
        return original(method, path, *args, **kwargs)

    api.request.side_effect = publish_during_run
    with pytest.raises(ValueError, match="published"):
        release_script.prepare_draft(groups, VERSION, "community", SHA, api)
    assert not stored["uploads"]
    assert all(method == "GET" for method, _, _ in stored["calls"])


def test_publisher_checks_the_uploaded_bytes_not_just_upload_success(release_script, tmp_path, monkeypatch):
    groups, api, stored, _ = publisher_fixture(release_script, tmp_path, monkeypatch)
    original = api.request.side_effect

    def corrupt_remote_digest(method, path, *args, **kwargs):
        result = original(method, path, *args, **kwargs)
        if path.endswith("/assets?per_page=100") and stored["uploads"]:
            result[0]["digest"] = "sha256:" + "0" * 64
        return result

    api.request.side_effect = corrupt_remote_digest
    with pytest.raises(ValueError, match="Uploaded draft assets"):
        release_script.prepare_draft(groups, VERSION, "community", SHA, api)
    assert stored["release"]["draft"] is True


@pytest.mark.parametrize("status,missing_ok", [(403, True), (500, True), (404, False), (404, True)])
def test_release_api_fails_closed_without_echoing_response_bodies_or_tokens(release_script, monkeypatch, status, missing_ok):
    monkeypatch.setenv("GH_TOKEN", "synthetic-private-token")
    error = urllib.error.HTTPError("https://api.github.com", status, "private-response", {}, None)
    request = Mock(side_effect=error)
    monkeypatch.setattr(release_script.urllib.request, "urlopen", request)
    if status == 404 and missing_ok:
        assert release_script.GitHub().request("GET", "releases/tags/v3.2.1", missing_ok=True) is None
    else:
        with pytest.raises(RuntimeError, match=f"GitHub release request failed \\(HTTP {status}\\)") as failure:
            release_script.GitHub().request("GET", "releases/tags/v3.2.1", missing_ok=missing_ok)
        assert "private" not in str(failure.value)
    outgoing = request.call_args.args[0]
    assert outgoing.full_url.startswith(f"https://api.github.com/repos/{release_script.REPOSITORY}/")
    assert outgoing.headers["Authorization"] == "Bearer synthetic-private-token"
    assert request.call_args.kwargs == {"timeout": 60}
