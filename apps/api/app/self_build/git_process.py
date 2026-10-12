"""Private fixed Git subprocess ownership and pre-execution memory enforcement."""

import os
import signal
import subprocess
import sys
from contextlib import contextmanager

from app.runtime_supervisor.windows_job import WindowsJob, resume_initial_thread

POSIX_LAUNCH = (
    "import os,resource,sys; "
    "limit=int(sys.argv[1]); "
    "resource.setrlimit(resource.RLIMIT_AS,(limit,limit)); "
    "resource.setrlimit(resource.RLIMIT_CORE,(0,0)); "
    "os.execve(sys.argv[2],sys.argv[2:],os.environ)"
)


@contextmanager
def limited_process(argv, *, cwd, env, authority_check, launch, memory_bytes=536870912):
    """OS memory bounds for fixed native Git reads, established before Git starts."""
    authority_check()
    job = WindowsJob(memory_bytes=memory_bytes) if os.name == "nt" else None
    if os.name != "nt":
        # A clean helper sets hard limits before exec, avoiding unsafe preexec_fn
        # callbacks in the multithreaded API. Inherited sealed Git fd stays open.
        launch = dict(launch)
        executable = launch.pop("executable", argv[0])
        argv = [sys.executable, "-I", "-c", POSIX_LAUNCH, str(memory_bytes), executable, *argv[1:]]
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
