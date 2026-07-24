from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath


def _relative_parts(value: str, field_name: str) -> tuple[str, ...]:
    raw = value.strip().replace("\\", "/")
    windows_path = PureWindowsPath(raw)
    if (
        not raw
        or PurePosixPath(raw).is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
    ):
        raise ValueError(f"{field_name} must be a non-empty relative sandbox path")
    parts = tuple(raw.split("/"))
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"{field_name} must not contain empty, dot, or parent segments")
    return parts


@dataclass(frozen=True, slots=True)
class SandboxConfiguration:
    root: Path
    maximum_file_size: int
    allowed_extensions: frozenset[str] | None
    restricted_directories: tuple[tuple[str, ...], ...]
    temporary_directory: tuple[str, ...]
    read_only: bool

    @classmethod
    def build(
        cls,
        *,
        root: Path,
        maximum_file_size: int,
        allowed_extensions: str,
        restricted_directories: str,
        temporary_directory: str,
        read_only: bool,
    ) -> SandboxConfiguration:
        if maximum_file_size < 1:
            raise ValueError("sandbox maximum file size must be at least one byte")

        extensions = frozenset(
            extension.strip().lower()
            if extension.strip().startswith(".")
            else f".{extension.strip().lower()}"
            for extension in allowed_extensions.split(",")
            if extension.strip()
        )
        if any(extension == "." for extension in extensions):
            raise ValueError("sandbox allowed extensions must include a value after the dot")

        restrictions = tuple(
            _relative_parts(value, "sandbox restricted directory")
            for value in restricted_directories.split(",")
            if value.strip()
        )
        temporary_parts = _relative_parts(temporary_directory, "sandbox temporary directory")
        if any(
            temporary_parts[: len(restriction)] == restriction
            or restriction[: len(temporary_parts)] == temporary_parts
            for restriction in restrictions
        ):
            raise ValueError(
                "sandbox temporary directory and restricted directories must not overlap"
            )

        return cls(
            root=root.expanduser().absolute(),
            maximum_file_size=maximum_file_size,
            allowed_extensions=extensions or None,
            restricted_directories=restrictions,
            temporary_directory=temporary_parts,
            read_only=read_only,
        )
