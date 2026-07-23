from .enums import SupervisorState

def apply_emergency_stop(db, supervisor_id: str):
    state = db.get_supervisor_state(supervisor_id)
    if state:
        db.set_supervisor_state(
            id=supervisor_id,
            state=SupervisorState(state['status']),
            paused=state['paused'],
            emergency_stop=True,
            crash_loop_detected=state['crash_loop_detected'],
            restart_attempt_count=state['restart_attempt_count'],
            current_worker_instance_id=state['current_worker_instance_id'],
            current_worker_pid=state['current_worker_pid'],
            current_worker_start_token=state['current_worker_start_token']
        )
