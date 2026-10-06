"""Emergency control keeps native leases, durable state, audit and revocation fences."""

import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select

from app.core.errors import DomainError
from app.db.models import AuditEventRow
from app.models.identity import AssignPermissionRequest, CreatePermissionRequest

pytest_plugins = ["tests.test_remote_http"]


def grant_system(app, actor):
    permission = app.state.identity_service.create_definition(
        "permission",
        CreatePermissionRequest(
            stable_key="system.control",
            display_name="System control",
            resource_type="administrative_function",
            action="system_control",
        ),
    )
    scope = dict(
        permission_id=permission.id,
        resource_type="administrative_function",
        resource_id="system_control",
    )
    app.state.identity_service.assign_permission(
        actor.actor_id, AssignPermissionRequest(effect="allow", **scope)
    )
    return scope


@pytest.mark.parametrize("native_stop", [False, True])
def test_remote_agent_transitions_survive_reload_and_restart(remote_http, native_stop):
    from app.main import create_app

    app, actor, client, headers = remote_http
    grant_system(app, actor)
    agent = next(iter(app.state.repository.agents.values()))
    agent.status = "thinking"
    agent.previousStatus = None
    app.state.repository.persist()
    if native_stop:
        client.portal.call(app.state.simulator.emergency_stop)
    else:
        # The API projection can lag behind durable worker updates. Stop must
        # derive agent transitions from the fenced database, not this cache.
        agent.status = "idle"
        assert client.post("/api/remote/system/emergency-stop", headers=headers).status_code == 200
    app.state.repository.reload()
    assert app.state.repository.agents[agent.id].status == "paused"
    assert app.state.repository.agents[agent.id].previousStatus == "thinking"
    assert client.post("/api/remote/system/resume", headers=headers).status_code == 200
    app.state.repository.reload()
    assert not app.state.repository.system_control_snapshot().emergencyStop
    assert app.state.repository.agents[agent.id].status == "thinking"
    assert app.state.repository.agents[agent.id].previousStatus is None
    assert app.state.repository.agents[agent.id].statusMessage == "Resumed after emergency stop"
    restarted = create_app(
        database_url=app.state.settings.database_url, recover_interrupted_workflow=False
    )
    assert not restarted.state.repository.system_control_snapshot().emergencyStop
    assert restarted.state.repository.agents[agent.id].status == "thinking"
    assert (
        restarted.state.repository.agents[agent.id].statusMessage == "Resumed after emergency stop"
    )


def test_remote_stop_checkpoint_uses_fenced_agent_and_task_projections(remote_http):
    app, actor, client, headers = remote_http
    grant_system(app, actor)
    repository = app.state.repository
    created = client.post(
        "/api/remote/goals",
        json={
            "title": "Checkpoint projection",
            "description": "Preserve leased task in checkpoint",
        },
        headers=headers | {"Idempotency-Key": "checkpoint-projection-1"},
    )
    assert created.status_code == 201
    task_id = created.json()["data"]["id"]
    agent = next(iter(repository.agents.values()))
    agent.status = "thinking"
    repository.create_workflow_run("run-remote-projection", 30)
    client.portal.call(app.state.broker.emit, "system.simulator.started", {"step": 0})
    worker = app.state.task_leases.register_worker("Checkpoint fixture", "checkpoint-fixture", 60)
    acquired = app.state.task_leases.acquire_task(worker.id, task_id=task_id)
    assert acquired is not None
    repository.agents[agent.id].status = "idle"
    repository.tasks[task_id].status = "queued"
    app.state.simulator.run_id = "run-remote-projection"
    app.state.simulator.control.state = "paused"
    assert client.post("/api/remote/system/emergency-stop", headers=headers).status_code == 200
    workflow = repository.active_workflow()
    checkpoint = repository.load_checkpoint(workflow.checkpoint_id)
    assert checkpoint["agentStatuses"][agent.id] == "paused"
    assert checkpoint["taskStatuses"][task_id] == "in_progress"
    assert repository.get_task_durable(task_id).status == "in_progress"


def test_remote_stop_fences_worker_and_does_not_flush_stale_task_cache(remote_http):
    app, actor, client, headers = remote_http
    assert client.post("/api/remote/system/emergency-stop", headers=headers).status_code == 403
    grant_system(app, actor)
    created = client.post(
        "/api/remote/goals",
        json={"title": "Stop fence", "description": "Preserve leased task state"},
        headers=headers | {"Idempotency-Key": "stop-fence-1"},
    )
    task_id = created.json()["data"]["id"]
    worker = app.state.task_leases.register_worker("Stop fixture worker", "stop-fixture", 60)
    _, lease = app.state.task_leases.acquire_task(worker.id, task_id=task_id)
    # Model an API cache lagging behind a separately committed worker projection.
    app.state.repository.tasks[task_id].status = "queued"
    stopped = client.post("/api/remote/system/emergency-stop", headers=headers)
    assert stopped.status_code == 200 and stopped.json()["data"]["emergencyStop"]
    assert app.state.repository.get_task_durable(task_id).status == "in_progress"
    with pytest.raises(DomainError):
        app.state.task_leases.complete_task(task_id, worker.id, lease.leaseToken, "late completion")
    resumed = client.post("/api/remote/system/resume", headers=headers)
    assert resumed.status_code == 200 and not resumed.json()["data"]["emergencyStop"]
    assert app.state.repository.get_task_durable(task_id).status == "in_progress"
    assert client.post("/api/remote/system/resume", headers=headers).json() == resumed.json()
    with app.state.repository.session_factory() as session:
        records = session.scalars(
            select(AuditEventRow).where(
                AuditEventRow.actor == actor.actor_id,
                AuditEventRow.event_type.in_(["system.emergency_stop", "system.resumed"]),
            )
        ).all()
        assert len(records) == 2 and all(row.agent_id is None for row in records)
        assert all(row.payload["actorIdentityId"] == actor.actor_id for row in records)


@pytest.mark.parametrize("stop", [True, False])
def test_system_revocation_at_commit_preserves_durable_and_memory_state(
    remote_http, monkeypatch, stop
):
    app, actor, client, headers = remote_http
    scope = grant_system(app, actor)
    if not stop:
        assert client.post("/api/remote/system/emergency-stop", headers=headers).status_code == 200
    else:
        app.state.simulator.control.state = "running"
    before_control = app.state.simulator.control.model_copy(deep=True)
    before_agents = {
        agent_id: agent.model_copy(deep=True)
        for agent_id, agent in app.state.repository.agents.items()
    }
    original = app.state.broker.emit

    async def revoke_before_commit(*args, **kwargs):
        app.state.identity_service.assign_permission(
            actor.actor_id, AssignPermissionRequest(effect="deny", **scope)
        )
        return await original(*args, **kwargs)

    monkeypatch.setattr(app.state.broker, "emit", revoke_before_commit)
    path = "/api/remote/system/" + ("emergency-stop" if stop else "resume")
    response = client.post(path, headers=headers)
    assert (
        response.status_code == 403
        and response.json()["error"]["code"] == "REMOTE_SYSTEM_CONTROL_DENIED"
    )
    assert app.state.repository.system_control_snapshot().emergencyStop == (not stop)
    assert app.state.simulator.control == before_control
    assert app.state.repository.agents == before_agents
    if stop:
        assert app.state.simulator._resume.is_set()


def test_committed_stop_survives_lost_acknowledgement_without_duplicate_event(
    remote_http, monkeypatch
):
    app, actor, client, headers = remote_http
    grant_system(app, actor)
    original = app.state.broker.emit

    async def lose_acknowledgement(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("injected committed stop acknowledgement loss")

    monkeypatch.setattr(app.state.broker, "emit", lose_acknowledgement)
    with pytest.raises(RuntimeError, match="acknowledgement loss"):
        client.post("/api/remote/system/emergency-stop", headers=headers)
    assert app.state.repository.system_control_snapshot().emergencyStop
    monkeypatch.setattr(app.state.broker, "emit", original)
    assert client.post("/api/remote/system/emergency-stop", headers=headers).status_code == 200
    with app.state.repository.session_factory() as session:
        rows = session.scalars(
            select(AuditEventRow).where(
                AuditEventRow.actor == actor.actor_id,
                AuditEventRow.event_type == "system.emergency_stop",
            )
        ).all()
        assert len(rows) == 1


@pytest.mark.parametrize("stop", [True, False])
def test_concurrent_desired_system_controls_commit_once(remote_http, monkeypatch, stop):
    app, actor, client, headers = remote_http
    grant_system(app, actor)
    if not stop:
        assert client.post("/api/remote/system/emergency-stop", headers=headers).status_code == 200
    action_name = "emergency_stop" if stop else "system_resume"
    original = getattr(app.state.simulator, action_name)
    both_arrived = asyncio.Event()
    arrivals = 0

    async def overlap(**kwargs):
        nonlocal arrivals
        arrivals += 1
        if arrivals == 2:
            both_arrived.set()
        await asyncio.wait_for(both_arrived.wait(), timeout=5)
        return await original(**kwargs)

    monkeypatch.setattr(app.state.simulator, action_name, overlap)
    path = "/api/remote/system/" + ("emergency-stop" if stop else "resume")
    with ThreadPoolExecutor(max_workers=2) as pool:
        requests = [pool.submit(client.post, path, headers=headers) for _ in range(2)]
        responses = [request.result(timeout=10) for request in requests]
    assert all(response.status_code == 200 for response in responses)
    assert responses[0].json() == responses[1].json()
    assert app.state.repository.system_control_snapshot().emergencyStop == stop
    with app.state.repository.session_factory() as session:
        rows = session.scalars(
            select(AuditEventRow).where(
                AuditEventRow.actor == actor.actor_id,
                AuditEventRow.event_type == ("system.emergency_stop" if stop else "system.resumed"),
            )
        ).all()
        assert len(rows) == 1


def test_committed_resume_survives_lost_acknowledgement(remote_http, monkeypatch):
    app, actor, client, headers = remote_http
    grant_system(app, actor)
    assert client.post("/api/remote/system/emergency-stop", headers=headers).status_code == 200
    original = app.state.broker.emit

    async def lose_acknowledgement(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("injected committed resume acknowledgement loss")

    monkeypatch.setattr(app.state.broker, "emit", lose_acknowledgement)
    with pytest.raises(RuntimeError, match="acknowledgement loss"):
        client.post("/api/remote/system/resume", headers=headers)
    assert not app.state.repository.system_control_snapshot().emergencyStop
    assert all(agent.status != "paused" for agent in app.state.repository.agents.values())
    monkeypatch.setattr(app.state.broker, "emit", original)
    assert client.post("/api/remote/system/resume", headers=headers).status_code == 200
    with app.state.repository.session_factory() as session:
        rows = session.scalars(
            select(AuditEventRow).where(
                AuditEventRow.actor == actor.actor_id,
                AuditEventRow.event_type == "system.resumed",
            )
        ).all()
        assert len(rows) == 1
