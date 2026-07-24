from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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

    max_log_bytes: int = 1024 * 1024
    lease_ttl_seconds: float | None = None

    def __post_init__(self) -> None:
        self.runtime_dir = str(Path(self.runtime_dir).expanduser().resolve())
        if not isinstance(self.scenario, WorkerScenario):
            self.scenario = WorkerScenario(self.scenario)

        positive = {
            "readiness_timeout_seconds": self.readiness_timeout_seconds,
            "heartbeat_timeout_seconds": self.heartbeat_timeout_seconds,
            "graceful_shutdown_seconds": self.graceful_shutdown_seconds,
            "forced_shutdown_seconds": self.forced_shutdown_seconds,
            "watchdog_interval_seconds": self.watchdog_interval_seconds,
            "restart_window_seconds": self.restart_window_seconds,
            "initial_backoff_seconds": self.initial_backoff_seconds,
            "maximum_backoff_seconds": self.maximum_backoff_seconds,
            "stable_runtime_seconds": self.stable_runtime_seconds,
            "max_log_bytes": self.max_log_bytes,
        }
        invalid = [name for name, value in positive.items() if value <= 0]
        if invalid:
            raise ValueError(f"Configuration values must be positive: {', '.join(invalid)}")
        if self.maximum_restarts < 0:
            raise ValueError("maximum_restarts must be non-negative")
        if self.maximum_backoff_seconds < self.initial_backoff_seconds:
            raise ValueError("maximum_backoff_seconds must be >= initial_backoff_seconds")
        if self.lease_ttl_seconds is None:
            self.lease_ttl_seconds = self.watchdog_interval_seconds * 3
        if self.lease_ttl_seconds <= self.watchdog_interval_seconds:
            raise ValueError("lease_ttl_seconds must exceed watchdog_interval_seconds")
