from __future__ import annotations

from .enums import SupervisorState
from .shutdown import request_graceful_shutdown


def apply_emergency_stop(db, supervisor_id: str, runtime_dir: str | None = None) -> None:
    state = db.get_supervisor_state(supervisor_id)
    if not state:
        return
    db.update_supervisor(
        supervisor_id,
        status=SupervisorState.EMERGENCY_STOPPED,
        emergency_stop=True,
    )
    db.append_event(
        "supervisor.emergency_stop",
        worker_instance_id=state["current_worker_instance_id"],
        previous_state=state["status"],
        new_state=SupervisorState.EMERGENCY_STOPPED.value,
        severity="warning",
    )
    if runtime_dir and state["current_worker_instance_id"]:
        request_graceful_shutdown(runtime_dir, state["current_worker_instance_id"])
