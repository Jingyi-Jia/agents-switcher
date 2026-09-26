import hashlib
import importlib.util
from pathlib import Path


def test_checksums_cover_update_metadata_and_blockmaps_without_build_configs(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[1] / "desktop" / "scripts" / "checksums.py"
    spec = importlib.util.spec_from_file_location("updater_checksums", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "__file__", str(tmp_path / "scripts" / "checksums.py"))
    output = tmp_path / "release"
    output.mkdir()
    included = [
        "Agent-Switch-1.2.0-win-x64.exe", "Agent-Switch-1.2.0-win-x64.exe.blockmap",
        "Agent-Switch-1.2.0-mac-arm64.zip", "Agent-Switch-1.2.0-linux-x86_64.AppImage",
        "latest.yml", "latest-arm64-mac.yml", "latest-x64-mac.yml", "latest-linux.yml",
    ]
    for name in [*included, "builder-effective-config.yaml", "app-update.yml", "private.txt"]:
        (output / name).write_text("synthetic artifact", encoding="utf-8")
    (output / "unpacked").mkdir()
    module.main()
    lines = (output / f"SHA256SUMS-{module.sys.platform}.txt").read_text().splitlines()
    checksum = hashlib.sha256(b"synthetic artifact").hexdigest()
    assert lines == [f"{checksum}  {name}" for name in sorted(included)]
