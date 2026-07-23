from dataclasses import dataclass
from typing import Optional
from .enums import SupervisorState, WorkerState

@dataclass
class SupervisorRecord:
    id: str
    status: SupervisorState
    paused: bool
    emergency_stop: bool
    desired_worker_state: str
    current_worker_instance_id: Optional[str]
    current_worker_pid: Optional[int]
    current_worker_start_token: Optional[str]
    restart_attempt_count: int
    crash_loop_detected: bool

@dataclass
class WorkerInstanceRecord:
    instance_id: str
    pid: int
    process_start_token: str
    scenario: str
    status: WorkerState
    started_at: float
