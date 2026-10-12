"""Atomic Windows directory create/open with a retained deny-write/delete handle."""

import ctypes
import os
from ctypes import wintypes

from app.tool_execution.filesystem import check_stat


def supports_atomic_directory():
    return os.name == "nt"


def pinned_target_directory(stack, root, leaf, *, create):
    if not supports_atomic_directory():
        raise OSError("Atomic target directory ownership requires Windows")
    if (
        not isinstance(leaf, str)
        or not leaf.startswith("jarvis-")
        or len(leaf) != 39
        or any(c not in "0123456789abcdef" for c in leaf[7:])
    ):
        raise ValueError("Use only the generated target leaf")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    native = ctypes.WinDLL("ntdll")
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
    root_handle = kernel.CreateFileW(
        "\\\\?\\" + str(root.path), 0x80000000, 3, None, 3, 0x02200000, None
    )
    if root_handle == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), "Cannot open pinned target parent")
    try:

        class UnicodeString(ctypes.Structure):
            _fields_ = [
                ("Length", wintypes.USHORT),
                ("MaximumLength", wintypes.USHORT),
                ("Buffer", wintypes.LPWSTR),
            ]

        class ObjectAttributes(ctypes.Structure):
            _fields_ = [
                ("Length", wintypes.ULONG),
                ("RootDirectory", wintypes.HANDLE),
                ("ObjectName", ctypes.POINTER(UnicodeString)),
                ("Attributes", wintypes.ULONG),
                ("SecurityDescriptor", ctypes.c_void_p),
                ("SecurityQualityOfService", ctypes.c_void_p),
            ]

        class IoStatus(ctypes.Structure):
            _fields_ = [("Status", ctypes.c_void_p), ("Information", ctypes.c_size_t)]

        text = ctypes.create_unicode_buffer(leaf)
        encoded = leaf.encode("utf-16-le")
        name = UnicodeString(len(encoded), len(encoded) + 2, ctypes.cast(text, wintypes.LPWSTR))
        attributes = ObjectAttributes(
            ctypes.sizeof(ObjectAttributes), root_handle, ctypes.pointer(name), 0x40, None, None
        )
        handle = wintypes.HANDLE()
        io = IoStatus()
        native.NtCreateFile.argtypes = [
            ctypes.POINTER(wintypes.HANDLE),
            wintypes.ULONG,
            ctypes.POINTER(ObjectAttributes),
            ctypes.POINTER(IoStatus),
            ctypes.c_void_p,
            wintypes.ULONG,
            wintypes.ULONG,
            wintypes.ULONG,
            wintypes.ULONG,
            ctypes.c_void_p,
            wintypes.ULONG,
        ]
        native.NtCreateFile.restype = ctypes.c_long
        # FILE_CREATE fails on every existing node. Opening for replay cannot create.
        # Read-only sharing denies replacement and reparse mutation through a writer.
        status = native.NtCreateFile(
            ctypes.byref(handle),
            0x00100081,
            ctypes.byref(attributes),
            ctypes.byref(io),
            None,
            0,
            1,
            2 if create else 1,
            0x21 if create else 0x200021,
            None,
            0,
        )
        if status < 0:
            native.RtlNtStatusToDosError.argtypes = [ctypes.c_long]
            native.RtlNtStatusToDosError.restype = wintypes.ULONG
            error = native.RtlNtStatusToDosError(status)
            if error in {80, 183}:
                raise FileExistsError(error, "Target already exists")
            raise OSError(error, "Cannot atomically pin target directory")
        stack.callback(kernel.CloseHandle, handle)
        if io.Information != (2 if create else 1):
            raise OSError("Unexpected atomic target create/open disposition")
        target = root.path / leaf
        info = target.lstat()
        check_stat(info, directory=True, internal=True)
        return target, info
    finally:
        kernel.CloseHandle(root_handle)
