from __future__ import annotations

from enum import StrEnum
from uuid import uuid4

import psutil


class ProcessIdentityStatus(StrEnum):
    VERIFIED = "verified"
    NOT_RUNNING = "not_running"
    MISMATCH = "mismatch"
    INACCESSIBLE = "inaccessible"


def generate_process_start_token() -> str:
    return str(uuid4())


def generate_instance_id() -> str:
    return str(uuid4())


def inspect_process_identity(
    db, instance_id: str, pid: int, start_token: str
) -> ProcessIdentityStatus:
    worker = db.get_worker_instance(instance_id)
    if not worker:
        return ProcessIdentityStatus.MISMATCH
    if worker["pid"] != pid or worker["process_start_token"] != start_token:
        return ProcessIdentityStatus.MISMATCH

    try:
        process = psutil.Process(pid)
        if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
            return ProcessIdentityStatus.NOT_RUNNING
        recorded_create_time = worker["process_create_time"]
        if abs(process.create_time() - recorded_create_time) > 1.0:
            return ProcessIdentityStatus.MISMATCH
        if worker["executable"] and process.exe() != worker["executable"]:
            return ProcessIdentityStatus.MISMATCH
        if worker["command_line"] and " ".join(process.cmdline()) != worker["command_line"]:
            return ProcessIdentityStatus.MISMATCH
    except psutil.NoSuchProcess:
        return ProcessIdentityStatus.NOT_RUNNING
    except psutil.AccessDenied:
        return ProcessIdentityStatus.INACCESSIBLE
    return ProcessIdentityStatus.VERIFIED


def verify_process_identity(db, instance_id: str, pid: int, start_token: str) -> bool:
    return (
        inspect_process_identity(db, instance_id, pid, start_token)
        is ProcessIdentityStatus.VERIFIED
    )
