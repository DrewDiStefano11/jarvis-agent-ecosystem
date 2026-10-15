"""Private Windows name fence for one exact persisted native metadata effect.

This observes permitted names, never authorizes an effect or validates its bytes.
The caller must retain ordinary file/directory pins, classify actual inode-owned
states and resolve the journal, approvals, lease and checkpoint independently.
Unknown names, aliases, overflow and malformed notifications always refuse.
"""

import ctypes
import os
import re
import struct
from contextlib import contextmanager
from ctypes import wintypes
from threading import RLock

from app.self_build.commit_process import refuse


def exact_names(names):
    if not isinstance(names, frozenset) or not 1 <= len(names) <= 8:
        raise ValueError("native metadata effects require a bounded exact name set")
    for name in names:
        if (
            not isinstance(name, str)
            or len(name) > 240
            or not re.fullmatch(r"[A-Za-z0-9._/-]+", name)
            or any(part in {"", ".", ".."} for part in name.split("/"))
        ):
            raise ValueError("native effect name is not an exact canonical relative path")
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError("native effect names contain aliases")
    return names


def notification_names(payload):
    """Decode a complete bounded FILE_NOTIFY_INFORMATION chain without guessing."""
    if not isinstance(payload, bytes) or not 12 <= len(payload) <= 65536:
        refuse("GIT_COMMIT_NAMESPACE_UNCERTAIN")
    offset, names = 0, []
    while True:
        if len(payload) - offset < 12 or len(names) >= 4096:
            refuse("GIT_COMMIT_NAMESPACE_UNCERTAIN")
        following, action, length = struct.unpack_from("<III", payload, offset)
        end = offset + 12 + length
        if action not in {1, 2, 3, 4, 5} or not length or length % 2 or end > len(payload):
            refuse("GIT_COMMIT_NAMESPACE_UNCERTAIN")
        try:
            name = payload[offset + 12 : end].decode("utf-16-le").replace("\\", "/")
            exact_names(frozenset({name}))
        except (ValueError, UnicodeError):
            refuse("GIT_COMMIT_NAMESPACE_UNCERTAIN")
        names.append(name)
        if following == 0:
            if len(payload) - end > 3:
                refuse("GIT_COMMIT_NAMESPACE_UNCERTAIN")
            return tuple(names)
        if following != (12 + length + 3) // 4 * 4 or offset + following >= len(payload):
            refuse("GIT_COMMIT_NAMESPACE_UNCERTAIN")
        offset += following


@contextmanager
def effect_namespace_watch(path, names):
    names = exact_names(names)
    if os.name != "nt":
        refuse("GIT_COMMIT_PLATFORM_UNAVAILABLE")

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
    close.argtypes, close.restype = [wintypes.HANDLE], wintypes.BOOL
    event_create = kernel.CreateEventW
    event_create.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    event_create.restype = wintypes.HANDLE
    reset = kernel.ResetEvent
    reset.argtypes, reset.restype = [wintypes.HANDLE], wintypes.BOOL
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
    cancel.argtypes, cancel.restype = [wintypes.HANDLE, ctypes.POINTER(Overlapped)], wintypes.BOOL
    handle = create(str(path), 1, 3, None, 3, 0x42200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), "Native commit namespace watch unavailable")
    event, armed, normal = None, False, False
    buffer = (wintypes.DWORD * 16384)()
    pending, transferred = Overlapped(), wintypes.DWORD()
    lock = RLock()

    def inspect():
        payload = bytes(buffer)[: transferred.value]
        if any(name not in names for name in notification_names(payload)):
            refuse("GIT_COMMIT_NAMESPACE_CHANGED")

    def arm():
        nonlocal armed
        if not reset(event) or not read(
            handle, buffer, ctypes.sizeof(buffer), True, 0x3, None, ctypes.byref(pending), None
        ):
            raise OSError(ctypes.get_last_error(), "Cannot arm native commit namespace watch")
        armed = True

    try:
        event = event_create(None, True, False, None)
        if not event:
            raise OSError(ctypes.get_last_error(), "Native commit watch event unavailable")
        pending.hEvent = event
        arm()

        def check():
            nonlocal armed
            with lock:
                for _ in range(64):
                    if result(handle, ctypes.byref(pending), ctypes.byref(transferred), False):
                        armed = False
                        inspect()
                        # Windows keeps notifications between calls in the buffer
                        # associated with this handle. Zero/overflow never recovers
                        # by enumeration; unknown transient names remain a refusal.
                        arm()
                    elif ctypes.get_last_error() == 996:
                        return
                    else:
                        refuse("GIT_COMMIT_NAMESPACE_UNCERTAIN")
                refuse("GIT_COMMIT_NAMESPACE_LIMIT")

        check()
        yield check
        check()
        normal = True
    finally:
        try:
            if armed:
                cancel(handle, ctypes.byref(pending))
                completed = result(handle, ctypes.byref(pending), ctypes.byref(transferred), True)
                error = ctypes.get_last_error()
                # A notification winning final cancellation leaves an uncertain
                # tail. Refuse rather than accept a partially drained history.
                if normal and (completed or error != 995):
                    refuse("GIT_COMMIT_NAMESPACE_UNCERTAIN")
        finally:
            if event:
                close(event)
            close(handle)
