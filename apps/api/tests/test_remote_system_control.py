"""Emergency control keeps native leases, durable state, audit and revocation fences."""

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
