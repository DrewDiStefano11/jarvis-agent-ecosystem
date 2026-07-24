from __future__ import annotations

from app.core.errors import DomainError


class FilesystemSandboxError(DomainError):
    """Base error for deterministic sandbox failures."""


class InvalidSandboxPathError(FilesystemSandboxError):
    def __init__(self, path: object, reason: str) -> None:
        super().__init__(
            "FILESYSTEM_INVALID_PATH",
            f"Invalid sandbox path {path!r}: {reason}.",
            400,
        )


class SandboxViolationError(FilesystemSandboxError):
    def __init__(self, path: object, reason: str) -> None:
        super().__init__(
            "FILESYSTEM_SANDBOX_VIOLATION",
            f"Sandbox access denied for {path!r}: {reason}.",
            403,
        )


class SandboxReadOnlyError(FilesystemSandboxError):
    def __init__(self, operation: str) -> None:
        super().__init__(
            "FILESYSTEM_READ_ONLY",
            f"Cannot {operation}: the filesystem sandbox is read-only.",
            403,
        )


class SandboxPathNotFoundError(FilesystemSandboxError):
    def __init__(self, path: object) -> None:
        super().__init__(
            "FILESYSTEM_PATH_NOT_FOUND",
            f"Sandbox path {path!r} does not exist.",
            404,
        )


class SandboxPathExistsError(FilesystemSandboxError):
    def __init__(self, path: object) -> None:
        super().__init__(
            "FILESYSTEM_PATH_EXISTS",
            f"Sandbox path {path!r} already exists; explicit overwrite authorization is required.",
            409,
        )


class SandboxFileTooLargeError(FilesystemSandboxError):
    def __init__(self, path: object, size: int, maximum: int) -> None:
        super().__init__(
            "FILESYSTEM_FILE_TOO_LARGE",
            f"Sandbox file {path!r} is {size} bytes; the configured maximum is {maximum} bytes.",
            413,
        )


class SandboxOperationError(FilesystemSandboxError):
    def __init__(self, operation: str, path: object, reason: str) -> None:
        super().__init__(
            "FILESYSTEM_OPERATION_FAILED",
            f"Could not {operation} sandbox path {path!r}: {reason}.",
            500,
        )
