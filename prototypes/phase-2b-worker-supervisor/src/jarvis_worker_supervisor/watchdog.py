from __future__ import annotations

import json
import logging
import threading
import time

from .enums import SupervisorState
from .supervisor import Supervisor

LOGGER = logging.getLogger(__name__)


class Watchdog:
    def __init__(self, supervisor: Supervisor):
        self.supervisor = supervisor
        self._stop_requested = threading.Event()

    @property
    def running(self) -> bool:
        return not self._stop_requested.is_set()

    def stop(self) -> None:
        self._stop_requested.set()

    def start(self) -> None:
        self.supervisor.prepare_startup()
        self.supervisor.recover()
        LOGGER.info("runtime_started supervisor=%s", self.supervisor.supervisor_id)
        try:
            while not self._stop_requested.is_set():
                self.supervisor.tick()
                if self.supervisor.state["status"] in {
                    SupervisorState.STOPPED.value,
                    SupervisorState.FAILED.value,
                }:
                    break
                self._stop_requested.wait(self.supervisor.config.watchdog_interval_seconds)
        except KeyboardInterrupt:
            self._stop_requested.set()
        except Exception as exc:
            LOGGER.exception("runtime_unexpected_failure")
            self.supervisor.db.bump_metric("unexpected_error_count")
            error = {"type": type(exc).__name__, "message": str(exc)}
            self._stop_requested.set()
            try:
                self._drain()
            except Exception:
                LOGGER.exception("runtime_failure_cleanup_failed")
            self.supervisor._transition(
                SupervisorState.FAILED,
                event_type="supervisor.unexpected_failure",
                severity="error",
                details=error,
                last_error_json=json.dumps(error, sort_keys=True),
            )
            raise
        finally:
            if (
                self._stop_requested.is_set()
                and self.supervisor.state["status"] != SupervisorState.FAILED.value
            ):
                self._drain()
            self.supervisor.release_lease()
            LOGGER.info("runtime_exited supervisor=%s", self.supervisor.supervisor_id)

    def _drain(self) -> None:
        self.supervisor.request_shutdown()
        deadline = (
            time.monotonic()
            + self.supervisor.config.graceful_shutdown_seconds
            + self.supervisor.config.forced_shutdown_seconds
            + self.supervisor.config.watchdog_interval_seconds
        )
        while (
            self.supervisor.state["status"] != SupervisorState.STOPPED.value
            and time.monotonic() < deadline
        ):
            self.supervisor.tick()
            time.sleep(min(0.05, self.supervisor.config.watchdog_interval_seconds))
