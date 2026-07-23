import enum

class WorkerScenario(str, enum.Enum):
    HEALTHY = "healthy"
    COMPLETE_AND_EXIT = "complete-and-exit"
    CRASH_IMMEDIATELY = "crash-immediately"
    CRASH_AFTER_READY = "crash-after-ready"
    CRASH_AFTER_HEARTBEATS = "crash-after-heartbeats"
    HANG_BEFORE_READY = "hang-before-ready"
    HANG_AFTER_READY = "hang-after-ready"
    STOP_HEARTBEATS = "stop-heartbeats"
    IGNORE_SHUTDOWN = "ignore-shutdown"
    SLOW_SHUTDOWN = "slow-shutdown"
    LOG_FLOOD = "log-flood"
    EXIT_CODE = "exit-code"
