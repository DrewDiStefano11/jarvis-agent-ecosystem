from enum import StrEnum


class SupervisorState(StrEnum):
    STARTING = "starting"
    RECOVERING = "recovering"
    IDLE = "idle"
    LAUNCHING = "launching"
    WAITING_FOR_READINESS = "waiting_for_readiness"
    RUNNING = "running"
    DEGRADED = "degraded"
    STOPPING = "stopping"
    PAUSED = "paused"
    EMERGENCY_STOPPED = "emergency_stopped"
    CRASH_LOOP = "crash_loop"
    FAILED = "failed"
    STOPPED = "stopped"
    OFFLINE = "offline"


class WorkerState(StrEnum):
    CREATED = "created"
    STARTING = "starting"
    READY = "ready"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    STOPPING = "stopping"
    STOPPED = "stopped"
    COMPLETED = "completed"
    CRASHED = "crashed"
    KILLED = "killed"
    TIMED_OUT = "timed_out"
    ORPHANED = "orphaned"
    UNKNOWN = "unknown"


TERMINAL_WORKER_STATES = frozenset(
    {
        WorkerState.STOPPED.value,
        WorkerState.COMPLETED.value,
        WorkerState.CRASHED.value,
        WorkerState.KILLED.value,
        WorkerState.ORPHANED.value,
        WorkerState.UNKNOWN.value,
    }
)
