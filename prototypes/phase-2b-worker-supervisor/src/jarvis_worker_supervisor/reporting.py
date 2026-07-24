from __future__ import annotations

import time
from typing import Any

from .enums import TERMINAL_WORKER_STATES, SupervisorState


def _health_for_state(state: str) -> str:
    if state == SupervisorState.STARTING.value:
        return "starting"
    if state == SupervisorState.RECOVERING.value:
        return "recovering"
    if state == SupervisorState.STOPPED.value:
        return "stopped"
    if state in {SupervisorState.FAILED.value, SupervisorState.OFFLINE.value}:
        return "failed"
    if state in {
        SupervisorState.DEGRADED.value,
        SupervisorState.CRASH_LOOP.value,
        SupervisorState.EMERGENCY_STOPPED.value,
        SupervisorState.PAUSED.value,
        SupervisorState.STOPPING.value,
    }:
        return "degraded"
    return "healthy"


def generate_status_report(
    db,
    supervisor_id: str,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    state = db.get_supervisor_state(supervisor_id)
    if not state:
        return {
            "health": "stopped",
            "error": {"code": "supervisor_not_initialized"},
        }
    worker_row = db.get_worker_instance(state["current_worker_instance_id"])
    worker = dict(worker_row) if worker_row else None
    timestamp = time.time() if now is None else now
    stopped_at = state["stopped_at"] or timestamp
    uptime = max(0.0, stopped_at - state["started_at"]) if state["started_at"] else 0.0
    counts = db.worker_counts()
    active_workers = sum(
        count for status, count in counts.items() if status not in TERMINAL_WORKER_STATES
    )
    return {
        "service": "jarvis-worker-supervisor-prototype",
        "health": _health_for_state(state["status"]),
        "supervisor": {
            "id": state["id"],
            "state": state["status"],
            "paused": bool(state["paused"]),
            "emergencyStop": bool(state["emergency_stop"]),
            "crashLoopDetected": bool(state["crash_loop_detected"]),
            "restartAttemptCount": state["restart_attempt_count"],
            "uptimeSeconds": uptime,
            "lastError": state["last_error_json"],
        },
        "worker": worker,
        "metrics": {
            "activeWorkers": active_workers,
            "workerStates": counts,
            "lifecycleEvents": db.event_count(),
            **db.metrics(),
        },
        "ownership": {
            "tasks": "phase-2a-control-plane",
            "checkpoints": "phase-2a-control-plane",
            "audit": "phase-2a-control-plane",
            "eventPublication": "phase-2a-transactional-outbox",
        },
    }
