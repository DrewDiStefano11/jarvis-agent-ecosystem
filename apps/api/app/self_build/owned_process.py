"""Internal native process ownership, not execution permission or a sandbox."""

import ctypes
import os
import signal
import subprocess
from contextlib import contextmanager
from ctypes import wintypes

from app.runtime_supervisor.windows_job import WindowsJob


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


@contextmanager
def owned_process(argv, *, cwd, env, authority_check, launch):
    """Own one internal fixed invocation through cleanup, including descendants.

    Callers must bind fixed argv to persisted intent, immutable executable image,
    live approval/lease/checkpoints and bounded output supervision. This primitive
    is not exposed to an agent, API or tool and grants no filesystem confinement.
    Windows assignment precedes initial-thread resume; failure never runs the tool.
    POSIX owns a new session/process group for trusted native tool descendants.
    """
    authority_check()
    job = WindowsJob() if os.name == "nt" else None
    process = None
    try:
        kwargs = {"creationflags": 0x08000004} if job else {"start_new_session": True}
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            close_fds=True,
            **kwargs,
            **launch,
        )
        if job:
            job.assign(int(process._handle))
            authority_check()
            resume_initial_thread(process)
        yield process
    finally:
        if job:
            job.close()
        elif process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            for stream in (process.stdout, process.stderr):
                stream.close()
