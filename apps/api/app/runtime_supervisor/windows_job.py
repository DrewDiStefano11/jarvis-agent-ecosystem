from __future__ import annotations

import ctypes
import os
from ctypes import wintypes


class WindowsJob:
    """Own children in a kill-on-close Job Object on Windows."""

    def __init__(self, *, memory_bytes: int | None = None) -> None:
        if memory_bytes is not None and (
            not isinstance(memory_bytes, int) or memory_bytes < 16777216
        ):
            raise ValueError("Native job memory limit must be at least 16 MiB")
        self.handle: int | None = None
        if os.name != "nt":
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")

        class BasicLimit(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_ulonglong)
                for name in (
                    "ReadOperationCount",
                    "WriteOperationCount",
                    "OtherOperationCount",
                    "ReadTransferCount",
                    "WriteTransferCount",
                    "OtherTransferCount",
                )
            ]

        class ExtendedLimit(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimit),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        info = ExtendedLimit()
        info.BasicLimitInformation.LimitFlags = 0x00002000
        if memory_bytes is not None:
            info.BasicLimitInformation.LimitFlags |= 0x00000300
            info.ProcessMemoryLimit = memory_bytes
            info.JobMemoryLimit = memory_bytes
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        if not kernel32.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            raise OSError(error, "SetInformationJobObject failed")
        self.handle = handle

    def assign(self, process_handle: int) -> None:
        if self.handle is None:
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        if not kernel32.AssignProcessToJobObject(self.handle, process_handle):
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")

    def close(self) -> None:
        if self.handle is not None:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            kernel32.CloseHandle(self.handle)
            self.handle = None


def resume_initial_thread(process):
    """Resume only after assigning the still-suspended process to its native job."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)

    class ThreadEntry(ctypes.Structure):
        _fields_ = [
            ("size", wintypes.DWORD),
            ("usage", wintypes.DWORD),
            ("thread_id", wintypes.DWORD),
            ("owner_pid", wintypes.DWORD),
            ("base_priority", wintypes.LONG),
            ("delta_priority", wintypes.LONG),
            ("flags", wintypes.DWORD),
        ]

    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
    kernel.Thread32First.restype = wintypes.BOOL
    kernel.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
    kernel.Thread32Next.restype = wintypes.BOOL
    kernel.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenThread.restype = wintypes.HANDLE
    kernel.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel.ResumeThread.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    snapshot = kernel.CreateToolhelp32Snapshot(4, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), "Cannot inspect suspended process")
    try:
        entry = ThreadEntry()
        entry.size = ctypes.sizeof(entry)
        found = []
        available = kernel.Thread32First(snapshot, ctypes.byref(entry))
        while available:
            if entry.owner_pid == process.pid:
                found.append(entry.thread_id)
            entry.size = ctypes.sizeof(entry)
            available = kernel.Thread32Next(snapshot, ctypes.byref(entry))
        if ctypes.get_last_error() != 18 or len(found) != 1:
            raise OSError("Cannot identify the unique suspended initial thread")
        thread = kernel.OpenThread(2, False, found[0])
        if not thread:
            raise OSError(ctypes.get_last_error(), "Cannot open suspended initial thread")
        try:
            if kernel.ResumeThread(thread) != 1:
                raise OSError("Unexpected initial thread suspension state")
        finally:
            kernel.CloseHandle(thread)
    finally:
        kernel.CloseHandle(snapshot)
