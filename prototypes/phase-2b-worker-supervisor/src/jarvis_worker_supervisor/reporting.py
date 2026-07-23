import json

def generate_status_report(db, supervisor_id: str) -> dict:
    state = db.get_supervisor_state(supervisor_id)
    if not state:
        return {"error": "Supervisor not initialized"}

    worker = None
    if state['current_worker_instance_id']:
        w = db.get_worker_instance(state['current_worker_instance_id'])
        if w:
            worker = dict(w)

    return {
        "supervisor_instance": state['id'],
        "state": state['status'],
        "paused": bool(state['paused']),
        "emergency_stop": bool(state['emergency_stop']),
        "crash_loop_detected": bool(state['crash_loop_detected']),
        "restart_attempt_count": state['restart_attempt_count'],
        "worker": worker
    }
