"""Hold the approved executable image immutable throughout a native read."""

import ctypes
import os
import stat
import sys
from contextlib import ExitStack, contextmanager
from hashlib import sha256
from pathlib import Path

from app.core.errors import DomainError
from app.tool_execution.filesystem import open_directory, windows_final_path

MAX_IMAGE = 64 * 1024 * 1024


def image_bytes(fd, expected_hash):
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & 0x400
        or info.st_size > MAX_IMAGE
    ):
        raise OSError("Unsafe executable image")
    chunks, size = [], 0
    while chunk := os.read(fd, 65536):
        size += len(chunk)
        if size > MAX_IMAGE:
            raise OSError("Executable image exceeds limit")
        chunks.append(chunk)
    content = b"".join(chunks)
    if sha256(content).hexdigest() != expected_hash:
        raise OSError("Executable image changed")
    return content


@contextmanager
def pinned_image(path, expected_hash):
    """Windows denies writes/replacement; Linux executes a sealed verified snapshot.

    A content pin alone followed by pathname execution has a check/use race. Keep
    Windows ancestors and executable locked until process cleanup, or pass a sealed
    Linux memfd to exec. Unsupported platforms fail closed. This owns no authority.
    """
    try:
        with ExitStack() as stack:
            path = Path(path)
            if os.name == "nt":
                import msvcrt
                from ctypes import wintypes

                for parent in reversed(path.parents):
                    open_directory(stack, parent, internal=True)
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
                handle = create(str(path), 0x80000000, 1, None, 3, 0x00200000, None)
                if handle == ctypes.c_void_p(-1).value:
                    raise OSError(ctypes.get_last_error(), "Cannot lock Git image")
                try:
                    fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
                except BaseException:
                    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
                    kernel.CloseHandle(handle)
                    raise
                stack.callback(os.close, fd)
                if os.path.normcase(windows_final_path(fd)) != os.path.normcase(str(path)):
                    raise OSError("Executable handle changed path")
                image_bytes(fd, expected_hash)
                launch = {"executable": str(path)}
            elif sys.platform == "linux" and hasattr(os, "memfd_create"):
                import fcntl

                source = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                stack.callback(os.close, source)
                content = image_bytes(source, expected_hash)
                # Seals prohibit every write, truncate and growth after verification.
                fd = os.memfd_create("jarvis-approved-git", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
                stack.callback(os.close, fd)
                position = 0
                while position < len(content):
                    position += os.write(fd, content[position:])
                fcntl.fcntl(
                    fd,
                    fcntl.F_ADD_SEALS,
                    fcntl.F_SEAL_WRITE
                    | fcntl.F_SEAL_GROW
                    | fcntl.F_SEAL_SHRINK
                    | fcntl.F_SEAL_SEAL,
                )
                launch = {"executable": f"/proc/self/fd/{fd}", "pass_fds": (fd,)}
            else:
                raise OSError("Immutable native execution is unavailable")
            yield launch
    except OSError:
        raise DomainError(
            "SELF_BUILD_GIT_TOOL_INVALID",
            "The approved executable image cannot be held immutable.",
            409,
        ) from None
