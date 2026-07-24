from __future__ import annotations

import logging
import os
import shutil
import stat
import threading
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from uuid import uuid4

from app.filesystem.config import SandboxConfiguration, canonical_policy_key
from app.filesystem.errors import (
    FilesystemSandboxError,
    InvalidSandboxPathError,
    SandboxFileTooLargeError,
    SandboxOperationError,
    SandboxPathExistsError,
    SandboxPathNotFoundError,
    SandboxReadOnlyError,
    SandboxViolationError,
)
from app.filesystem.protocols import FileMetadata

_INVALID_FILENAME_CHARACTERS = frozenset('<>:"|?*')
_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)
_LOCKS: dict[Path, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


class LocalFilesystemSandbox:
    """A root-confined, symlink-rejecting local filesystem provider."""

    def __init__(
        self,
        configuration: SandboxConfiguration,
        logger: logging.Logger | None = None,
    ) -> None:
        self.configuration = configuration
        self._logger = logger or logging.getLogger("jarvis.filesystem")
        configured_root = configuration.root
        if configured_root.is_symlink() or self._is_reparse_point(configured_root):
            raise SandboxViolationError(".", "the configured root cannot be a symbolic link")
        if configuration.read_only and not configured_root.is_dir():
            raise ValueError("a read-only sandbox root must already exist and be a directory")
        configured_root.mkdir(parents=True, exist_ok=True)
        self._root = configured_root.resolve(strict=True)
        if not self._root.is_dir():
            raise ValueError("sandbox root must be a directory")
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(self._root, threading.RLock())
        self._temporary_root = self._root.joinpath(*configuration.temporary_directory)
        if not configuration.read_only:
            self._temporary_root.mkdir(parents=True, exist_ok=True)
            self._assert_no_links(self._temporary_root)

    @property
    def root(self) -> Path:
        return self._root

    @staticmethod
    def _is_reparse_point(path: Path) -> bool:
        try:
            attributes = getattr(path.lstat(), "st_file_attributes", 0)
        except FileNotFoundError:
            return False
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        return bool(reparse_flag and attributes & reparse_flag)

    @classmethod
    def _is_link(cls, path: Path) -> bool:
        return path.is_symlink() or cls._is_reparse_point(path)

    @staticmethod
    def _invalid_filename_reason(part: str) -> str | None:
        if any(character in _INVALID_FILENAME_CHARACTERS for character in part):
            return "filename contains a platform-unsafe character"
        if any(ord(character) < 32 for character in part):
            return "filename contains a control character"
        if part.endswith((" ", ".")):
            return "filename cannot end with a space or dot"
        if part.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
            return "filename is reserved by the operating system"
        return None

    def normalize_path(self, path: str | Path, *, allow_root: bool = False) -> str:
        raw = os.fspath(path)
        if "\x00" in raw:
            self._violation(path, "path contains a null byte", invalid=True)
        portable = raw.replace("\\", "/")
        windows_path = PureWindowsPath(raw)
        if (
            PurePosixPath(portable).is_absolute()
            or windows_path.is_absolute()
            or windows_path.drive
        ):
            self._violation(path, "absolute paths and drive-qualified paths are not allowed")
        if portable in {"", "."}:
            if allow_root:
                return "."
            self._violation(path, "a file or non-root directory path is required", invalid=True)
        parts = portable.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            self._violation(
                path,
                "empty, dot, and parent path segments are not allowed",
                invalid=True,
            )
        for part in parts:
            if reason := self._invalid_filename_reason(part):
                self._violation(path, reason, invalid=True)
        normalized = "/".join(parts)
        self._assert_public_path(normalized, parts)
        return normalized

    def _assert_public_path(self, path: str, parts: list[str]) -> None:
        if self._is_protected(tuple(parts)):
            self._violation(path, "path is inside a restricted directory")

    def _is_protected(self, parts: tuple[str, ...]) -> bool:
        canonical_parts = canonical_policy_key(parts)
        protected = (
            *self.configuration.restricted_directories,
            self.configuration.temporary_directory,
        )
        return any(canonical_parts[: len(prefix)] == prefix for prefix in protected)

    def _violation(self, path: object, reason: str, *, invalid: bool = False) -> None:
        self._logger.warning(
            "filesystem_path_rejected",
            extra={"sandbox_event": "path_rejected", "sandbox_path": str(path), "reason": reason},
        )
        if invalid:
            raise InvalidSandboxPathError(path, reason)
        raise SandboxViolationError(path, reason)

    def _assert_no_links(self, path: Path) -> None:
        relative = path.relative_to(self._root)
        current = self._root
        if self._is_link(current):
            self._violation(".", "sandbox root became a symbolic link")
        for part in relative.parts:
            current /= part
            if not current.exists() and not current.is_symlink():
                break
            if self._is_link(current):
                display = current.relative_to(self._root).as_posix()
                self._violation(display, "symbolic links and filesystem reparse points are denied")

    def _resolve(self, path: str | Path, *, allow_root: bool = False) -> tuple[str, Path]:
        normalized = self.normalize_path(path, allow_root=allow_root)
        candidate = self._root if normalized == "." else self._root.joinpath(*normalized.split("/"))
        self._assert_no_links(candidate)
        resolved = candidate.resolve(strict=False)
        try:
            common = Path(os.path.commonpath((self._root, resolved)))
        except ValueError:
            self._violation(path, "resolved path is outside the sandbox root")
        if common != self._root:
            self._violation(path, "resolved path is outside the sandbox root")
        return normalized, candidate

    def _assert_extension(self, normalized: str) -> None:
        allowed = self.configuration.allowed_extensions
        if allowed is not None and Path(normalized).suffix.lower() not in allowed:
            self._violation(
                normalized,
                f"file extension is not allowed; configured extensions are {sorted(allowed)}",
            )

    def _assert_mutable(self, operation: str) -> None:
        if self.configuration.read_only:
            self._logger.warning(
                "filesystem_write_rejected",
                extra={"sandbox_event": "write_rejected", "operation": operation},
            )
            raise SandboxReadOnlyError(operation)

    def _assert_size(self, path: object, size: int) -> None:
        maximum = self.configuration.maximum_file_size
        if size > maximum:
            self._logger.warning(
                "filesystem_size_rejected",
                extra={
                    "sandbox_event": "size_rejected",
                    "sandbox_path": str(path),
                    "size": size,
                    "maximum_size": maximum,
                },
            )
            raise SandboxFileTooLargeError(path, size, maximum)

    def _reject_implicit_overwrite(self, path: str) -> None:
        self._logger.warning(
            "filesystem_overwrite_rejected",
            extra={"sandbox_event": "overwrite_rejected", "sandbox_path": path},
        )
        raise SandboxPathExistsError(path)

    @staticmethod
    def _metadata(normalized: str, path: Path) -> FileMetadata:
        details = path.stat(follow_symlinks=False)
        return FileMetadata(
            path=normalized,
            size=details.st_size,
            created_at=datetime.fromtimestamp(details.st_ctime, UTC),
            modified_at=datetime.fromtimestamp(details.st_mtime, UTC),
            is_file=stat.S_ISREG(details.st_mode),
            is_directory=stat.S_ISDIR(details.st_mode),
        )

    def _require_existing(self, normalized: str, path: Path) -> None:
        if not path.exists():
            raise SandboxPathNotFoundError(normalized)

    def _translate_os_error(self, operation: str, path: object, error: OSError) -> None:
        if isinstance(error, FileNotFoundError):
            raise SandboxPathNotFoundError(path) from error
        if isinstance(error, FileExistsError):
            raise SandboxPathExistsError(path) from error
        if isinstance(error, PermissionError):
            reason = "the operating system denied permission"
        elif getattr(error, "winerror", None) == 112 or error.errno == 28:
            reason = "the filesystem is full"
        elif error.errno == 30:
            reason = "the host filesystem is read-only"
        else:
            reason = error.strerror or type(error).__name__
        self._logger.exception(
            "filesystem_operation_failed",
            extra={
                "sandbox_event": "operation_failed",
                "operation": operation,
                "sandbox_path": str(path),
            },
        )
        raise SandboxOperationError(operation, path, reason) from error

    def _log(self, event: str, path: str, **details: object) -> None:
        self._logger.info(
            f"filesystem_{event}",
            extra={"sandbox_event": event, "sandbox_path": path, **details},
        )

    def exists(self, path: str | Path) -> bool:
        with self._lock:
            _, resolved = self._resolve(path, allow_root=True)
            return resolved.exists()

    def metadata(self, path: str | Path) -> FileMetadata:
        with self._lock:
            normalized, resolved = self._resolve(path, allow_root=True)
            self._require_existing(normalized, resolved)
            return self._metadata(normalized, resolved)

    def read_file(self, path: str | Path) -> bytes:
        with self._lock:
            normalized, resolved = self._resolve(path)
            self._assert_extension(normalized)
            self._require_existing(normalized, resolved)
            try:
                flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(resolved, flags)
                with os.fdopen(descriptor, "rb") as handle:
                    details = os.fstat(handle.fileno())
                    if not stat.S_ISREG(details.st_mode):
                        raise InvalidSandboxPathError(normalized, "path is not a regular file")
                    self._assert_size(normalized, details.st_size)
                    data = handle.read(self.configuration.maximum_file_size + 1)
                self._assert_size(normalized, len(data))
            except FilesystemSandboxError:
                raise
            except OSError as error:
                self._translate_os_error("read", normalized, error)
            self._log("read", normalized, size=len(data))
            return data

    def _create_parents(self, parent: Path) -> None:
        relative = parent.relative_to(self._root)
        current = self._root
        for part in relative.parts:
            current /= part
            if current.exists():
                if self._is_link(current):
                    self._violation(
                        current.relative_to(self._root).as_posix(),
                        "symbolic links and filesystem reparse points are denied",
                    )
                if not current.is_dir():
                    raise InvalidSandboxPathError(
                        current.relative_to(self._root).as_posix(),
                        "parent path is not a directory",
                    )
            else:
                current.mkdir()

    def _stage_bytes(self, data: bytes) -> Path:
        stage = self._temporary_root / f"{uuid4().hex}.tmp"
        descriptor = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            stage.unlink(missing_ok=True)
            raise
        return stage

    def write_file(
        self,
        path: str | Path,
        data: bytes,
        *,
        overwrite: bool = False,
        create_parents: bool = False,
    ) -> FileMetadata:
        if not isinstance(data, bytes):
            raise TypeError("sandbox file data must be bytes")
        self._assert_mutable("write file")
        self._assert_size(path, len(data))
        with self._lock:
            normalized, destination = self._resolve(path)
            self._assert_extension(normalized)
            if create_parents:
                self._create_parents(destination.parent)
            elif not destination.parent.is_dir():
                raise SandboxPathNotFoundError(Path(normalized).parent.as_posix())
            destination_existed = destination.exists()
            if destination_existed and destination.is_dir():
                raise InvalidSandboxPathError(normalized, "path is a directory")
            if destination_existed and not overwrite:
                self._reject_implicit_overwrite(normalized)
            stage: Path | None = None
            try:
                stage = self._stage_bytes(data)
                if overwrite:
                    os.replace(stage, destination)
                    stage = None
                else:
                    os.link(stage, destination, follow_symlinks=False)
                result = self._metadata(normalized, destination)
            except FilesystemSandboxError:
                raise
            except OSError as error:
                self._translate_os_error("write", normalized, error)
            finally:
                if stage is not None:
                    stage.unlink(missing_ok=True)
            self._log(
                "modified" if destination_existed else "created",
                normalized,
                size=len(data),
            )
            return result

    def append_file(
        self, path: str | Path, data: bytes, *, create_parents: bool = False
    ) -> FileMetadata:
        if not isinstance(data, bytes):
            raise TypeError("sandbox file data must be bytes")
        self._assert_mutable("append file")
        with self._lock:
            normalized, destination = self._resolve(path)
            self._assert_extension(normalized)
            if not destination.exists():
                return self.write_file(
                    normalized, data, create_parents=create_parents, overwrite=False
                )
            try:
                flags = (
                    os.O_WRONLY
                    | os.O_APPEND
                    | getattr(os, "O_BINARY", 0)
                    | getattr(os, "O_NOFOLLOW", 0)
                )
                descriptor = os.open(destination, flags)
                with os.fdopen(descriptor, "ab") as handle:
                    details = os.fstat(handle.fileno())
                    if not stat.S_ISREG(details.st_mode):
                        raise InvalidSandboxPathError(normalized, "path is not a regular file")
                    self._assert_size(normalized, details.st_size + len(data))
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                result = self._metadata(normalized, destination)
            except FilesystemSandboxError:
                raise
            except OSError as error:
                self._translate_os_error("append", normalized, error)
            self._log("modified", normalized, size=result.size, operation="append")
            return result

    def create_directory(self, path: str | Path, *, parents: bool = False) -> FileMetadata:
        self._assert_mutable("create directory")
        with self._lock:
            normalized, destination = self._resolve(path)
            try:
                if parents:
                    self._create_parents(destination)
                else:
                    destination.mkdir()
                result = self._metadata(normalized, destination)
            except FilesystemSandboxError:
                raise
            except OSError as error:
                self._translate_os_error("create directory", normalized, error)
            self._log("created", normalized, kind="directory")
            return result

    def delete_file(self, path: str | Path) -> None:
        self._assert_mutable("delete file")
        with self._lock:
            normalized, resolved = self._resolve(path)
            self._require_existing(normalized, resolved)
            if not resolved.is_file():
                raise InvalidSandboxPathError(normalized, "path is not a regular file")
            try:
                resolved.unlink()
            except OSError as error:
                self._translate_os_error("delete file", normalized, error)
            self._log("deleted", normalized, kind="file")

    def _assert_tree_has_no_links(self, root: Path) -> None:
        for current_root, directories, files in os.walk(root, followlinks=False):
            current = Path(current_root)
            for name in (*directories, *files):
                child = current / name
                if self._is_link(child):
                    relative = child.relative_to(self._root).as_posix()
                    self._violation(relative, "recursive deletion cannot cross a symbolic link")
        self._assert_no_links(root)

    def delete_directory(self, path: str | Path, *, recursive: bool = False) -> None:
        self._assert_mutable("delete directory")
        with self._lock:
            normalized, resolved = self._resolve(path)
            self._require_existing(normalized, resolved)
            if not resolved.is_dir():
                raise InvalidSandboxPathError(normalized, "path is not a directory")
            try:
                if recursive:
                    self._assert_tree_has_no_links(resolved)
                    shutil.rmtree(resolved)
                else:
                    resolved.rmdir()
            except FilesystemSandboxError:
                raise
            except OSError as error:
                self._translate_os_error("delete directory", normalized, error)
            self._log("deleted", normalized, kind="directory", recursive=recursive)

    def move(
        self, source: str | Path, destination: str | Path, *, overwrite: bool = False
    ) -> FileMetadata:
        self._assert_mutable("move")
        with self._lock:
            source_name, source_path = self._resolve(source)
            destination_name, destination_path = self._resolve(destination)
            self._require_existing(source_name, source_path)
            if source_path.is_file():
                self._assert_extension(source_name)
                self._assert_extension(destination_name)
            if destination_path.exists() and not overwrite:
                self._reject_implicit_overwrite(destination_name)
            if not destination_path.parent.is_dir():
                raise SandboxPathNotFoundError(Path(destination_name).parent.as_posix())
            try:
                if overwrite:
                    os.replace(source_path, destination_path)
                else:
                    os.rename(source_path, destination_path)
                result = self._metadata(destination_name, destination_path)
            except OSError as error:
                self._translate_os_error("move", source_name, error)
            self._log("moved", source_name, destination=destination_name, overwrite=overwrite)
            return result

    def rename(self, source: str | Path, new_name: str, *, overwrite: bool = False) -> FileMetadata:
        normalized_source = self.normalize_path(source)
        if "/" in new_name or "\\" in new_name:
            raise InvalidSandboxPathError(new_name, "new name must be one filename")
        destination = (PurePosixPath(normalized_source).parent / new_name).as_posix()
        return self.move(normalized_source, destination, overwrite=overwrite)

    def copy(
        self, source: str | Path, destination: str | Path, *, overwrite: bool = False
    ) -> FileMetadata:
        with self._lock:
            source_name, source_path = self._resolve(source)
            self._require_existing(source_name, source_path)
            if not source_path.is_file():
                raise InvalidSandboxPathError(source_name, "only regular files can be copied")
            data = self.read_file(source_name)
            result = self.write_file(destination, data, overwrite=overwrite)
            self._log("copied", source_name, destination=result.path, overwrite=overwrite)
            return result

    def list_directory(self, path: str | Path = ".") -> list[FileMetadata]:
        with self._lock:
            normalized, resolved = self._resolve(path, allow_root=True)
            self._require_existing(normalized, resolved)
            if not resolved.is_dir():
                raise InvalidSandboxPathError(normalized, "path is not a directory")
            try:
                entries: list[FileMetadata] = []
                for child in resolved.iterdir():
                    relative = child.relative_to(self._root).as_posix()
                    if self._is_protected(tuple(relative.split("/"))):
                        continue
                    if self._is_link(child):
                        self._violation(
                            relative,
                            "directory contains a symbolic link or filesystem reparse point",
                        )
                    entries.append(self._metadata(relative, child))
            except FilesystemSandboxError:
                raise
            except OSError as error:
                self._translate_os_error("list directory", normalized, error)
            return sorted(entries, key=lambda entry: entry.path)
