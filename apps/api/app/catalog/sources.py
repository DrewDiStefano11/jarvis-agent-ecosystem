"""Explicit acquisition only. No upstream code, hooks, links or installs run."""

import io
import re
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryFile
from urllib.request import urlopen

from app.core.errors import DomainError
from app.models.catalog import CatalogKind, RawDefinition, SourceSnapshot


@dataclass(frozen=True)
class SourceAdapter:
    repository: str
    pinned_commit: str
    patterns: tuple[tuple[str, CatalogKind], ...]

    def classify(self, path: str) -> CatalogKind | None:
        return next((kind for pattern, kind in self.patterns if re.fullmatch(pattern, path)), None)


ADAPTERS = {
    "wshobson-agents": SourceAdapter(
        "wshobson/agents",
        "a30778f8c4e6b0a87567941b7cca4f534bf642b6",
        (
            (r"plugins/[^/]+/agents/[^/]+\.md", "agent"),
            (r"plugins/[^/]+/skills/[^/]+/SKILL\.md", "skill"),
        ),
    ),
    "voltagent-skills": SourceAdapter(
        "VoltAgent/awesome-agent-skills",
        "e4f7a502a78253550890e8b356d43f50192415ae",
        ((r"README\.md", "discovery"),),
    ),
}
SOURCES = {key: (adapter.repository, adapter.pinned_commit) for key, adapter in ADAPTERS.items()}


MAX_ARCHIVE = 30_000_000


def acquire(source: str, commit: str, local_tree: Path | None = None) -> SourceSnapshot:
    """Read exact committed bytes, including for a dirty local checkout.

    Local paths are CLI-only. HTTP callers cannot read arbitrary server paths.
    Network acquisition permits only the source registry and a full commit SHA.
    """
    if source not in SOURCES or not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise DomainError(
            "CATALOG_SOURCE_INVALID", "Known source and full commit SHA required.", 422
        )
    repository = SOURCES[source][0]
    try:
        if local_tree:
            with TemporaryFile() as archive_output:
                subprocess.run(
                    ["git", "-C", str(local_tree.resolve()), "archive", "--format=zip", commit],
                    stdout=archive_output,
                    stderr=subprocess.PIPE,
                    check=True,
                    timeout=60,
                )
                archive_output.seek(0)
                data = archive_output.read(MAX_ARCHIVE + 1)
            strip_root = False
        else:
            with urlopen(
                f"https://codeload.github.com/{repository}/zip/{commit}", timeout=30
            ) as response:
                data = response.read(MAX_ARCHIVE + 1)
            strip_root = True
        if len(data) > MAX_ARCHIVE:
            raise ValueError("Source archive exceeds limit")
        files = {}
        total_size = 0
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if len(archive.infolist()) > 20_000:
                raise ValueError("Too many source files")
            for info in archive.infolist():
                path = info.filename.split("/", 1)[-1] if strip_root else info.filename
                is_definition = ADAPTERS[source].classify(path)
                is_license = Path(path).name.upper() in {
                    "LICENSE",
                    "LICENSE.MD",
                    "LICENSE.TXT",
                    "COPYING",
                    "NOTICE",
                }
                if is_license and path != "LICENSE":
                    raise ValueError("Nested or additional licensing requires independent review")
                if is_license or is_definition:
                    size_limit = 1_000_000 if source == "voltagent-skills" else 200_000
                    if (
                        info.file_size > size_limit
                        or info.external_attr >> 16 & 0o170000 == 0o120000
                    ):
                        raise ValueError("Oversized or symbolic source definition")
                    total_size += info.file_size
                    if total_size > MAX_ARCHIVE:
                        raise ValueError("Expanded definitions exceed source limit")
                    files[path] = archive.read(info).decode("utf-8")
        return parse_snapshot(source, repository, commit, files)
    except (OSError, ValueError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        raise DomainError(
            "CATALOG_SOURCE_UNAVAILABLE", "Pinned source could not be read or validated.", 422
        ) from exc


def parse_snapshot(
    source: str, repository: str, commit: str, files: dict[str, str]
) -> SourceSnapshot:
    license_text = files.get("LICENSE", "License unavailable")
    # Repository MIT does not license the bodies of third-party linked skills.
    license_name = (
        "MIT"
        if "MIT License" in license_text and "Permission is hereby granted" in license_text
        else "unknown"
    )
    definitions = []
    for path, text in sorted(files.items()):
        if path == "LICENSE":
            continue
        kind = ADAPTERS[source].classify(path)
        if kind is None:
            continue
        definitions.append(RawDefinition(kind=kind, path=path, text=text))
    return SourceSnapshot(
        provider=source,
        repository=repository,
        commit=commit,
        license=license_name,
        license_text=license_text,
        definitions=definitions,
    )
