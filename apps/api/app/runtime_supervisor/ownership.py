from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path


def process_identity(pid: int) -> str | None:
    """Return a PID-reuse-resistant process creation identity."""

    if pid <= 0:
        return None
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        process = kernel32.OpenProcess(0x1000, False, pid)
        if not process:
            return None
        creation = ctypes.c_ulonglong()
        exit_time = ctypes.c_ulonglong()
        kernel_time = ctypes.c_ulonglong()
        user_time = ctypes.c_ulonglong()
        kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        try:
            success = kernel32.GetProcessTimes(
                process,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel_time),
                ctypes.byref(user_time),
            )
            return (
                f"windows-filetime:{creation.value}" if success and exit_time.value == 0 else None
            )
        finally:
            kernel32.CloseHandle(process)
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="ascii").split()
        return f"proc-start:{fields[21]}"
    except (FileNotFoundError, OSError, IndexError):
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError, OSError):
            return None
        return f"pid-only:{pid}"


class SingletonLock:
    """A lifetime-held OS file lock; the lock file itself is never deleted."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._stream = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._stream = self.path.open("a+b")
            self._stream.seek(0)
            if self._stream.read(1) == b"":
                self._stream.seek(0)
                self._stream.write(b"0")
                self._stream.flush()
            self._stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            if self._stream is not None:
                self._stream.close()
            self._stream = None
            return False
        return True

    def release(self) -> None:
        if self._stream is None:
            return
        self._stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(self._stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
        self._stream.close()
        self._stream = None

    def __enter__(self) -> SingletonLock:
        if not self.acquire():
            raise RuntimeError("another supervisor owns this installation")
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


def state_ownership(state: dict[str, object] | None) -> str:
    if not state:
        return "not_running"
    pid = state.get("pid")
    identity = state.get("processIdentity")
    if not isinstance(pid, int) or not isinstance(identity, str):
        return "stale"
    current = process_identity(pid)
    return "running" if current is not None and current == identity else "stale"
