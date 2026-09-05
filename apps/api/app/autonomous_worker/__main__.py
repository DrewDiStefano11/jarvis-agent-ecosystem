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


def _create_worker_app():
    """Compose worker dependencies without declaring an API process restart."""

    return create_app(recover_interrupted_workflow=False)


async def _run_once_resilient(service, worker_id: str):
    try:
        return await service.run_once(worker_id)
    except AutonomousWorkerError as exc:
        if exc.code not in EXPECTED_RUN_ERRORS:
            raise
        return None


async def _run_once_or_stop(service, worker_id: str, stop: asyncio.Event):
    run_task = asyncio.create_task(_run_once_resilient(service, worker_id))
    stop_task = asyncio.create_task(stop.wait())
    done, _pending = await asyncio.wait({run_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
    if stop_task in done:
        run_task.cancel()
        try:
            await run_task
        except asyncio.CancelledError:
            pass
        return None, True
    stop_task.cancel()
    try:
        await stop_task
    except asyncio.CancelledError:
        pass
    return await run_task, False


def _install_stop_handlers(loop, stop: asyncio.Event) -> None:
    signal_names = [signal.SIGINT, signal.SIGTERM]
    sigbreak = getattr(signal, "SIGBREAK", None)
    if sigbreak is not None:
        signal_names.append(sigbreak)

    def request_stop(_signum, _frame) -> None:
        loop.call_soon_threadsafe(stop.set)

    for signal_name in signal_names:
        try:
            loop.add_signal_handler(signal_name, stop.set)
        except NotImplementedError:
            try:
                signal.signal(signal_name, request_stop)
            except (OSError, ValueError):
                pass


async def run() -> None:
    # The sidecar shares durable services with the API, but it is not an API
    # restart and must never run simulator crash-recovery initialization.
    app = _create_worker_app()
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
    _install_stop_handlers(loop, stop)
    try:
        while not stop.is_set():
            app.state.task_leases.heartbeat_worker(worker.id)
            result, stopping = await _run_once_or_stop(service, worker.id, stop)
            if stopping:
                break
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
