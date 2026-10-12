"""Native Windows change notification covering absent Git metadata names."""

import ctypes
import os
from contextlib import contextmanager
from ctypes import wintypes

from app.core.errors import DomainError


def changed():
    raise DomainError(
        "SELF_BUILD_GIT_STATE_CHANGED", "Git metadata namespace changed during inspection.", 409
    )


@contextmanager
def namespace_watch(path):
    if os.name != "nt":
        yield lambda: None
        return

    class Overlapped(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_size_t),
            ("InternalHigh", ctypes.c_size_t),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]

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
    event_create = kernel.CreateEventW
    event_create.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    event_create.restype = wintypes.HANDLE
    read = kernel.ReadDirectoryChangesW
    read.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(Overlapped),
        ctypes.c_void_p,
    ]
    read.restype = wintypes.BOOL
    result = kernel.GetOverlappedResult
    result.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(Overlapped),
        ctypes.POINTER(wintypes.DWORD),
        wintypes.BOOL,
    ]
    result.restype = wintypes.BOOL
    cancel = kernel.CancelIoEx
    cancel.argtypes = [wintypes.HANDLE, ctypes.POINTER(Overlapped)]
    cancel.restype = wintypes.BOOL
    # Parents are pinned before this handle is opened. Deny deletion/replacement;
    # existing metadata files separately deny writes. Observe the entire subtree.
    handle = create(str(path), 1, 3, None, 3, 0x42200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), "Native metadata change watch unavailable")
    event = None
    armed = False
    normal = False
    buffer = (wintypes.DWORD * 16384)()  # aligned bounded 64 KiB notification buffer
    pending = Overlapped()
    transferred = wintypes.DWORD()
    try:
        event = event_create(None, True, False, None)
        if not event:
            raise OSError(ctypes.get_last_error(), "Metadata watch event unavailable")
        pending.hEvent = event
        # Observe file/directory names. Existing files are separately deny-write pinned;
        # attribute/access notifications can be delayed by ordinary read handles.
        if not read(
            handle, buffer, ctypes.sizeof(buffer), True, 0x3, None, ctypes.byref(pending), None
        ):
            raise OSError(ctypes.get_last_error(), "Cannot arm metadata namespace watch")
        armed = True

        def check():
            if result(handle, ctypes.byref(pending), ctypes.byref(transferred), False):
                # Any completion, including overflow/zero bytes, invalidates evidence.
                changed()
            error = ctypes.get_last_error()
            if error != 996:  # ERROR_IO_INCOMPLETE means the original watch is still pending.
                changed()

        check()
        yield check
        check()
        normal = True
    finally:
        try:
            if armed:
                cancel(handle, ctypes.byref(pending))
                # Drain cancellation before releasing the OVERLAPPED/buffer memory.
                completed = result(handle, ctypes.byref(pending), ctypes.byref(transferred), True)
                error = ctypes.get_last_error()
                if normal and (completed or error != 995):
                    # A notification won the final cancellation race, or the watch failed.
                    changed()
        finally:
            if event:
                close(event)
            close(handle)
