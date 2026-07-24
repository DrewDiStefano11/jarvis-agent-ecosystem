from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class FileMetadata:
    path: str
    size: int
    created_at: datetime
    modified_at: datetime
    is_file: bool
    is_directory: bool


class FilesystemSandbox(Protocol):
    @property
    def root(self) -> Path: ...

    def normalize_path(self, path: str | Path, *, allow_root: bool = False) -> str: ...
    def read_file(self, path: str | Path) -> bytes: ...
    def write_file(
        self,
        path: str | Path,
        data: bytes,
        *,
        overwrite: bool = False,
        create_parents: bool = False,
    ) -> FileMetadata: ...
    def append_file(
        self, path: str | Path, data: bytes, *, create_parents: bool = False
    ) -> FileMetadata: ...
    def create_directory(self, path: str | Path, *, parents: bool = False) -> FileMetadata: ...
    def delete_file(self, path: str | Path) -> None: ...
    def delete_directory(self, path: str | Path, *, recursive: bool = False) -> None: ...
    def move(
        self, source: str | Path, destination: str | Path, *, overwrite: bool = False
    ) -> FileMetadata: ...
    def rename(
        self, source: str | Path, new_name: str, *, overwrite: bool = False
    ) -> FileMetadata: ...
    def copy(
        self, source: str | Path, destination: str | Path, *, overwrite: bool = False
    ) -> FileMetadata: ...
    def list_directory(self, path: str | Path = ".") -> list[FileMetadata]: ...
    def exists(self, path: str | Path) -> bool: ...
    def metadata(self, path: str | Path) -> FileMetadata: ...
