"""Write checksums for distributable artifacts, never unpacked build trees."""

import hashlib
import sys
from pathlib import Path


def main() -> None:
    output = Path(__file__).resolve().parents[1] / "release"
    artifacts = sorted(
        path for path in output.iterdir()
        if path.is_file() and path.name.endswith((".dmg", ".zip", ".exe", ".AppImage", ".deb", ".tar.gz"))
    )
    if not artifacts:
        raise SystemExit("No desktop artifacts were produced")
    lines = []
    for path in artifacts:
        with path.open("rb") as artifact:
            checksum = hashlib.file_digest(artifact, "sha256").hexdigest()
        lines.append(f"{checksum}  {path.name}\n")
    (output / f"SHA256SUMS-{sys.platform}.txt").write_text("".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
