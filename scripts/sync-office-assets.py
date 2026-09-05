"""Fetch pinned, integrity-checked original office artwork; safe to rerun offline."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def checksum(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def main() -> None:
    manifest = json.loads((ROOT / "scripts/office-assets.json").read_text())
    public = ROOT / "apps/web/public"
    for asset in manifest["assets"]:
        destination = ROOT / "apps/web" / asset["destination"]
        if not destination.resolve().is_relative_to(public.resolve()):
            raise ValueError("Office asset destination escaped public directory")
        expected = asset["sha256"]
        if destination.is_file() and checksum(destination) == expected:
            print(f"Office asset verified: {destination.name}")
            continue
        url = f"https://raw.githubusercontent.com/{manifest['repository']}/{manifest['commit']}/{asset['source']}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with (
                urlopen(url, timeout=120) as response,
                tempfile.NamedTemporaryFile(
                    dir=destination.parent, delete=False
                ) as output,
            ):
                temporary = Path(output.name)
                digest = hashlib.sha256()
                while chunk := response.read(1024 * 1024):
                    digest.update(chunk)
                    output.write(chunk)
            if digest.hexdigest() != expected:
                raise ValueError(f"Office asset checksum mismatch: {destination.name}")
            temporary.replace(destination)
            print(f"Office asset installed: {destination.name}")
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
