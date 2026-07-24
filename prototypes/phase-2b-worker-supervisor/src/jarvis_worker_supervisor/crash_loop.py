from __future__ import annotations

import time

from .config import SupervisorConfig
from .enums import SupervisorState


def check_crash_loop(db, supervisor_id: str, config: SupervisorConfig) -> bool:
    state = db.get_supervisor_state(supervisor_id)
    if not state or not state["restart_window_started_at"]:
        return False
    return bool(
        time.time() - state["restart_window_started_at"] <= config.restart_window_seconds
        and state["restart_attempt_count"] >= config.maximum_restarts
    )


def reset_crash_loop(db, supervisor_id: str) -> None:
    state = db.get_supervisor_state(supervisor_id)
    if not state:
        return
    target = SupervisorState.PAUSED if state["paused"] else SupervisorState.IDLE
    db.update_supervisor(
        supervisor_id,
        status=target,
        crash_loop_detected=False,
        restart_attempt_count=0,
        restart_window_started_at=None,
        next_restart_at=None,
        last_error_json=None,
    )
    db.append_event(
        "supervisor.crash_loop_reset",
        previous_state=state["status"],
        new_state=target.value,
        severity="warning",
    )
