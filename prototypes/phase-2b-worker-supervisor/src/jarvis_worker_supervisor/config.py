from dataclasses import dataclass
from typing import Optional
from jarvis_simulated_worker.scenarios import WorkerScenario

@dataclass
class SupervisorConfig:
    runtime_dir: str
    scenario: WorkerScenario
    readiness_timeout_seconds: float = 10.0
    heartbeat_timeout_seconds: float = 15.0
    graceful_shutdown_seconds: float = 10.0
    forced_shutdown_seconds: float = 5.0
    watchdog_interval_seconds: float = 1.0
    maximum_restarts: int = 5
    restart_window_seconds: float = 60.0
    initial_backoff_seconds: float = 1.0
    maximum_backoff_seconds: float = 30.0
    stable_runtime_seconds: float = 30.0
    jitter_enabled: bool = False

    # Log limits
    max_log_bytes: int = 1024 * 1024
