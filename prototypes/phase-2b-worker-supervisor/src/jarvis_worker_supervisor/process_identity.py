import uuid
import psutil
from typing import Tuple

def generate_process_start_token() -> str:
    return str(uuid.uuid4())

def generate_instance_id() -> str:
    return str(uuid.uuid4())

def verify_process_identity(db, instance_id: str, pid: int, start_token: str) -> bool:
    worker = db.get_worker_instance(instance_id)
    if not worker:
        return False

    if worker['pid'] != pid or worker['process_start_token'] != start_token:
        return False

    try:
        proc = psutil.Process(pid)
        create_time = proc.create_time()

        # Check against recorded process creation time to detect PID reuse
        recorded_create_time = worker['process_create_time']
        if recorded_create_time and abs(create_time - recorded_create_time) > 1.0: # Allow 1s precision diff
            return False

        return True
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        return False
