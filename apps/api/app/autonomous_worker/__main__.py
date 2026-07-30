from __future__ import annotations

import asyncio
import signal

from app.autonomous_worker.errors import AutonomousWorkerError
from app.main import create_app

EXPECTED_RUN_ERRORS = {
    "EXECUTION_CANCELLED",
    "EXECUTION_EMERGENCY_STOPPED",
    "EXECUTION_LEASE_LOST",
    "EXECUTION_AUTHORIZATION_REVOKED",
}


async def _run_once_resilient(service, worker_id: str):
    try:
        return await service.run_once(worker_id)
    except AutonomousWorkerError as exc:
        if exc.code not in EXPECTED_RUN_ERRORS:
            raise
        return None


async def run() -> None:
    app = create_app()
    settings = app.state.settings
    service = app.state.autonomous_worker_service
    service.validate_enabled()
    worker = app.state.task_leases.register_worker(
        "autonomous-planning-worker",
        settings.autonomous_worker_instance_id,
        settings.autonomous_worker_lease_seconds,
        {"kind": "autonomous_planning_review", "maximumConcurrency": 1},
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, stop.set)
        except NotImplementedError:
            pass
    try:
        while not stop.is_set():
            app.state.task_leases.heartbeat_worker(worker.id)
            result = await _run_once_resilient(service, worker.id)
            if result is None:
                try:
                    await asyncio.wait_for(
                        stop.wait(),
                        timeout=settings.autonomous_worker_poll_interval_ms / 1000,
                    )
                except TimeoutError:
                    pass
    finally:
        app.state.task_leases.stop_worker(worker.id)
        app.state.engine.dispose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
