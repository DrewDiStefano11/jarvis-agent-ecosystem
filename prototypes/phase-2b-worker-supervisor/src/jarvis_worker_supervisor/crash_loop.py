import time
from .config import SupervisorConfig
from .enums import SupervisorState

def check_crash_loop(db, supervisor_id: str, config: SupervisorConfig) -> bool:
    state = db.get_supervisor_state(supervisor_id)
    if not state:
        return False

    window_started_at = state['restart_window_started_at']
    if window_started_at and (time.time() - window_started_at) <= config.restart_window_seconds:
        if state['restart_attempt_count'] >= config.maximum_restarts:
            return True

    return False

def reset_crash_loop(db, supervisor_id: str):
    state = db.get_supervisor_state(supervisor_id)
    if state:
        db.set_supervisor_state(
            id=supervisor_id,
            state=SupervisorState(state['status']),
            paused=state['paused'],
            emergency_stop=state['emergency_stop'],
            crash_loop_detected=False,
            restart_attempt_count=0,
            current_worker_instance_id=state['current_worker_instance_id'],
            current_worker_pid=state['current_worker_pid'],
            current_worker_start_token=state['current_worker_start_token']
        )

        with db._get_connection() as conn:
            conn.execute("UPDATE supervisor_state SET restart_window_started_at = NULL WHERE id = ?", (supervisor_id,))
