from __future__ import annotations

import asyncio
import signal
import sys
from hashlib import sha256

from app.autonomous_worker.errors import AutonomousWorkerError
from app.main import create_app

EXPECTED_RUN_ERRORS = {
    "VERIFICATION_IN_PROGRESS",
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
    try:
        done, _pending = await asyncio.wait(
            {run_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if stop_task in done:
            run_task.cancel()
            try:
                await run_task
            except asyncio.CancelledError:
                pass
            return None, True
        return await run_task, False
    finally:
        # Outer cancellation (including another slot failing) must not leave an
        # inference or stop waiter running after native leases are released.
        run_task.cancel()
        stop_task.cancel()
        await asyncio.gather(run_task, stop_task, return_exceptions=True)


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


async def _worker_loop(app, service, worker, stop):
    settings = app.state.settings
    while not stop.is_set():
        app.state.task_leases.heartbeat_worker(worker.id)
        result, stopping = await _run_once_or_stop(service, worker.id, stop)
        if stopping:
            return
        if result is None:
            try:
                await asyncio.wait_for(
                    stop.wait(), timeout=settings.autonomous_worker_poll_interval_ms / 1000
                )
            except TimeoutError:
                pass


def _slot_instance(instance, index):
    if index == 0:
        return instance
    # Bounded, restart-stable additional native registrations; preserve slot zero.
    return "autonomous-slot-" + sha256(f"{instance}:{index}".encode()).hexdigest()


async def run() -> None:
    # The sidecar shares durable services with the API, but it is not an API
    # restart and must never run simulator crash-recovery initialization.
    app = _create_worker_app()
    settings = app.state.settings
    service = app.state.autonomous_worker_service
    workers, jobs = [], []
    try:
        service.validate_enabled()
        concurrency = settings.autonomous_worker_max_concurrency
        if type(concurrency) is not int or not 1 <= concurrency <= 4:
            raise AutonomousWorkerError("AUTONOMOUS_WORKER_CONCURRENCY_INVALID")
        stop = asyncio.Event()
        _install_stop_handlers(asyncio.get_running_loop(), stop)
        for index in range(concurrency):
            workers.append(
                app.state.task_leases.register_worker(
                    "autonomous-planning-worker",
                    _slot_instance(settings.autonomous_worker_instance_id, index),
                    settings.autonomous_worker_lease_seconds,
                    {
                        "kind": "autonomous_planning_review",
                        "maximumConcurrency": 1,
                        "poolConcurrency": concurrency,
                        "poolSlot": index + 1,
                    },
                )
            )
        jobs = [asyncio.create_task(_worker_loop(app, service, worker, stop)) for worker in workers]
        await asyncio.gather(*jobs)
    finally:
        failing = sys.exc_info()[0] is not None
        for job in jobs:
            job.cancel()
        if jobs:
            await asyncio.gather(*jobs, return_exceptions=True)
        cleanup_errors = []
        try:
            for worker in workers:
                try:
                    app.state.task_leases.stop_worker(worker.id)
                except Exception as exc:
                    cleanup_errors.append(exc)
        finally:
            app.state.engine.dispose()
        if cleanup_errors and not failing:
            raise cleanup_errors[0]


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
