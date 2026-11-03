"""Bounded native scheduling/cancellation with intercepted model responses."""

import asyncio
from types import SimpleNamespace

import pytest

import app.autonomous_worker.__main__ as worker_main


@pytest.mark.parametrize("size", [1, 2, 4])
async def test_worker_pool_uses_bounded_distinct_slots_and_cancels_before_shutdown(monkeypatch, size):
    registrations, stopped, entered, cancelled = [], [], set(), set()
    all_entered = asyncio.Event()
    captured_stop = []
    disposed = []
    instance = "p" * 120

    class Leases:
        def register_worker(self, name, instance_id, seconds, metadata):
            registrations.append((name, instance_id, seconds, metadata))
            return SimpleNamespace(id=f"worker-{len(registrations)}")

        def heartbeat_worker(self, worker_id):
            assert worker_id not in stopped

        def stop_worker(self, worker_id):
            assert worker_id in cancelled
            stopped.append(worker_id)

    class Service:
        def validate_enabled(self):
            pass

        async def run_once(self, worker_id):
            entered.add(worker_id)
            if len(entered) == size:
                all_entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.add(worker_id)
                raise

    app = SimpleNamespace(state=SimpleNamespace(
        settings=SimpleNamespace(
            autonomous_worker_max_concurrency=size,
            autonomous_worker_instance_id=instance,
            autonomous_worker_lease_seconds=60,
            autonomous_worker_poll_interval_ms=100,
        ),
        autonomous_worker_service=Service(), task_leases=Leases(),
        engine=SimpleNamespace(dispose=lambda: disposed.append(True)),
    ))
    monkeypatch.setattr(worker_main, "_create_worker_app", lambda: app)
    monkeypatch.setattr(worker_main, "_install_stop_handlers", lambda loop, stop: captured_stop.append(stop))
    run = asyncio.create_task(worker_main.run())
    try:
        await asyncio.wait_for(all_entered.wait(), 3)
    finally:
        captured_stop[0].set()
        await asyncio.wait_for(run, 3)
    assert len(registrations) == len(entered) == len(cancelled) == len(stopped) == size
    assert registrations[0][1] == instance
    assert len({item[1] for item in registrations}) == size
    assert all(len(item[1]) <= 120 and item[3]["maximumConcurrency"] == 1 for item in registrations)
    assert disposed == [True]


async def test_disabled_worker_never_registers_pool_or_starts_work(monkeypatch):
    from app.autonomous_worker.errors import AutonomousWorkerError

    registered, disposed = [], []

    class Service:
        def validate_enabled(self):
            raise AutonomousWorkerError("AUTONOMOUS_WORKER_DISABLED")

    app = SimpleNamespace(state=SimpleNamespace(
        settings=SimpleNamespace(autonomous_worker_max_concurrency=4),
        autonomous_worker_service=Service(),
        task_leases=SimpleNamespace(register_worker=lambda *args: registered.append(args)),
        engine=SimpleNamespace(dispose=lambda: disposed.append(True)),
    ))
    monkeypatch.setattr(worker_main, "_create_worker_app", lambda: app)
    with pytest.raises(AutonomousWorkerError):
        await worker_main.run()
    assert registered == [] and disposed == [True]


async def test_outer_cancellation_waits_for_the_inflight_iteration_to_cancel():
    entered, cancelled = asyncio.Event(), asyncio.Event()
    inner_tasks = []

    class Service:
        async def run_once(self, worker_id):
            inner_tasks.append(asyncio.current_task())
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

    outer = asyncio.create_task(worker_main._run_once_or_stop(Service(), "slot", asyncio.Event()))
    await asyncio.wait_for(entered.wait(), 3)
    try:
        outer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await outer
        assert cancelled.is_set()
        assert all(task.done() for task in inner_tasks)
    finally:
        for task in inner_tasks:
            task.cancel()
        await asyncio.gather(*inner_tasks, return_exceptions=True)


async def test_two_native_queued_tasks_overlap_complete_and_idle_without_more_inference(tmp_path, monkeypatch):
    import json

    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from app.db.models import ModelExecutionRow
    from app.main import create_app
    from tests.test_agent_runtime_sql_control_plane import grant_runtime_permissions
    from tests.test_autonomous_worker import (
        VALID_RESULT,
        FakeRouter,
        configure_worker,
        create_assembly_and_runtime,
        queue_only_demo_task,
    )

    entered, release, idle = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls, idle_slots, stop_holder = set(), set(), []

    class HeldRouter(FakeRouter):
        async def execute(self, **kwargs):
            request = kwargs["request"]
            purpose = request.output_schema.name if request.output_schema else None
            if purpose not in {"required_capabilities", "task_decomposition"}:
                calls.add(request.task_id)
                if len(calls) == 2:
                    entered.set()
                await release.wait()
            return await super().execute(**kwargs)

    app = create_app(database_url=f"sqlite:///{(tmp_path / 'native-pool.db').as_posix()}")
    router = HeldRouter([json.dumps(VALID_RESULT), json.dumps(VALID_RESULT)])
    with TestClient(app) as client:
        queue_only_demo_task(app)
        second = app.state.repository.tasks["task-completed"]
        second.status, second.startedAt, second.completedAt, second.result = "queued", None, None, None
        app.state.repository.persist()
        from app.models.identity import AssignPermissionRequest

        actor_id = grant_runtime_permissions(app, "pool-operator", task_id="task-demo")
        for permission in app.state.identity_service.list_definitions("permission", 0, 100):
            app.state.identity_service.assign_permission(
                actor_id,
                AssignPermissionRequest(
                    permission_id=permission.id, effect="allow", resource_type="task",
                    resource_id="task-completed",
                ),
            )
        configure_worker(app, actor_id, router)
        app.state.settings.autonomous_worker_max_concurrency = 2
        for index, task_id in enumerate(("task-demo", "task-completed")):
            create_assembly_and_runtime(
                client, app, actor_id, run_id=f"pool-run-{index}", task_id=task_id
            )
        service = app.state.autonomous_worker_service
        run_once = service.run_once

        async def observe(worker_id, **kwargs):
            result = await run_once(worker_id, **kwargs)
            if result is None and all(
                app.state.repository.get_task_durable(task_id).status == "completed"
                for task_id in ("task-demo", "task-completed")
            ):
                idle_slots.add(worker_id)
                if len(idle_slots) == 2:
                    idle.set()
            return result

        monkeypatch.setattr(service, "run_once", observe)
        monkeypatch.setattr(worker_main, "_create_worker_app", lambda: app)
        monkeypatch.setattr(worker_main, "_install_stop_handlers", lambda loop, stop: stop_holder.append(stop))
        running = asyncio.create_task(worker_main.run())
        try:
            await asyncio.wait_for(entered.wait(), 10)
            assert len(router.requests) == 0  # Both calls are still held before their intercepted responses.
            release.set()
            await asyncio.wait_for(idle.wait(), 10)
        finally:
            release.set()
            stop_holder[0].set()
            await asyncio.wait_for(running, 10)
        assert len(router.requests) == 2 and calls == {"task-demo", "task-completed"}
        assert all(worker.status == "stopped" for worker in app.state.task_leases.list_workers())
        with app.state.model_execution_repository.sessions() as session:
            rows = list(session.scalars(select(ModelExecutionRow)))
        assert len(rows) == 2 and len({row.worker_id for row in rows}) == 2
        assert all(app.state.agent_runtime_service.repository.load_run(f"pool-run-{index}").state == "succeeded" for index in range(2))
