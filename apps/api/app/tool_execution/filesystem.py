"""Bounded filesystem primitives, invoked only inside the runtime authorization fence.

Configuration and a local marker form the outer boundary. A ToolScope is the
explicit, separately authorized invocation grant; model text cannot widen it.
No subprocess, shell, network, or arbitrary absolute-path operation is exposed.
"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import re
import stat
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from app.core.errors import DomainError
from app.models.tool_execution import ToolObservation, ToolScope, ToolStep, WorkspaceInfo

TOOLS = ("workspace.list", "workspace.read", "workspace.write", "workspace.report")
MARKER = ".jarvis-workspace.json"
LOCK = ".jarvis-workspace.lock"
MAX_BYTES = 65536
_RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³]|CONIN\$|CONOUT\$)$", re.I)
_ALIAS = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$")
_CREDENTIALS = {
    "credentials",
    "credentials.json",
    "secrets",
    "secrets.json",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
    "id_dsa",
}


def fail(code, message, status=400):
    raise DomainError(code, message, status)


def parts(value: str) -> tuple[str, ...]:
    if value == ".":
        return ()
    if not isinstance(value, str) or not value or len(value) > 240:
        fail("TOOL_PATH_INVALID", "Use a bounded relative workspace path.")
    if (
        "\\" in value
        or any(char in value for char in '<>:"|?*')
        or any(ord(char) < 32 for char in value)
    ):
        fail("TOOL_PATH_INVALID", "Use relative paths with forward slashes and ordinary filenames.")
    segments = value.split("/")
    for item in segments:
        if (
            not item
            or item in {".", ".."}
            or item != item.strip()
            or item.endswith(".")
            or _RESERVED.fullmatch(item.split(".")[0])
        ):
            fail(
                "TOOL_PATH_INVALID",
                "Path traversal, absolute paths and device names are unavailable.",
            )
        lower = item.casefold()
        if (
            item.startswith(".")
            or lower in _CREDENTIALS
            or lower.split(".")[0] in {"credentials", "secrets"}
            or lower.endswith((".pem", ".key", ".p12", ".pfx", ".kdbx"))
        ):
            fail("TOOL_PATH_DENIED", "Hidden files and credential paths are unavailable.", 403)
    return tuple(segments)


def within(path: tuple[str, ...], prefix: tuple[str, ...]):
    if os.name == "nt":
        path, prefix = tuple(map(str.casefold, path)), tuple(map(str.casefold, prefix))
    return path[: len(prefix)] == prefix


def check_stat(info, *, directory=False, internal=False):
    attributes = getattr(info, "st_file_attributes", 0)
    if stat.S_ISLNK(info.st_mode) or attributes & 0x400:
        fail("TOOL_PATH_UNSAFE", "Symbolic links and reparse points are unavailable.", 403)
    if not internal and attributes & 0x6:
        fail("TOOL_PATH_DENIED", "Hidden and system files are unavailable.", 403)
    if directory and not stat.S_ISDIR(info.st_mode):
        fail("TOOL_PATH_UNSAFE", "The workspace parent is not a directory.", 403)
    if not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
        fail("TOOL_PATH_UNSAFE", "Only ordinary files with one link are available.", 403)


def windows_final_path(fd):
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    method = kernel.GetFinalPathNameByHandleW
    method.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    method.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    length = method(msvcrt.get_osfhandle(fd), buffer, len(buffer), 0)
    if not length or length >= len(buffer):
        raise OSError("Cannot resolve workspace file handle")
    return buffer.value.removeprefix("\\\\?\\")


@contextmanager
def windows_directory(path, *, internal=False):
    """Deny directory deletion/replacement while permitting ordinary child writes."""
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    handle = create(str(path), 0x80000000, 3, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), "Cannot lock workspace directory")
    try:
        # The handle prevents replacement while this path-based metadata check runs.
        check_stat(path.lstat(), directory=True, internal=internal)
        yield
    finally:
        close(handle)


@dataclass
class Directory:
    path: Path
    fd: int | None

    def name(self, leaf):
        return str(self.path / leaf) if self.fd is None else leaf

    @property
    def kwargs(self):
        return {} if self.fd is None else {"dir_fd": self.fd}


def open_directory(stack, path, parent=None, leaf=None, *, create=False, internal=False):
    if create:
        try:
            os.mkdir(parent.name(leaf), mode=0o700, **parent.kwargs)
        except FileExistsError:
            pass
    if os.name == "nt":
        stack.enter_context(windows_directory(path, internal=internal))
        return Directory(path, None)
    fd = os.open(
        parent.name(leaf) if parent else str(path),
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        **(parent.kwargs if parent else {}),
    )
    stack.callback(os.close, fd)
    check_stat(os.fstat(fd), directory=True, internal=internal)
    return Directory(path, fd)


@contextmanager
def open_file(directory, leaf, flags, *, internal=False):
    try:
        check_stat(
            os.stat(directory.name(leaf), follow_symlinks=False, **directory.kwargs),
            internal=internal,
        )
    except FileNotFoundError:
        if not flags & os.O_CREAT:
            raise
    fd = os.open(
        directory.name(leaf),
        flags
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_BINARY", 0),
        0o600,
        **directory.kwargs,
    )
    try:
        check_stat(os.fstat(fd), internal=internal)
        if os.name == "nt" and os.path.normcase(windows_final_path(fd)) != os.path.normcase(
            str(directory.path / leaf)
        ):
            fail("TOOL_PATH_UNSAFE", "The file handle escaped its declared workspace path.", 403)
        yield fd
    finally:
        os.close(fd)


def read_bytes(fd, maximum):
    if os.fstat(fd).st_size > maximum:
        fail("TOOL_IO_LIMIT", "The file exceeds the authorized byte limit.", 413)
    output = bytearray()
    while len(output) <= maximum:
        chunk = os.read(fd, min(8192, maximum + 1 - len(output)))
        if not chunk:
            break
        output.extend(chunk)
    if len(output) > maximum:
        fail("TOOL_IO_LIMIT", "The file exceeds the authorized byte limit.", 413)
    return bytes(output)


@contextmanager
def workspace_lock(root):
    with open_file(root, LOCK, os.O_RDWR | os.O_CREAT, internal=True) as fd:
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"0")
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fail("TOOL_WORKSPACE_BUSY", "Another bounded workspace operation is in progress.", 409)
        try:
            yield
        finally:
            if os.name == "nt":
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)


class WorkspaceToolRegistry:
    def __init__(self, workspaces_json: str):
        try:
            configured = json.loads(workspaces_json or "{}")
            if not isinstance(configured, dict) or len(configured) > 16:
                raise ValueError
            if any(
                not isinstance(alias, str)
                or not _ALIAS.fullmatch(alias)
                or not isinstance(root, str)
                for alias, root in configured.items()
            ):
                raise ValueError
        except (ValueError, TypeError):
            fail(
                "TOOL_CONFIG_INVALID",
                "Tool workspace configuration must map up to 16 aliases to marked local directories.",
            )
        self.configured = configured

    @contextmanager
    def access(self, alias):
        configured = self.configured.get(alias)
        if configured is None:
            fail("TOOL_WORKSPACE_UNAVAILABLE", "The workspace alias is not configured.", 404)
        path = Path(configured)
        if (
            not path.is_absolute()
            or configured.startswith(("\\\\", "//"))
            or path == Path(path.anchor)
            or path == Path.home()
            or path in Path.home().parents
        ):
            fail(
                "TOOL_WORKSPACE_UNSAFE",
                "Configure a dedicated absolute local workspace directory.",
                403,
            )
        # Never normalize away a link or a '..' segment before validating ancestors.
        if ".." in path.parts:
            fail("TOOL_WORKSPACE_UNSAFE", "The configured workspace path must be canonical.", 403)
        try:
            with ExitStack() as stack:
                current = open_directory(stack, Path(path.anchor), internal=True)
                for leaf in path.parts[1:]:
                    current = open_directory(
                        stack, current.path / leaf, current, leaf, internal=True
                    )
                try:
                    with open_file(current, MARKER, os.O_RDONLY, internal=True) as fd:
                        marker = json.loads(read_bytes(fd, 8192).decode("utf-8"))
                except FileNotFoundError:
                    fail(
                        "TOOL_WORKSPACE_UNMARKED",
                        "The configured directory requires an operator-created workspace marker.",
                        403,
                    )
                except (ValueError, UnicodeError):
                    fail("TOOL_WORKSPACE_MARKER_INVALID", "The workspace marker is invalid.", 403)
                if (
                    not isinstance(marker, dict)
                    or marker.get("schemaVersion") != "1.0"
                    or marker.get("workspaceId") != alias
                ):
                    fail(
                        "TOOL_WORKSPACE_MARKER_INVALID",
                        "The workspace marker does not match the configured alias.",
                        403,
                    )
                for field, default in (
                    ("readPrefixes", ["inputs"]),
                    ("writePrefixes", ["reports"]),
                ):
                    marker.setdefault(field, default)
                    if (
                        not isinstance(marker[field], list)
                        or len(marker[field]) > 8
                        or any(not isinstance(item, str) for item in marker[field])
                    ):
                        fail(
                            "TOOL_WORKSPACE_MARKER_INVALID",
                            "The workspace marker prefixes are invalid.",
                            403,
                        )
                    for prefix in marker[field]:
                        parts(prefix)
                marker.setdefault("allowedTools", list(TOOLS))
                if not isinstance(marker["allowedTools"], list) or any(
                    tool not in TOOLS for tool in marker["allowedTools"]
                ):
                    fail(
                        "TOOL_WORKSPACE_MARKER_INVALID",
                        "The workspace marker tools are invalid.",
                        403,
                    )
                yield stack, current, marker
        except DomainError:
            raise
        except FileNotFoundError:
            fail("TOOL_PATH_NOT_FOUND", "The requested workspace path does not exist.", 404)
        except OSError:
            fail(
                "TOOL_PATH_UNSAFE",
                "The workspace path is unavailable, linked, locked, or changed during access.",
                403,
            )

    def workspaces(self) -> list[WorkspaceInfo]:
        result = []
        for alias in sorted(self.configured):
            try:
                with self.access(alias) as (_, _, marker):
                    result.append(
                        WorkspaceInfo(
                            workspaceId=alias,
                            displayName=alias,
                            allowedTools=marker["allowedTools"],
                            readPrefixes=marker["readPrefixes"],
                            writePrefixes=marker["writePrefixes"],
                            ready=True,
                        )
                    )
            except DomainError as error:
                result.append(
                    WorkspaceInfo(
                        workspaceId=alias,
                        displayName=alias,
                        allowedTools=[],
                        ready=False,
                        reasonCode=error.code,
                    )
                )
        return result

    @staticmethod
    def scope_against_marker(scope, marker):
        if any(tool not in marker["allowedTools"] for tool in scope.allowedTools):
            fail(
                "TOOL_SCOPE_DENIED",
                "The requested tools exceed the operator's workspace boundary.",
                403,
            )
        for field in ("readPrefixes", "writePrefixes"):
            outer = [parts(prefix) for prefix in marker[field]]
            if any(
                not any(within(parts(prefix), maximum) for maximum in outer)
                for prefix in getattr(scope, field)
            ):
                fail(
                    "TOOL_SCOPE_DENIED",
                    "The requested prefixes exceed the operator's workspace boundary.",
                    403,
                )

    def validate_scope(self, scope: ToolScope):
        with self.access(scope.workspaceId) as (_, _, marker):
            self.scope_against_marker(scope, marker)

    def validate_step(self, step: ToolStep, scope: ToolScope):
        self.validate_scope(scope)
        self.step_against_scope(step, scope)

    @staticmethod
    def step_against_scope(step, scope):
        if step.tool not in scope.allowedTools:
            fail("TOOL_NOT_AUTHORIZED", "This tool is outside the explicit invocation grant.", 403)
        relative = parts(step.path)
        writing = step.tool in {"workspace.write", "workspace.report"}
        prefixes = scope.writePrefixes if writing else scope.readPrefixes
        if not any(within(relative, parts(prefix)) for prefix in prefixes):
            fail("TOOL_PATH_DENIED", "This path is outside the explicit invocation grant.", 403)
        if step.tool != "workspace.list" and not relative:
            fail("TOOL_PATH_INVALID", "Select a file within the workspace.")
        if writing and (
            step.content is None
            or len(step.content.encode("utf-8")) > min(scope.maximumBytes, MAX_BYTES)
        ):
            fail("TOOL_IO_LIMIT", "Write content exceeds the authorized byte limit.", 413)
        if step.tool == "workspace.report" and Path(step.path).suffix.lower() not in {
            ".md",
            ".txt",
            ".json",
        }:
            fail("TOOL_REPORT_FORMAT", "Reports use Markdown, text or JSON files.")
        return relative

    def execute(self, step: ToolStep, scope: ToolScope) -> ToolObservation:
        relative = self.step_against_scope(step, scope)
        writing = step.tool in {"workspace.write", "workspace.report"}
        with self.access(scope.workspaceId) as (stack, root, marker):
            self.scope_against_marker(scope, marker)
            with workspace_lock(root):
                parent = root
                directories = relative if step.tool == "workspace.list" else relative[:-1]
                for leaf in directories:
                    parent = open_directory(stack, parent.path / leaf, parent, leaf, create=writing)
                if step.tool == "workspace.list":
                    entries = []
                    byte_count = 0
                    with os.scandir(parent.fd if parent.fd is not None else parent.path) as listing:
                        for index, entry in enumerate(listing):
                            if index >= 1000:
                                fail(
                                    "TOOL_IO_LIMIT",
                                    "This directory exceeds the bounded listing limit.",
                                    413,
                                )
                            try:
                                parts(entry.name)
                                # Windows DirEntry's cached stat omits the link count.
                                info = os.stat(
                                    parent.name(entry.name),
                                    follow_symlinks=False,
                                    **parent.kwargs,
                                )
                                check_stat(info, directory=stat.S_ISDIR(info.st_mode))
                            except DomainError:
                                continue
                            name = entry.name + ("/" if stat.S_ISDIR(info.st_mode) else "")
                            byte_count += len(name.encode("utf-8")) + 1
                            if byte_count > scope.maximumBytes:
                                fail(
                                    "TOOL_IO_LIMIT",
                                    "The listing exceeds the authorized byte limit.",
                                    413,
                                )
                            entries.append(name)
                            if len(entries) > 100:
                                fail(
                                    "TOOL_IO_LIMIT",
                                    "This directory exceeds 100 visible entries.",
                                    413,
                                )
                    return ToolObservation(
                        tool=step.tool,
                        path=step.path,
                        entries=sorted(entries),
                        byteCount=byte_count,
                    )
                leaf = relative[-1]
                if not writing:
                    with open_file(parent, leaf, os.O_RDONLY) as fd:
                        content = read_bytes(fd, scope.maximumBytes)
                    try:
                        text = content.decode("utf-8")
                        if "\0" in text:
                            raise UnicodeError
                    except UnicodeError:
                        fail("TOOL_CONTENT_NOT_TEXT", "Only bounded UTF-8 text files can be read.")
                    return ToolObservation(
                        tool=step.tool,
                        path=step.path,
                        content=text,
                        contentHash=hashlib.sha256(content).hexdigest(),
                        byteCount=len(content),
                    )
                content = step.content.encode("utf-8")
                digest = hashlib.sha256(content).hexdigest()
                try:
                    with open_file(parent, leaf, os.O_RDONLY) as fd:
                        existing_hash = hashlib.sha256(
                            read_bytes(fd, scope.maximumBytes)
                        ).hexdigest()
                except FileNotFoundError:
                    existing_hash = None
                if existing_hash == digest:
                    return ToolObservation(
                        tool=step.tool,
                        path=step.path,
                        contentHash=digest,
                        byteCount=len(content),
                        written=False,
                    )
                if existing_hash != step.expectedContentHash:
                    fail(
                        "TOOL_WRITE_CONFLICT",
                        "The destination differs from the reviewed content hash. Review it before replacing it.",
                        409,
                    )
                temporary = f".jarvis-write-{uuid4().hex}.tmp"
                try:
                    with open_file(
                        parent, temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, internal=True
                    ) as fd:
                        position = 0
                        while position < len(content):
                            position += os.write(fd, content[position:])
                        os.fsync(fd)
                    if parent.fd is None:
                        os.replace(parent.name(temporary), parent.name(leaf))
                    else:
                        os.replace(temporary, leaf, src_dir_fd=parent.fd, dst_dir_fd=parent.fd)
                        os.fsync(parent.fd)
                finally:
                    try:
                        os.unlink(parent.name(temporary), **parent.kwargs)
                    except OSError as error:
                        if error.errno != errno.ENOENT:
                            raise
                return ToolObservation(
                    tool=step.tool,
                    path=step.path,
                    contentHash=digest,
                    byteCount=len(content),
                    written=True,
                )
