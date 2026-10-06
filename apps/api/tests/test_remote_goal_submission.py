"""Real domain/outbox mutation; HTTP/TLS integration is developed separately."""

import pytest
from sqlalchemy import select

from app.core.errors import DomainError
from app.db.models import AuditEventRow, OutboxEventRow, TaskRow
from app.models.domain import CreateTaskRequest
from app.models.identity import AssignPermissionRequest, CreatePermissionRequest
from app.remote_control.service import RemoteControlService
from tests.test_remote_control_access import TOKEN

pytest_plugins = ["tests.test_remote_control_access"]


def configured_service(remote_access):
    app, actor_id, access = remote_access
    permission = app.state.identity_service.create_definition(
        "permission",
        CreatePermissionRequest(
            stable_key="remote.submit",
            display_name="Remote goal submission",
            resource_type="administrative_function",
            action="remote_submit",
        ),
    )
    grant = dict(
        permission_id=permission.id,
        resource_type="administrative_function",
        resource_id="remote_control",
    )
    app.state.identity_service.assign_permission(
        actor_id, AssignPermissionRequest(effect="allow", **grant)
    )
    actor = access.authenticate("Bearer " + TOKEN, secure_transport=True)
    return (
        app,
        actor,
        RemoteControlService(
            access,
            app.state.repository,
            app.state.broker,
            app.state.task_leases,
            app.state.agent_runtime_service,
            app.state.agent_runtime_repository,
        ),
        grant,
    )


@pytest.mark.asyncio
async def test_remote_goal_replay_has_one_native_task_audit_and_outbox(remote_access):
    app, actor, service, _ = configured_service(remote_access)
    goal = CreateTaskRequest(
        title="Remote objective", description="Produce a bounded research plan"
    )
    first = await service.submit_goal(actor, goal, "retry-1")
    second = await service.submit_goal(actor, goal, "retry-1")
    assert first == second
    assert first.createdBy == actor.actor_id and first.status == "queued"
    with app.state.task_leases.session_factory() as session:
        rows = session.scalars(select(TaskRow).where(TaskRow.id == first.id)).all()
        audits = session.scalars(
            select(AuditEventRow).where(AuditEventRow.task_id == first.id)
        ).all()
        outbox = session.scalars(
            select(OutboxEventRow).where(OutboxEventRow.envelope["taskId"].as_string() == first.id)
        ).all()
        assert len(rows) == len(audits) == len(outbox) == 1
        assert audits[0].actor == actor.actor_id
        assert audits[0].agent_id is None  # Registry identity is not a simulated-agent FK.
        assert outbox[0].envelope["source"] == "remote-operator"
    app.state.repository.reload()
    restored = next(item for item in app.state.repository.audit if item.taskId == first.id)
    assert restored.actorIdentityId == actor.actor_id
    with pytest.raises(DomainError, match="different request"):
        await service.submit_goal(
            actor, goal.model_copy(update={"title": "Changed objective"}), "retry-1"
        )


@pytest.mark.asyncio
async def test_revocation_before_goal_commit_blocks_mutation_and_event(remote_access, monkeypatch):
    app, actor, service, grant = configured_service(remote_access)
    original = app.state.broker.emit
    before = set(app.state.repository.tasks)

    async def revoke_before_commit(*args, **kwargs):
        app.state.identity_service.assign_permission(
            actor.actor_id, AssignPermissionRequest(effect="deny", **grant)
        )
        return await original(*args, **kwargs)

    monkeypatch.setattr(app.state.broker, "emit", revoke_before_commit)
    with pytest.raises(DomainError, match="denied"):
        await service.submit_goal(
            actor,
            CreateTaskRequest(
                title="Late revoked objective", description="Must never be committed"
            ),
            "revoked-1",
        )
    app.state.repository.reload()
    assert set(app.state.repository.tasks) == before
    with app.state.task_leases.session_factory() as session:
        assert not session.scalars(
            select(TaskRow).where(TaskRow.title == "Late revoked objective")
        ).all()
        assert not session.scalars(
            select(AuditEventRow).where(AuditEventRow.actor == actor.actor_id)
        ).all()


def grant_control(app, actor):
    permission = app.state.identity_service.create_definition(
        "permission",
        CreatePermissionRequest(
            stable_key="remote.control",
            display_name="Remote control",
            resource_type="administrative_function",
            action="remote_control",
        ),
    )
    scope = dict(
        permission_id=permission.id,
        resource_type="administrative_function",
        resource_id="remote_control",
    )
    app.state.identity_service.assign_permission(
        actor.actor_id, AssignPermissionRequest(effect="allow", **scope)
    )
    return scope


def grant_task_permission(app, actor, key, task_id=None):
    identity = app.state.identity_service
    permission = next(
        (
            item
            for item in identity.list_definitions("permission", 0, 100)
            if item.stable_key == key
        ),
        None,
    )
    if permission is None:
        permission = identity.create_definition(
            "permission",
            CreatePermissionRequest(
                stable_key=key, display_name=key, resource_type="task", action=key.replace(".", "_")
            ),
        )
    identity.assign_permission(
        actor.actor_id,
        AssignPermissionRequest(
            permission_id=permission.id,
            effect="allow",
            resource_type="task" if task_id is not None else None,
            resource_id=task_id,
        ),
    )


@pytest.mark.asyncio
async def test_remote_cancellation_revokes_real_worker_lease_and_attributes_audit(remote_access):
    app, actor, service, _ = configured_service(remote_access)
    grant_control(app, actor)
    goal = await service.submit_goal(
        actor,
        CreateTaskRequest(
            title="Cancelable goal", description="Stop this goal before worker completion"
        ),
        "cancel-1",
    )
    worker = app.state.task_leases.register_worker(
        "Cancel fixture worker", "remote-cancel-worker", 60
    )
    grant_task_permission(app, actor, "runtime.cancel", goal.id)
    acquired = app.state.task_leases.acquire_task(worker.id, task_id=goal.id)
    assert acquired is not None
    _, lease = acquired
    cancelled = await service.cancel_goal(actor, goal.id)
    assert cancelled.status == "cancelled"
    with pytest.raises(DomainError):
        app.state.task_leases.complete_task(goal.id, worker.id, lease.leaseToken, "stale success")
    assert app.state.repository.get_task_durable(goal.id).status == "cancelled"
    with app.state.task_leases.session_factory() as session:
        audit = session.scalar(
            select(AuditEventRow).where(
                AuditEventRow.task_id == goal.id, AuditEventRow.event_type == "task.cancel"
            )
        )
        assert audit is not None and audit.actor == actor.actor_id
        assert audit.payload["actorIdentityId"] == actor.actor_id


@pytest.mark.asyncio
async def test_remote_cancellation_rechecks_permissions_inside_native_write(
    remote_access, monkeypatch
):
    app, actor, service, _ = configured_service(remote_access)
    scope = grant_control(app, actor)
    goal = await service.submit_goal(
        actor,
        CreateTaskRequest(
            title="Still authorized goal",
            description="A revoked operator must not cancel this goal",
        ),
        "cancel-revoke-1",
    )
    original = app.state.task_leases.cancel_task
    grant_task_permission(app, actor, "runtime.cancel", goal.id)

    def revoke_then_cancel(*args, **kwargs):
        app.state.identity_service.assign_permission(
            actor.actor_id, AssignPermissionRequest(effect="deny", **scope)
        )
        return original(*args, **kwargs)

    monkeypatch.setattr(app.state.task_leases, "cancel_task", revoke_then_cancel)
    with pytest.raises(DomainError, match="denied"):
        await service.cancel_goal(actor, goal.id)
    assert app.state.repository.get_task_durable(goal.id).status == "queued"
