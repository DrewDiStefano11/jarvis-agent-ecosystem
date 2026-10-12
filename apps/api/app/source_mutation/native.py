"""Windows file-handle rename primitives. Never replace a destination by name."""

import ctypes
import os
from contextlib import contextmanager
from ctypes import wintypes

from app.tool_execution.filesystem import check_stat, windows_final_path, windows_native_path


@contextmanager
def read_pin(path):
    """Retain a native read pin without CRT limits, including extended local paths."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(windows_native_path(path), 0x80000000, 1, None, 3, 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), "Cannot retain source read pin")
    try:
        kernel.GetFinalPathNameByHandleW.argtypes = [
            wintypes.HANDLE,
            wintypes.LPWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        kernel.GetFinalPathNameByHandleW.restype = wintypes.DWORD
        buffer = ctypes.create_unicode_buffer(32768)
        length = kernel.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
        if (
            not length
            or length >= len(buffer)
            or os.path.normcase(buffer.value.removeprefix("\\\\?\\")) != os.path.normcase(str(path))
        ):
            raise OSError("Source read pin escaped its namespace")
        check_stat(os.stat(windows_native_path(path), follow_symlinks=False), internal=True)
        yield
    finally:
        kernel.CloseHandle(handle)


@contextmanager
def staging_file(path, expected_identity):
    """Exclusive new staging or exact persisted inode, denying foreign writers."""
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    handle = kernel.CreateFileW(
        windows_native_path(path),
        0xC0000000,
        1,
        None,
        1 if expected_identity is None else 3,
        0x00200000,
        None,
    )
    if handle == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), "Staging ownership is unavailable")
    fd = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
    try:
        info = os.fstat(fd)
        check_stat(info, internal=True)
        if os.path.normcase(windows_final_path(fd)) != os.path.normcase(str(path)):
            raise OSError("Staging escaped its pinned namespace")
        observed = (info.st_dev, info.st_ino)
        if expected_identity is not None and observed != expected_identity:
            raise OSError("Staging ownership changed")
        yield fd, observed
    finally:
        os.close(fd)


@contextmanager
def pinned_file(path):
    """Pin the exact regular, single-link file against foreign writes and deletion."""
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    handle = kernel.CreateFileW(windows_native_path(path), 0x80010000, 1, None, 3, 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        code = ctypes.get_last_error()
        if code in {2, 3}:
            raise FileNotFoundError(code, "Source file is absent")
        raise OSError(code, "Cannot pin source file")
    fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    try:
        check_stat(os.fstat(fd), internal=True)
        if os.path.normcase(windows_final_path(fd)) != os.path.normcase(str(path)):
            raise OSError("Source handle changed namespace")
        yield fd
    finally:
        os.close(fd)


def rename_pinned(fd, leaf):
    """Rename the pinned handle within its pinned parent, refusing name collisions."""
    import msvcrt

    if not leaf or any(char in leaf for char in "/\\:"):
        raise ValueError("Native rename requires one exact leaf")

    class Rename(ctypes.Structure):
        _fields_ = [
            ("ReplaceIfExists", wintypes.BOOLEAN),
            ("RootDirectory", wintypes.HANDLE),
            ("FileNameLength", wintypes.ULONG),
            ("FileName", wintypes.WCHAR * (len(leaf.encode("utf-16-le")) // 2 + 1)),
        ]

    class IoStatus(ctypes.Structure):
        _fields_ = [("Status", ctypes.c_void_p), ("Information", ctypes.c_size_t)]

    native = ctypes.WinDLL("ntdll")
    native.NtSetInformationFile.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(IoStatus),
        ctypes.c_void_p,
        wintypes.ULONG,
        wintypes.ULONG,
    ]
    native.NtSetInformationFile.restype = ctypes.c_long
    rename = Rename(False, None, len(leaf.encode("utf-16-le")), leaf)
    status = native.NtSetInformationFile(
        msvcrt.get_osfhandle(fd),
        ctypes.byref(IoStatus()),
        ctypes.byref(rename),
        ctypes.sizeof(rename),
        10,
    )
    if status < 0:
        native.RtlNtStatusToDosError.argtypes = [ctypes.c_long]
        native.RtlNtStatusToDosError.restype = wintypes.ULONG
        raise OSError(native.RtlNtStatusToDosError(status), "Exclusive source rename failed")
