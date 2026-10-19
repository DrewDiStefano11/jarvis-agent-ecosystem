"""Atomic create/open of one exact Git object prefix, retaining its native handle."""

import ctypes
import os
import re
from ctypes import wintypes

from app.tool_execution.filesystem import check_stat, windows_native_path


def object_directory(stack, root, prefix, *, create):
    if os.name != "nt" or not isinstance(prefix, str) or not re.fullmatch(r"[a-f0-9]{2}", prefix):
        raise ValueError("native object directory requires one exact two-hex prefix")
    kernel, native = ctypes.WinDLL("kernel32", use_last_error=True), ctypes.WinDLL("ntdll")
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
    kernel.CloseHandle.argtypes, kernel.CloseHandle.restype = [wintypes.HANDLE], wintypes.BOOL
    root_handle = kernel.CreateFileW(
        windows_native_path(root.path), 0x80000000, 3, None, 3, 0x02200000, None
    )
    if root_handle == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), "Cannot pin native object parent")
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

        text = ctypes.create_unicode_buffer(prefix)
        name = UnicodeString(4, 6, ctypes.cast(text, wintypes.LPWSTR))
        attributes = ObjectAttributes(
            ctypes.sizeof(ObjectAttributes), root_handle, ctypes.pointer(name), 0x40, None, None
        )
        handle, io = wintypes.HANDLE(), IoStatus()
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
            native.RtlNtStatusToDosError.argtypes, native.RtlNtStatusToDosError.restype = (
                [ctypes.c_long],
                wintypes.ULONG,
            )
            error = native.RtlNtStatusToDosError(status)
            if error in {80, 183}:
                raise FileExistsError(error, "An unclaimed object directory already exists")
            raise OSError(error, "Cannot atomically pin object directory")
        stack.callback(kernel.CloseHandle, handle)
        if io.Information != (2 if create else 1):
            raise OSError("Unexpected native object directory disposition")
        path = root.path / prefix
        info = os.stat(windows_native_path(path), follow_symlinks=False)
        check_stat(info, directory=True, internal=True)
        return path, (info.st_dev, info.st_ino)
    finally:
        kernel.CloseHandle(root_handle)
