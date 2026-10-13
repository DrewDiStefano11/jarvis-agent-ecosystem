"""Native creation intent, exact authority, checkpoint crash gaps and migration."""

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.errors import DomainError
from app.db.models import AuditEventRow, DevelopmentWorkspaceRow, OutboxEventRow, SystemStateRow
from app.models.agent_runtime import BeginAttemptCommand, StartAttemptCommand
from app.models.identity import AssignPermissionRequest, CreatePermissionRequest
from app.models.workspace_creation import ApproveWorkspaceCreation, CreateWorkspaceRequest
from app.self_build.creation_service import WorkspaceCreationService
from app.self_build.policy import digest
from app.source_mutation import history
from app.source_mutation.repository import SourceMutationRow
from app.source_mutation.service import intent_digest
from tests.agent_runtime_testkit import ts
from tests.test_autonomous_worker_migration import migration_config

pytest_plugins = ["tests.test_self_build_git_inspection"]


@pytest.fixture
def editing(creation, request):
    from contextlib import ExitStack
    from types import SimpleNamespace

    from app.models.development_validation import CodeState
    from app.models.source_change import (
        PlanReferences,
        SourceChange,
        SourceChangeIntent,
        SourceIdentity,
    )
    from app.models.source_edit_planning import PersistEditingPlanRequest
    from app.models.source_mutation import ApplySourceMutation, ApproveSourceMutation
    from app.source_change.planner import observation_identity
    from app.source_edit_planning.service import SourceEditingPlanService
    from app.source_mutation.driver import content, observe
    from app.source_mutation.service import SourceMutationService
    from app.tool_execution.filesystem import open_directory

    app, actor, operator, creator, workspace_id, creation_request, _ = creation
    ready = creator.create(actor, workspace_id, creation_request)
    permission = app.state.identity_service.create_definition(
        "permission",
        CreatePermissionRequest(
            stable_key="self_build.workspace.source_edit",
            display_name="Edit approved owned source",
            resource_type="task",
            action="source_edit",
        ),
    )
    scope = dict(permission_id=permission.id, resource_type="task", resource_id="task-demo")
    for principal in (actor, operator):
        app.state.identity_service.assign_permission(
            principal.actor_id, AssignPermissionRequest(effect="allow", **scope)
        )
    policy = creator.workspace.policies.get("jarvis")
    target = Path(policy.worktree_root) / workspace_id
    before, after = content(b"value = 1\n", "100644"), content(b"value = 2\n", "100644")
    operation = getattr(request, "param", "modify")
    change = SourceChange(
        path="added.py" if operation == "add" else "source.py",
        operation=operation,
        before=before.model_dump(exclude={"text"}) if operation != "add" else None,
        after=after if operation != "delete" else None,
    )
    source = SourceIdentity(
        repository_identity=policy.repository_identity,
        head_sha=ready.plan.workspace.base_sha,
        base_sha=ready.plan.workspace.base_sha,
        observation_digest="0" * 64,
    )
    with ExitStack() as stack:
        root = open_directory(stack, target, internal=True)
        observation, _ = observe(
            stack, root, SimpleNamespace(source=source, changes=(change,)), {change.path: "100644"}
        )
    with creator.repository.sessions() as session:
        row = creator.repository.lookup(session, workspace_id)
        reservation = creator.repository.contract(row)
        reference = row.approval_id
    proposal = SourceEditingPlanService(app).persist(
        actor,
        PersistEditingPlanRequest(
            workspace_id=workspace_id,
            expected_workspace_version=reservation.version,
            expected_workspace_plan_hash=reservation.plan.plan_hash,
            command_id="editing-fixture",
            source_intent=SourceChangeIntent(
                source=observation_identity(observation),
                references=PlanReferences(
                    original_plan_hash=reservation.plan.plan_hash, authorization_reference=reference
                ),
                changes=(change,),
            ),
        ),
        observation,
        CodeState(
            repository_identity=source.repository_identity,
            head_sha=source.head_sha,
            base_sha=source.base_sha,
            changes=(),
        ),
    )
    service = SourceMutationService(app)
    plan = service.preview(actor, proposal.record_id)
    approval = service.approve(
        operator,
        ApproveSourceMutation(
            editing_record_id=proposal.record_id, expected_plan_hash=plan.plan_hash
        ),
    )
    request = ApplySourceMutation(
        editing_record_id=proposal.record_id,
        expected_plan_hash=plan.plan_hash,
        approval_id=approval.approval_id,
        worker_id=creation_request.worker_id,
        lease_token=creation_request.lease_token,
    )
    from app.tool_execution.filesystem import windows_native_path

    return app, actor, operator, service, request, Path(windows_native_path(target)), scope


def test_source_mutation_service_has_exact_checkpoint_and_transactional_outbox(editing):
    from app.source_mutation.repository import SourceMutationRow
    from app.source_mutation.service import SourceMutationService, intent_digest

    app, actor, _, service, request, target, _ = editing
    result = service.apply(actor, request)
    assert result.state == "complete" and result.completed_count == 1 and result.checkpoint_id
    assert (target / "source.py").read_bytes() == b"value = 2\n"
    assert SourceMutationService(app).apply(actor, request) == result
    with service.repository.sessions() as session:
        stored = session.get(SourceMutationRow, request.editing_record_id)
        assert stored.integrity_digest == intent_digest(result)
        audit = list(
            session.scalars(
                select(AuditEventRow).where(
                    AuditEventRow.event_type.like("self_build.source_mutation.%")
                )
            )
        )
        outbox = list(
            session.scalars(
                select(OutboxEventRow).where(
                    OutboxEventRow.event_type.like("self_build.source_mutation.%")
                )
            )
        )
        assert {event.sequence_number for event in audit} == {
            event.sequence_number for event in outbox
        }
        assert len(audit) >= 8
        assert "value =" not in json.dumps([event.payload for event in audit])
        assert request.lease_token not in json.dumps([event.payload for event in audit])
        assert all(
            event.payload["payload"]["authorizationApprovalId"] == request.approval_id
            for event in audit
            if event.event_type != "self_build.source_mutation.approved"
        )
    checkpoints = service.runtime.checkpoints_authorized(result.plan.runtime_run_id, actor)
    measured = [
        item
        for item in checkpoints
        if item.state_reference == "source-mutation:" + result.operation_id
    ]
    assert len(measured) == 5 and measured[-1].checkpoint_id == result.checkpoint_id


@pytest.mark.parametrize(
    "boundary",
    ["hash", "approval", "lease", "permission", "stop", "self_approval", "foreign_actor"],
)
def test_source_mutation_authority_loss_refuses_before_intent_or_files(editing, boundary):
    from app.models.source_mutation import ApproveSourceMutation
    from app.source_mutation.repository import SourceMutationRow

    app, actor, operator, service, request, target, scope = editing
    if boundary == "hash":
        request = request.model_copy(update={"expected_plan_hash": "0" * 64})
    elif boundary == "approval":
        request = request.model_copy(update={"approval_id": "foreign"})
    elif boundary == "lease":
        request = request.model_copy(update={"lease_token": "foreign"})
    elif boundary == "permission":
        app.state.identity_service.assign_permission(
            actor.actor_id, AssignPermissionRequest(effect="deny", **scope)
        )
    elif boundary == "stop":
        with service.repository.leases._write() as session:
            session.get(SystemStateRow, 1).emergency_stop = True
    elif boundary == "self_approval":
        with pytest.raises(DomainError):
            service.approve(
                actor,
                ApproveSourceMutation(
                    editing_record_id=request.editing_record_id,
                    expected_plan_hash=request.expected_plan_hash,
                ),
            )
        return
    elif boundary == "foreign_actor":
        actor = operator
    with pytest.raises(DomainError):
        service.apply(actor, request)
    assert (target / "source.py").read_bytes() == b"value = 1\n"
    with service.repository.sessions() as session:
        assert session.get(SourceMutationRow, request.editing_record_id) is None


@pytest.mark.parametrize(
    "gap",
    [
        "before_checkpoint",
        "after_checkpoint",
        "after_stage_claim",
        "after_original_move",
        "after_publication",
        "finalizing",
    ],
)
def test_source_mutation_durable_crash_gaps_recover_same_operation(editing, monkeypatch, gap):
    from app.source_mutation import driver as mutation_driver
    from app.source_mutation.service import SourceMutationService

    app, actor, _, service, request, target, _ = editing
    native_checkpoint, native_rename = (
        service.runtime.handle_authorized,
        mutation_driver.rename_pinned,
    )
    crashed = []

    def checkpoint_crash(command, *args, **kwargs):
        editing_phase = command.state_reference.startswith("source-mutation:")
        chosen = (
            editing_phase
            and not crashed
            and (
                gap in {"before_checkpoint", "after_checkpoint"}
                or (gap == "after_stage_claim" and command.resume_cursor.endswith("staged"))
                or (gap == "finalizing" and command.resume_cursor.startswith("complete"))
            )
        )
        if chosen and gap == "before_checkpoint":
            crashed.append(True)
            raise RuntimeError("injected crash")
        result = native_checkpoint(command, *args, **kwargs)
        if chosen:
            crashed.append(True)
            raise RuntimeError("injected crash")
        return result

    def rename_crash(fd, leaf):
        native_rename(fd, leaf)
        if not crashed and (
            (gap == "after_original_move" and leaf.endswith(".before"))
            or (gap == "after_publication" and leaf == "source.py")
        ):
            crashed.append(True)
            raise RuntimeError("injected crash")

    monkeypatch.setattr(service.runtime, "handle_authorized", checkpoint_crash)
    monkeypatch.setattr(mutation_driver, "rename_pinned", rename_crash)
    with pytest.raises(RuntimeError, match="injected crash"):
        service.apply(actor, request)
    pending = service.current(actor, request)[0]
    monkeypatch.setattr(service.runtime, "handle_authorized", native_checkpoint)
    monkeypatch.setattr(mutation_driver, "rename_pinned", native_rename)
    result = SourceMutationService(app).apply(actor, request)
    assert result.state == "complete" and result.operation_id == pending.operation_id
    assert (target / "source.py").read_bytes() == b"value = 2\n"


def test_source_mutation_event_failure_rolls_back_intent_before_source(editing, monkeypatch):
    from app.source_mutation.repository import SourceMutationRow

    _, actor, _, service, request, target, _ = editing
    native = service.repository.leases._add_event

    def failed(*args, **kwargs):
        native(*args, **kwargs)
        raise RuntimeError("event append failed")

    monkeypatch.setattr(service.repository.leases, "_add_event", failed)
    with pytest.raises(RuntimeError):
        service.apply(actor, request)
    assert (target / "source.py").read_bytes() == b"value = 1\n"
    with service.repository.sessions() as session:
        assert session.get(SourceMutationRow, request.editing_record_id) is None


def test_source_mutation_revocation_at_original_rename_preserves_original(editing, monkeypatch):
    from app.models.identity import AssignPermissionRequest
    from app.source_mutation import driver as mutation_driver

    app, actor, _, service, request, target, scope = editing
    native = mutation_driver.staging_file
    from contextlib import contextmanager

    @contextmanager
    def revoked(*args, **kwargs):
        with native(*args, **kwargs) as result:
            yield result
        app.state.identity_service.assign_permission(
            actor.actor_id, AssignPermissionRequest(effect="deny", **scope)
        )

    monkeypatch.setattr(mutation_driver, "staging_file", revoked)
    with pytest.raises(DomainError):
        service.apply(actor, request)
    assert (target / "source.py").read_bytes() == b"value = 1\n"


def test_source_mutation_journal_blocks_destructive_downgrade(editing):
    _, actor, _, service, request, _, _ = editing
    service.prepare(actor, request)
    config = migration_config(
        Path(service.app.state.settings.database_url.removeprefix("sqlite:///"))
    )
    with pytest.raises(RuntimeError, match="source mutation recovery evidence"):
        command.downgrade(config, "20261012_source_plans")


def test_source_mutation_expired_exact_approval_is_not_authority(editing):
    from datetime import UTC

    _, actor, _, service, request, target, _ = editing
    with service.repository.leases._write() as session:
        event = session.get(AuditEventRow, request.approval_id)
        event.payload = event.payload | {
            "payload": event.payload["payload"]
            | {"expiresAt": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()}
        }
    with pytest.raises(DomainError) as failure:
        service.apply(actor, request)
    assert failure.value.code == "SOURCE_MUTATION_APPROVAL_REQUIRED"
    assert (target / "source.py").read_bytes() == b"value = 1\n"


def test_source_mutation_operator_revocation_invalidates_persisted_grant(editing):
    app, actor, operator, service, request, target, scope = editing
    app.state.identity_service.assign_permission(
        operator.actor_id, AssignPermissionRequest(effect="deny", **scope)
    )
    with pytest.raises(DomainError) as failure:
        service.apply(actor, request)
    assert failure.value.code == "SOURCE_MUTATION_PERMISSION_DENIED"
    assert (target / "source.py").read_bytes() == b"value = 1\n"


def test_source_mutation_mismatched_native_checkpoint_blocks_pending_recovery(editing, monkeypatch):
    _, actor, _, service, request, target, _ = editing
    record = service.prepare(actor, request)
    service.acknowledge(actor, request, record)
    checkpoints = service.runtime.checkpoints_authorized(record.plan.runtime_run_id, actor)
    actual = [
        item
        for item in checkpoints
        if item.state_reference == "source-mutation:" + record.operation_id
    ][0]
    altered = actual.model_copy(update={"resume_cursor": "foreign-cursor"})
    monkeypatch.setattr(
        service.runtime,
        "checkpoints_authorized",
        lambda *args: tuple(altered if item == actual else item for item in checkpoints),
    )
    with pytest.raises(DomainError) as failure:
        service.apply(actor, request)
    assert failure.value.code == "SOURCE_MUTATION_CHECKPOINT_INVALID"
    assert (target / "source.py").read_bytes() == b"value = 1\n"


def test_source_mutation_database_failures_never_expose_bound_source_or_lease(editing, monkeypatch):
    from sqlalchemy.exc import StatementError

    _, actor, _, service, request, target, _ = editing

    def failed(*args):
        raise StatementError(
            "private source",
            "INSERT",
            {"source": "secret-value", "lease": request.lease_token},
            RuntimeError("secret-value"),
        )

    monkeypatch.setattr(service, "prepare", failed)
    with pytest.raises(DomainError) as failure:
        service.apply(actor, request)
    assert failure.value.code == "SOURCE_MUTATION_STORAGE_FAILED"
    assert failure.value.__suppress_context__
    assert "secret-value" not in str(failure.value) and request.lease_token not in str(
        failure.value
    )
    assert (target / "source.py").read_bytes() == b"value = 1\n"


def test_source_mutation_unpersisted_runtime_ack_never_grants_file_effects(editing, monkeypatch):
    _, actor, _, service, request, target, _ = editing
    monkeypatch.setattr(service.runtime, "handle_authorized", lambda *args, **kwargs: None)
    with pytest.raises(DomainError) as failure:
        service.apply(actor, request)
    assert failure.value.code == "SOURCE_MUTATION_CHECKPOINT_INVALID"
    assert (target / "source.py").read_bytes() == b"value = 1\n"
    assert service.current(actor, request)[0].checkpoint_id is None


def test_source_mutation_renewed_grant_retains_original_reference_and_audits_current_authority(
    editing,
):
    from app.models.source_mutation import ApproveSourceMutation

    _, actor, operator, service, request, _, _ = editing
    pending = service.prepare(actor, request)
    renewed = service.approve(
        operator,
        ApproveSourceMutation(
            editing_record_id=request.editing_record_id,
            expected_plan_hash=request.expected_plan_hash,
        ),
    )
    assert renewed.approval_id != pending.original_approval_id
    result = service.apply(actor, request.model_copy(update={"approval_id": renewed.approval_id}))
    assert result.original_approval_id == request.approval_id
    with service.repository.sessions() as session:
        events = list(
            session.scalars(
                select(AuditEventRow).where(
                    AuditEventRow.event_type == "self_build.source_mutation.phase_recorded"
                )
            )
        )
        assert events and all(
            event.payload["payload"]["authorizationApprovalId"] == renewed.approval_id
            for event in events
        )


@pytest.fixture
def creation(git_workspace):
    app, actor, operator, git, reservation, inspect_request, root, _ = git_workspace
    observed = git.inspect(actor, reservation.workspace_id, inspect_request)
    runtime = app.state.agent_runtime_service
    snapshot = runtime.read_run_authorized("run-1", actor)
    beginning = runtime.handle_authorized(
        BeginAttemptCommand(
            run_id="run-1",
            command_id="begin-create",
            expected_run_version=snapshot.version,
            timestamp=ts(3),
            executor_reference=inspect_request.worker_id,
        ),
        actor,
    )
    runtime.handle_authorized(
        StartAttemptCommand(
            run_id="run-1",
            command_id="start-create",
            expected_run_version=beginning.snapshot.version,
            timestamp=ts(4),
            attempt_id=beginning.snapshot.active_attempt_id,
        ),
        actor,
    )
    permission = app.state.identity_service.create_definition(
        "permission",
        CreatePermissionRequest(
            stable_key="self_build.workspace.materialize",
            display_name="Materialize owned workspace",
            resource_type="task",
            action="materialize",
        ),
    )
    scope = dict(permission_id=permission.id, resource_type="task", resource_id="task-demo")
    for principal in (actor, operator):
        app.state.identity_service.assign_permission(
            principal.actor_id, AssignPermissionRequest(effect="allow", **scope)
        )
    service = WorkspaceCreationService(app)
    plan = service.preview(actor, reservation.workspace_id, observed.inspection_id)
    approval = service.approve(
        operator,
        ApproveWorkspaceCreation(
            workspace_id=reservation.workspace_id,
            inspection_id=observed.inspection_id,
            expected_plan_hash=plan.plan_hash,
        ),
    )
    request = CreateWorkspaceRequest(
        inspection_id=observed.inspection_id,
        expected_plan_hash=plan.plan_hash,
        approval_id=approval.approval_id,
        worker_id=inspect_request.worker_id,
        lease_token=inspect_request.lease_token,
    )
    return app, actor, operator, service, reservation.workspace_id, request, scope


@pytest.mark.parametrize("identity", ["digest", "path"])
def test_source_mutation_changed_git_tool_refuses_before_intent_and_files(editing, identity):
    from app.source_mutation.repository import SourceMutationRow

    app, actor, _, service, request, target, _ = editing
    if identity == "digest":
        app.state.settings.self_build_git_sha256 = "0" * 64
    else:
        app.state.settings.self_build_git_executable = (
            app.state.settings.self_build_git_executable.swapcase()
        )
    with pytest.raises(DomainError) as failure:
        service.apply(actor, request)
    assert failure.value.code == "SOURCE_MUTATION_TOOL_CHANGED"
    assert (target / "source.py").read_bytes() == b"value = 1\n"
    with service.repository.sessions() as session:
        assert session.get(SourceMutationRow, request.editing_record_id) is None


def test_native_preparation_checkpoint_and_outbox_are_replayable_without_files(creation):
    app, actor, _, service, workspace_id, request, _ = creation
    result = service.prepare(actor, workspace_id, request)
    assert result.state == "prepared" and result.completed_file_count == 0
    assert result.checkpoint_id and service.prepare(actor, workspace_id, request) == result
    assert WorkspaceCreationService(app).read(actor, workspace_id) == result
    checkpoints = app.state.agent_runtime_service.checkpoints_authorized("run-1", actor)
    assert len(checkpoints) == 1 and checkpoints[0].checkpoint_id == result.checkpoint_id
    with app.state.repository.session_factory() as session:
        private = session.get(DevelopmentWorkspaceRow, workspace_id).creation_json
        assert checkpoints[0].integrity_digest == "sha256:" + private["digest"]
        events = list(
            session.scalars(
                select(AuditEventRow).where(
                    AuditEventRow.event_type == "self_build.workspace_creation.prepared"
                )
            )
        )
        assert len(events) == 1
        outbox = session.scalar(
            select(OutboxEventRow).where(
                OutboxEventRow.event_type == "self_build.workspace_creation.prepared"
            )
        )
        assert outbox and outbox.sequence_number == events[0].sequence_number
        assert private["nonce"] not in json.dumps(
            [event.payload for event in session.scalars(select(AuditEventRow))]
        )
    assert request.lease_token not in result.model_dump_json()
    policy = service.workspace.policies.get("jarvis")
    assert list(Path(policy.worktree_root).iterdir()) == []


def test_crash_after_intent_before_checkpoint_recovers_same_operation(creation, monkeypatch):
    app, actor, _, service, workspace_id, request, _ = creation
    native = service.runtime.handle_authorized

    def crashed(*args, **kwargs):
        raise RuntimeError("crash before native checkpoint")

    monkeypatch.setattr(service.runtime, "handle_authorized", crashed)
    with pytest.raises(RuntimeError, match="crash"):
        service.prepare(actor, workspace_id, request)
    pending = service.read(actor, workspace_id)
    assert pending.checkpoint_id is None
    monkeypatch.setattr(service.runtime, "handle_authorized", native)
    restored = WorkspaceCreationService(app).prepare(actor, workspace_id, request)
    assert (
        restored.operation_id == pending.operation_id
        and restored.ownership_digest == pending.ownership_digest
    )
    assert (
        restored.checkpoint_id and len(service.runtime.checkpoints_authorized("run-1", actor)) == 1
    )


def test_crash_after_checkpoint_before_ack_reuses_native_checkpoint(creation, monkeypatch):
    app, actor, _, service, workspace_id, request, _ = creation
    native = service.runtime.handle_authorized

    def crashed_after(*args, **kwargs):
        native(*args, **kwargs)
        raise RuntimeError("crash after native checkpoint")

    monkeypatch.setattr(service.runtime, "handle_authorized", crashed_after)
    with pytest.raises(RuntimeError, match="crash"):
        service.prepare(actor, workspace_id, request)
    pending = service.read(actor, workspace_id)
    assert pending.checkpoint_id is None
    checkpoints = service.runtime.checkpoints_authorized("run-1", actor)
    assert len(checkpoints) == 1
    monkeypatch.setattr(service.runtime, "handle_authorized", native)
    recovered = WorkspaceCreationService(app).prepare(actor, workspace_id, request)
    assert (
        recovered.operation_id == pending.operation_id
        and recovered.checkpoint_id == checkpoints[0].checkpoint_id
    )
    assert len(service.runtime.checkpoints_authorized("run-1", actor)) == 1


@pytest.mark.parametrize("boundary", ["plan", "approval", "lease", "stop", "permission", "expired"])
def test_revoked_creation_authority_never_persists_intent(creation, boundary, monkeypatch):
    app, actor, _, service, workspace_id, request, scope = creation
    if boundary == "plan":
        request = request.model_copy(update={"expected_plan_hash": "0" * 64})
    elif boundary == "approval":
        request = request.model_copy(update={"approval_id": "unknown-approval"})
    elif boundary == "lease":
        request = request.model_copy(update={"lease_token": "wrong-lease"})
    elif boundary == "permission":
        app.state.identity_service.assign_permission(
            actor.actor_id, AssignPermissionRequest(effect="deny", **scope)
        )
    elif boundary == "stop":
        with service.repository.leases._write() as session:
            session.get(SystemStateRow, 1).emergency_stop = True
    else:
        from app.self_build import creation_service

        class ExpiredClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.now(tz) + timedelta(seconds=1000)

        monkeypatch.setattr(creation_service, "datetime", ExpiredClock)
    expected = {
        "plan": "SELF_BUILD_PLAN_CHANGED",
        "approval": "SELF_BUILD_APPROVAL_REQUIRED",
        "lease": "TASK_LEASE_LOST",
        "stop": "EMERGENCY_STOP_ACTIVE",
        "permission": "SELF_BUILD_CREATION_PERMISSION_DENIED",
        "expired": "SELF_BUILD_APPROVAL_REQUIRED",
    }
    with pytest.raises(DomainError) as failure:
        service.prepare(actor, workspace_id, request)
    assert failure.value.code == expected[boundary]
    with app.state.repository.session_factory() as session:
        assert session.get(DevelopmentWorkspaceRow, workspace_id).creation_json is None


@pytest.mark.parametrize("method", ["prepare", "create"])
def test_disabled_remote_operator_rejects_persisted_approval_without_effects(creation, method):
    app, actor, _, service, workspace_id, request, _ = creation
    del app.state.remote_control_service
    with pytest.raises(DomainError) as failure:
        getattr(service, method)(actor, workspace_id, request)
    assert failure.value.code == "SELF_BUILD_APPROVAL_UNAVAILABLE"
    assert service.runtime.checkpoints_authorized("run-1", actor) == []
    with app.state.repository.session_factory() as session:
        assert session.get(DevelopmentWorkspaceRow, workspace_id).creation_json is None


def test_private_intent_tampering_blocks_recovery(creation):
    app, actor, _, service, workspace_id, request, _ = creation
    service.prepare(actor, workspace_id, request)
    with service.repository.leases._write() as session:
        row = session.get(DevelopmentWorkspaceRow, workspace_id)
        row.creation_json = row.creation_json | {"nonce": "0" * 64}
    with pytest.raises(DomainError) as failure:
        service.prepare(actor, workspace_id, request)
    assert failure.value.code == "SELF_BUILD_CREATION_RECORD_INVALID"


def test_creation_migration_refuses_to_discard_private_intent(creation):
    app, actor, _, service, workspace_id, request, _ = creation
    service.prepare(actor, workspace_id, request)
    config = migration_config(Path(app.state.engine.url.database))
    with pytest.raises(RuntimeError, match="Export workspace creation intent"):
        command.downgrade(config, "20261006_12")
    assert service.read(actor, workspace_id).checkpoint_id


def test_application_restart_preserves_native_intent_and_checkpoint(creation):
    from app.main import create_app

    app, actor, _, service, workspace_id, request, _ = creation
    result = service.prepare(actor, workspace_id, request)
    restarted = create_app(delay_ms=1, database_url=str(app.state.engine.url))
    try:
        # Historical read remains available with mutation disabled by default.
        assert not restarted.state.settings.self_build_enabled
        assert WorkspaceCreationService(restarted).read(actor, workspace_id) == result
    finally:
        restarted.state.engine.dispose()


def test_revocation_inside_checkpoint_commit_guard_leaves_no_checkpoint(creation, monkeypatch):
    app, actor, _, service, workspace_id, request, scope = creation
    native = service.runtime.handle_authorized

    def revoked_before_commit(*args, **kwargs):
        app.state.identity_service.assign_permission(
            actor.actor_id, AssignPermissionRequest(effect="deny", **scope)
        )
        return native(*args, **kwargs)

    monkeypatch.setattr(service.runtime, "handle_authorized", revoked_before_commit)
    with pytest.raises(DomainError) as failure:
        service.prepare(actor, workspace_id, request)
    assert failure.value.code == "SELF_BUILD_CREATION_PERMISSION_DENIED"
    pending = service.read(actor, workspace_id)
    assert pending.checkpoint_id is None
    assert len(service.runtime.checkpoints_authorized("run-1", actor)) == 0
    assert list(Path(service.workspace.policies.get("jarvis").worktree_root).iterdir()) == []


def test_unacknowledged_native_checkpoint_mismatch_blocks_recovery(creation, monkeypatch):
    from app.models.agent_runtime import AgentRunCheckpoint

    app, actor, _, service, workspace_id, request, _ = creation
    result = service.prepare(actor, workspace_id, request)
    checkpoint = service.runtime.checkpoints_authorized("run-1", actor)[0]
    corrupted = AgentRunCheckpoint.model_validate(
        checkpoint.model_dump() | {"resume_cursor": "invented"}
    )
    monkeypatch.setattr(service.runtime, "checkpoints_authorized", lambda *args: (corrupted,))
    with pytest.raises(DomainError) as failure:
        service.prepare(actor, workspace_id, request)
    assert failure.value.code == "SELF_BUILD_CREATION_CHECKPOINT_INVALID"
    with app.state.repository.session_factory() as session:
        assert (
            session.get(DevelopmentWorkspaceRow, workspace_id).creation_json["record"][
                "checkpoint_id"
            ]
            == result.checkpoint_id
        )


@pytest.mark.parametrize("gap", ["before_checkpoint", "acknowledged"])
def test_preparation_invalidates_prior_abandonment_approval(creation, gap, monkeypatch):
    from app.models.self_build import AbandonWorkspaceRequest, ApproveWorkspaceAbandonRequest

    app, actor, operator, service, workspace_id, request, _ = creation
    workspace = app.state.self_build_workspace_service
    prior = workspace.preview_abandon(actor, workspace_id)
    assert prior.creation_intent_digest is None
    approval = workspace.approve_abandon(
        operator,
        ApproveWorkspaceAbandonRequest(
            workspace_id=workspace_id, expected_plan_hash=prior.plan_hash
        ),
    )
    abandonment = AbandonWorkspaceRequest(
        expected_version=1,
        expected_plan_hash=prior.plan_hash,
        approval_id=approval.approval_id,
        worker_id=request.worker_id,
        lease_token=request.lease_token,
    )
    if gap == "before_checkpoint":

        def crash(*args, **kwargs):
            raise RuntimeError("crash before native checkpoint")

        monkeypatch.setattr(service.runtime, "handle_authorized", crash)
        with pytest.raises(RuntimeError, match="crash before native checkpoint"):
            service.prepare(actor, workspace_id, request)
    else:
        service.prepare(actor, workspace_id, request)
    current = workspace.preview_abandon(actor, workspace_id)
    assert current.creation_intent_digest is not None and current.plan_hash != prior.plan_hash
    with pytest.raises(DomainError) as failure:
        workspace.abandon(actor, workspace_id, abandonment)
    assert failure.value.code == "SELF_BUILD_PLAN_CHANGED"
    assert workspace.read(actor, workspace_id).state == "reserved"
    with app.state.repository.session_factory() as session:
        private = session.get(DevelopmentWorkspaceRow, workspace_id).creation_json
    fresh = workspace.approve_abandon(
        operator,
        ApproveWorkspaceAbandonRequest(
            workspace_id=workspace_id, expected_plan_hash=current.plan_hash
        ),
    )
    result = workspace.abandon(
        actor,
        workspace_id,
        abandonment.model_copy(
            update={"expected_plan_hash": current.plan_hash, "approval_id": fresh.approval_id}
        ),
    )
    assert result.state == "abandoned"
    with app.state.repository.session_factory() as session:
        assert session.get(DevelopmentWorkspaceRow, workspace_id).creation_json == private


def test_native_creation_ready_checkpoints_and_replay_preserve_primary(creation):
    app, actor, _, service, workspace_id, request, _ = creation
    policy = service.workspace.policies.get("jarvis")
    primary = Path(policy.primary_root)
    index = (primary / ".git/index").read_bytes()
    result = service.create(actor, workspace_id, request)
    assert result.state == "ready"
    assert result.completed_file_count == result.plan.file_count
    assert result.registration_digest and result.source_digest and result.checkpoint_id
    assert WorkspaceCreationService(app).read(actor, workspace_id) == result
    assert WorkspaceCreationService(app).create(actor, workspace_id, request) == result
    checkpoints = service.runtime.checkpoints_authorized("run-1", actor)
    assert {item.resume_cursor for item in checkpoints} == {
        "prepared",
        "git_created",
        "materializing-1",
        "ready",
    }
    assert (primary / ".git/index").read_bytes() == index
    target = Path(policy.worktree_root) / workspace_id
    assert (target / "source.py").read_bytes() == b"value = 1\n"
    assert (target / ".jarvis-workspace.json").is_file()


@pytest.mark.parametrize("phase", ["git_created", "materializing-1", "ready"])
def test_native_creation_checkpoint_crash_resumes_same_owned_effects(creation, monkeypatch, phase):
    app, actor, _, service, workspace_id, request, _ = creation
    native = service.runtime.handle_authorized

    def crash(command, *args, **kwargs):
        result = native(command, *args, **kwargs)
        if getattr(command, "resume_cursor", None) == phase:
            raise RuntimeError("after native phase before acknowledgement")
        return result

    monkeypatch.setattr(service.runtime, "handle_authorized", crash)
    with pytest.raises(RuntimeError, match="before acknowledgement"):
        service.create(actor, workspace_id, request)
    pending = service.read(actor, workspace_id)
    assert pending.checkpoint_id is None
    original_operation = pending.operation_id
    monkeypatch.setattr(service.runtime, "handle_authorized", native)
    result = WorkspaceCreationService(app).create(actor, workspace_id, request)
    assert result.state == "ready" and result.operation_id == original_operation
    assert len(service.runtime.checkpoints_authorized("run-1", actor)) == 4


def test_production_http_creation_requires_separate_operator_and_returns_native_ready(creation):
    from app.self_build.git_operator_router import router

    app, actor, _, _, workspace_id, request, _ = creation
    # Fixture enables remote access after create_app; production startup registers
    # this router only when remote_control_enabled is configured at startup.
    app.include_router(router)
    actor_headers = {"X-Jarvis-Actor-Id": actor.actor_id}
    with TestClient(app, base_url="https://testserver") as client:
        preview = client.post(
            f"/api/self-build/workspaces/{workspace_id}/creation/preview",
            headers=actor_headers,
            json={"inspection_id": request.inspection_id},
        )
        assert preview.status_code == 200
        approval_body = dict(
            workspace_id=workspace_id,
            inspection_id=request.inspection_id,
            expected_plan_hash=preview.json()["data"]["plan_hash"],
        )
        denied = client.post(
            "/api/remote/self-build/workspace-creation/approve",
            headers=actor_headers,
            json=approval_body,
        )
        assert denied.status_code == 401
        approved = client.post(
            "/api/remote/self-build/workspace-creation/approve",
            headers={"Authorization": "Bearer " + "x" * 48},
            json=approval_body,
        )
        assert approved.status_code == 200
        current_request = request.model_copy(
            update={"approval_id": approved.json()["data"]["approval_id"]}
        )
        result = client.post(
            f"/api/self-build/workspaces/{workspace_id}/creation",
            headers=actor_headers,
            json=current_request.model_dump(mode="json"),
        )
        assert result.status_code == 200, result.text
        assert result.json()["data"]["state"] == "ready"
        observed = client.get(
            f"/api/self-build/workspaces/{workspace_id}/creation", headers=actor_headers
        )
        assert observed.status_code == 200 and observed.json()["data"] == result.json()["data"]
    schema = app.openapi()
    assert "WorkspaceCreationPlan" in schema["components"]["schemas"]
    assert "WorkspaceCreationRecord" in schema["components"]["schemas"]


def test_legacy_prepared_projection_remains_readable_after_schema_extension(creation):
    from app.self_build.creation_service import persisted_intent_digest
    from app.self_build.policy import digest

    _, actor, _, service, workspace_id, request, _ = creation
    service.prepare(actor, workspace_id, request)
    with service.repository.leases._write() as session:
        row = session.get(DevelopmentWorkspaceRow, workspace_id)
        private = dict(row.creation_json)
        raw = dict(private["record"])
        raw.pop("registration_digest")
        raw.pop("source_digest")
        raw["checkpoint_id"] = None
        plan = dict(raw["plan"])
        for field in (
            "mutation_platform",
            "maximum_initial_metadata_nodes",
            "metadata_growth_reserve",
        ):
            plan.pop(field)
        plan["plan_hash"] = digest(
            {key: value for key, value in plan.items() if key != "plan_hash"}
        )
        raw["plan"] = plan
        raw["ownership_digest"] = digest(
            dict(
                workspaceId=workspace_id,
                operationId=raw["operation_id"],
                planHash=plan["plan_hash"],
                nonce=private["nonce"],
            )
        )
        private.update(record=raw, digest=persisted_intent_digest(raw, private["nonce"]))
        row.creation_json = private
    historical = service.read(actor, workspace_id)
    assert historical.state == "prepared" and historical.checkpoint_id is None
    with pytest.raises(DomainError) as failure:
        service.create(actor, workspace_id, request)
    assert failure.value.code == "SELF_BUILD_CREATION_CONFLICT"


def test_revocation_after_registration_preserves_effects_without_ready(creation, monkeypatch):
    _, actor, _, service, workspace_id, request, _ = creation
    transition = service.transition

    def stopped(actor, workspace_id, request, previous, **changes):
        if changes.get("state") == "git_created":
            with service.repository.leases._write() as session:
                session.get(SystemStateRow, 1).emergency_stop = True
        return transition(actor, workspace_id, request, previous, **changes)

    monkeypatch.setattr(service, "transition", stopped)
    with pytest.raises(DomainError) as failure:
        service.create(actor, workspace_id, request)
    assert failure.value.code == "EMERGENCY_STOP_ACTIVE"
    policy = service.workspace.policies.get("jarvis")
    target = Path(policy.worktree_root) / workspace_id
    assert (target / ".git").is_file() and not (target / "source.py").exists()
    assert service.read(actor, workspace_id).state == "prepared"


@pytest.mark.parametrize("after_native", [False, True])
def test_renewed_exact_operator_approval_recovers_original_preparation(
    creation, monkeypatch, after_native
):
    from app.self_build import creation_service

    _, actor, operator, service, workspace_id, request, _ = creation
    native = service.runtime.handle_authorized

    def crash(*args, **kwargs):
        if after_native:
            native(*args, **kwargs)
        raise RuntimeError("checkpoint crash gap")

    monkeypatch.setattr(service.runtime, "handle_authorized", crash)
    with pytest.raises(RuntimeError, match="crash gap"):
        service.prepare(actor, workspace_id, request)
    pending = service.read(actor, workspace_id)
    with service.repository.sessions() as session:
        original_private = dict(session.get(DevelopmentWorkspaceRow, workspace_id).creation_json)

    class ExpiredClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(seconds=1000)

    monkeypatch.setattr(creation_service, "datetime", ExpiredClock)
    with pytest.raises(DomainError) as failure:
        service.prepare(actor, workspace_id, request)
    assert failure.value.code == "SELF_BUILD_APPROVAL_REQUIRED"
    renewed = service.approve(
        operator,
        ApproveWorkspaceCreation(
            workspace_id=workspace_id,
            inspection_id=request.inspection_id,
            expected_plan_hash=request.expected_plan_hash,
        ),
    )
    monkeypatch.setattr(service.runtime, "handle_authorized", native)
    recovered = service.prepare(
        actor, workspace_id, request.model_copy(update={"approval_id": renewed.approval_id})
    )
    assert recovered.operation_id == pending.operation_id
    assert recovered.approval_id == pending.approval_id != renewed.approval_id
    assert (
        recovered.checkpoint_id and len(service.runtime.checkpoints_authorized("run-1", actor)) == 1
    )
    with service.repository.sessions() as session:
        current = session.get(DevelopmentWorkspaceRow, workspace_id).creation_json
        assert current["digest"] == original_private["digest"]
        assert current["nonce"] == original_private["nonce"]


@pytest.mark.parametrize("git_workspace", ["empty"], indirect=True)
def test_native_empty_base_has_complete_verified_ready_checkpoint(creation):
    _, actor, _, service, workspace_id, request, _ = creation
    result = service.create(actor, workspace_id, request)
    assert result.state == "ready" and result.completed_file_count == result.plan.file_count == 0
    assert result.source_digest and result.registration_digest and result.checkpoint_id
    assert {
        item.resume_cursor for item in service.runtime.checkpoints_authorized("run-1", actor)
    } == {"prepared", "git_created", "ready"}
    policy = service.workspace.policies.get("jarvis")
    assert {path.name for path in (Path(policy.worktree_root) / workspace_id).iterdir()} == {
        ".git",
        ".jarvis-workspace.json",
    }


def test_final_artifacts_remain_deny_write_pinned_through_native_ready_publication(
    creation, monkeypatch
):
    _, actor, _, service, workspace_id, request, _ = creation
    policy = service.workspace.policies.get("jarvis")
    target = Path(policy.worktree_root) / workspace_id
    index = Path(policy.primary_root) / ".git/worktrees" / workspace_id / "index"
    owner = Path(policy.worktree_root) / (".jarvis-owner-" + workspace_id) / "owner.json"
    native = service.runtime.handle_authorized
    blocked = []

    def guarded(command, *args, **kwargs):
        if getattr(command, "resume_cursor", None) == "ready":
            for path in (
                target / "source.py",
                target / ".git",
                target / ".jarvis-workspace.json",
                index,
                owner,
            ):
                with pytest.raises(PermissionError):
                    with path.open("r+b") as stream:
                        stream.write(b"foreign")
                blocked.append(path.name)
        return native(command, *args, **kwargs)

    monkeypatch.setattr(service.runtime, "handle_authorized", guarded)
    result = service.create(actor, workspace_id, request)
    assert result.state == "ready" and len(blocked) == 5
    assert (target / "source.py").read_bytes() == b"value = 1\n"
    # Publication releases every pin; later operator changes are separate effects.
    with (target / "source.py").open("r+b") as stream:
        stream.write(b"value = 2\n")


def test_finalization_recovery_reverifies_files_before_acknowledging_ready(creation, monkeypatch):
    _, actor, _, service, workspace_id, request, _ = creation
    native = service.runtime.handle_authorized

    def crash(command, *args, **kwargs):
        result = native(command, *args, **kwargs)
        if getattr(command, "resume_cursor", None) == "ready":
            raise RuntimeError("native ready before projection ack")
        return result

    monkeypatch.setattr(service.runtime, "handle_authorized", crash)
    with pytest.raises(RuntimeError, match="projection ack"):
        service.create(actor, workspace_id, request)
    target = Path(service.workspace.policies.get("jarvis").worktree_root) / workspace_id
    (target / "source.py").write_bytes(b"value = 9\n")
    monkeypatch.setattr(service.runtime, "handle_authorized", native)
    with pytest.raises(DomainError) as failure:
        service.create(actor, workspace_id, request)
    assert failure.value.code == "SELF_BUILD_MATERIALIZATION_CONFLICT"
    assert service.read(actor, workspace_id).state == "finalizing"
    assert (target / "source.py").read_bytes() == b"value = 9\n"


@pytest.mark.parametrize("field", ["original_approval_id", "plan", "operation_id", "created_at"])
def test_source_mutation_transition_preserves_original_references(editing, field):
    from app.source_mutation.repository import SourceMutationRow

    _, actor, _, service, request, target, _ = editing
    previous = service.prepare(actor, request)
    with pytest.raises(DomainError) as failure:
        service.transition(actor, request, previous, **{field: "foreign"})
    assert failure.value.code == "SOURCE_MUTATION_TRANSITION_INVALID"
    assert (target / "source.py").read_bytes() == b"value = 1\n"
    with service.repository.sessions() as session:
        assert (
            service.intent(
                session.get(SourceMutationRow, request.editing_record_id), previous.plan, session
            )
            == previous
        )


def test_source_mutation_coherently_rehashed_foreign_original_approval_refuses(editing):
    from app.source_mutation.repository import SourceMutationRow
    from app.source_mutation.service import intent_digest

    _, actor, _, service, request, target, _ = editing
    previous = service.prepare(actor, request)
    changed = previous.model_copy(update={"original_approval_id": "foreign"})
    with service.repository.leases._write() as session:
        row = session.get(SourceMutationRow, request.editing_record_id)
        row.payload = changed.model_dump(mode="json")
        row.integrity_digest = intent_digest(changed)
    with pytest.raises(DomainError) as failure:
        service.apply(actor, request)
    assert failure.value.code == "SOURCE_MUTATION_INTENT_INVALID"
    assert (target / "source.py").read_bytes() == b"value = 1\n"


def test_protected_complete_history_survives_stop_without_claiming_current_source(editing):
    app, actor, _, service, request, _, _ = editing
    completed = service.apply(actor, request)
    with service.repository.leases._write() as session:
        session.get(SystemStateRow, 1).emergency_stop = True
    result = history.MutationHistoryReader(app).read(actor, request.editing_record_id)
    assert result.record == completed
    assert result.native_checkpoint_present and result.journal_checkpoint_acknowledged
    assert not result.execution_authorized and not result.current_source_measured
    assert "value =" not in result.model_dump_json()
    assert request.lease_token not in result.model_dump_json()


def test_pending_unacknowledged_history_does_not_write_or_grant_execution(editing, monkeypatch):
    from app.source_mutation.driver import SourceMutationDriver

    app, actor, _, service, request, target, _ = editing
    record = service.prepare(actor, request)

    def forbidden(*args, **kwargs):
        raise AssertionError("read cannot execute files")

    monkeypatch.setattr(SourceMutationDriver, "apply", forbidden)
    result = history.MutationHistoryReader(app).read(actor, request.editing_record_id)
    assert result.record == record and not result.native_checkpoint_present
    assert not result.execution_authorized and not result.journal_checkpoint_acknowledged
    assert (target / "source.py").read_bytes() == b"value = 1\n"


@pytest.mark.parametrize("forged_checkpoint_id", [False, True])
def test_native_checkpoint_without_audit_acknowledgement_remains_unacknowledged(
    editing, monkeypatch, forged_checkpoint_id
):
    app, actor, _, service, request, _, _ = editing
    record = service.prepare(actor, request)
    event = service.event

    def crash_acknowledgement(session, row, actor, current, phase, approval_id):
        if phase == "checkpoint_acknowledged":
            raise RuntimeError("crash before durable acknowledgement")
        return event(session, row, actor, current, phase, approval_id)

    monkeypatch.setattr(service, "event", crash_acknowledgement)
    with pytest.raises(RuntimeError, match="durable acknowledgement"):
        service.acknowledge(actor, request, record)
    checkpoint, identifier, _, _ = service.checkpoint(actor, record)
    assert checkpoint is not None
    if forged_checkpoint_id:
        with service.repository.leases._write() as session:
            stored = session.get(SourceMutationRow, request.editing_record_id)
            stored.payload = stored.payload | {"checkpoint_id": identifier}
            assert stored.integrity_digest == intent_digest(
                record.model_copy(update={"checkpoint_id": identifier})
            )
    result = history.MutationHistoryReader(app).read(actor, request.editing_record_id)
    assert result.native_checkpoint_present
    assert not result.journal_checkpoint_acknowledged


def test_rehashed_foreign_attempt_cannot_replace_historical_provenance(editing):
    app, actor, _, service, request, _, _ = editing
    record = service.prepare(actor, request)
    payload = record.plan.model_dump(mode="json", exclude={"plan_hash"}) | {
        "attempt_id": "foreign-attempt"
    }
    altered_plan = record.plan.model_copy(update=payload | {"plan_hash": digest(payload)})
    altered = record.model_copy(update={"plan": altered_plan})
    with service.repository.leases._write() as session:
        stored = session.get(SourceMutationRow, request.editing_record_id)
        stored.payload = altered.model_dump(mode="json")
        stored.integrity_digest = intent_digest(altered)
    with pytest.raises(DomainError) as failure:
        history.MutationHistoryReader(app).read(actor, request.editing_record_id)
    assert failure.value.code == "SOURCE_MUTATION_HISTORY_INVALID"


@pytest.mark.parametrize("field", ["operationId", "editingRecordId", "planHash", "intentDigest"])
def test_history_acknowledgement_requires_exact_durable_audit_lineage(editing, field):
    app, actor, _, service, request, _, _ = editing
    record = service.acknowledge(actor, request, service.prepare(actor, request))
    with service.repository.leases._write() as session:
        event = session.scalar(
            select(AuditEventRow).where(
                AuditEventRow.event_type == "self_build.source_mutation.checkpoint_acknowledged"
            )
        )
        event.payload = event.payload | {"payload": event.payload["payload"] | {field: "foreign"}}
    result = history.MutationHistoryReader(app).read(actor, request.editing_record_id)
    assert result.record == record and result.native_checkpoint_present
    assert not result.journal_checkpoint_acknowledged


def test_valid_mutation_checkpoint_cannot_hide_missing_creation_checkpoint(editing, monkeypatch):
    app, actor, _, service, request, _, _ = editing
    service.apply(actor, request)
    native = service.runtime.checkpoints_authorized

    def missing_creation(*args):
        return tuple(
            item
            for item in native(*args)
            if not item.state_reference.startswith("workspace-creation:")
        )

    monkeypatch.setattr(service.runtime, "checkpoints_authorized", missing_creation)
    with pytest.raises(DomainError) as failure:
        history.MutationHistoryReader(app).read(actor, request.editing_record_id)
    assert failure.value.code == "SOURCE_MUTATION_CREATION_CHECKPOINT_INVALID"


def test_historical_receipt_does_not_claim_current_bytes_after_source_drift(editing):
    app, actor, _, service, request, target, _ = editing
    recorded = service.apply(actor, request)
    (target / "source.py").write_bytes(b"later operator edit\n")
    result = history.MutationHistoryReader(app).read(actor, request.editing_record_id)
    assert result.record == recorded and result.journal_checkpoint_acknowledged
    assert not result.current_source_measured and not result.execution_authorized
    assert (target / "source.py").read_bytes() == b"later operator edit\n"


def test_task_read_alone_does_not_reveal_protected_mutation_history(editing, monkeypatch):
    from types import SimpleNamespace

    app, actor, _, service, request, _, _ = editing
    service.prepare(actor, request)
    native = service.workspace.identity.check_permission_resource_access

    def denied(actor_id, permission_key, *args, **kwargs):
        if permission_key == "self_build.workspace":
            return SimpleNamespace(allowed=False)
        return native(actor_id, permission_key, *args, **kwargs)

    monkeypatch.setattr(service.workspace.identity, "check_permission_resource_access", denied)
    with pytest.raises(DomainError) as failure:
        history.MutationHistoryReader(app).read(actor, request.editing_record_id)
    assert failure.value.code == "SOURCE_EDIT_PERMISSION_DENIED"


def test_historical_self_approval_tampering_is_not_valid_provenance(editing):
    from app.db.models import AuditEventRow

    app, actor, _, service, request, _, _ = editing
    record = service.prepare(actor, request)
    with service.repository.leases._write() as session:
        session.get(AuditEventRow, record.original_approval_id).actor = actor.actor_id
    with pytest.raises(DomainError) as failure:
        history.MutationHistoryReader(app).read(actor, request.editing_record_id)
    assert failure.value.code == "SOURCE_MUTATION_HISTORY_INVALID"


def test_corrupt_mutation_checkpoint_cannot_be_presented_as_acknowledged_history(
    editing, monkeypatch
):
    app, actor, _, service, request, _, _ = editing
    record = service.prepare(actor, request)
    service.acknowledge(actor, request, record)
    checkpoints = service.runtime.checkpoints_authorized(record.plan.runtime_run_id, actor)
    native = service.runtime.checkpoints_authorized

    def altered(*args):
        return tuple(
            item.model_copy(update={"integrity_digest": "sha256:" + "0" * 64})
            if item.state_reference.startswith("source-mutation:")
            else item
            for item in native(*args)
        )

    assert any(item.state_reference.startswith("source-mutation:") for item in checkpoints)
    monkeypatch.setattr(service.runtime, "checkpoints_authorized", altered)
    with pytest.raises(DomainError) as failure:
        history.MutationHistoryReader(app).read(actor, request.editing_record_id)
    assert failure.value.code == "SOURCE_MUTATION_CHECKPOINT_INVALID"


@pytest.mark.parametrize("expiry", ["invalid", "2026-10-12T00:00:00"])
def test_history_refuses_invalid_original_approval_expiry(editing, expiry):
    from app.db.models import AuditEventRow

    app, actor, _, service, request, _, _ = editing
    record = service.prepare(actor, request)
    with service.repository.leases._write() as session:
        event = session.get(AuditEventRow, record.original_approval_id)
        event.payload = event.payload | {
            "payload": event.payload["payload"] | {"expiresAt": expiry}
        }
    with pytest.raises(DomainError) as failure:
        history.MutationHistoryReader(app).read(actor, request.editing_record_id)
    assert failure.value.code == "SOURCE_MUTATION_HISTORY_INVALID"


def test_history_accepts_expired_original_provenance_without_execution_authority(editing):
    from pydantic import ValidationError

    from app.db.models import AuditEventRow
    from app.models.source_mutation_history import MutationHistory

    app, actor, _, service, request, target, _ = editing
    record = service.prepare(actor, request)
    with service.repository.leases._write() as session:
        event = session.get(AuditEventRow, record.original_approval_id)
        event.payload = event.payload | {
            "payload": event.payload["payload"] | {"expiresAt": "2000-01-01T00:00:00+00:00"}
        }
    result = history.MutationHistoryReader(app).read(actor, request.editing_record_id)
    assert result.record == record and not result.execution_authorized
    for flag in ("execution_authorized", "current_source_measured"):
        with pytest.raises(ValidationError):
            MutationHistory.model_validate(result.model_dump() | {flag: True})
    assert (target / "source.py").read_bytes() == b"value = 1\n"


def test_history_requires_a_persisted_mutation_intent(editing):
    app, actor, _, _, request, target, _ = editing
    with pytest.raises(DomainError) as failure:
        history.MutationHistoryReader(app).read(actor, request.editing_record_id)
    assert failure.value.code == "SOURCE_MUTATION_INTENT_REQUIRED"
    assert (target / "source.py").read_bytes() == b"value = 1\n"


def test_history_storage_failure_never_exposes_database_details():
    from types import SimpleNamespace

    from sqlalchemy.exc import SQLAlchemyError

    def failed(*args):
        raise SQLAlchemyError("private source text and lease token")

    reader = history.MutationHistoryReader.__new__(history.MutationHistoryReader)
    reader.service = SimpleNamespace(planning=SimpleNamespace(read=failed))
    with pytest.raises(DomainError) as failure:
        reader.read(None, "0" * 64)
    assert failure.value.code == "SOURCE_MUTATION_STORAGE_FAILED"
    assert "private" not in str(failure.value)
    assert failure.value.__suppress_context__


def test_native_source_inspection_measures_completed_state_without_effects(editing, monkeypatch):
    from app.source_mutation.inspection import MutationInspector

    app, actor, _, service, request, target, _ = editing
    completed = service.apply(actor, request)
    inspector = MutationInspector(app)
    before = {
        path.relative_to(target).as_posix(): path.read_bytes()
        for path in target.rglob("*")
        if path.is_file()
    }

    def forbidden(*args, **kwargs):
        raise AssertionError("inspection cannot apply source effects or checkpoints")

    for name in ("apply", "prepare", "transition", "acknowledge"):
        monkeypatch.setattr(inspector.service, name, forbidden)
    result = inspector.inspect(actor, request)
    repeated = inspector.inspect(actor, request)
    assert result.operation_id == completed.operation_id
    assert result.checkpoint_id == completed.checkpoint_id
    assert result.matches_completed_mutation and result.current_source_measured
    assert not result.execution_authorized
    assert result.inspection_fingerprint == repeated.inspection_fingerprint
    assert "value =" not in result.model_dump_json()
    assert request.lease_token not in result.model_dump_json()
    assert before == {
        path.relative_to(target).as_posix(): path.read_bytes()
        for path in target.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize("drift", ["selected", "unrelated"])
def test_native_source_inspection_reports_current_drift_without_repair(editing, drift):
    from app.source_mutation.inspection import MutationInspector

    app, actor, _, service, request, target, _ = editing
    service.apply(actor, request)
    path = target / ("source.py" if drift == "selected" else "later.py")
    path.write_bytes(b"later operator edit\n")
    result = MutationInspector(app).inspect(actor, request)
    assert not result.matches_completed_mutation
    assert result.current_source_measured and not result.execution_authorized
    assert path.read_bytes() == b"later operator edit\n"
    assert "later operator edit" not in result.model_dump_json()


@pytest.mark.parametrize("loss", ["approval", "lease", "hash", "stop"])
def test_native_source_inspection_requires_original_live_exact_authority(editing, loss):
    from app.source_mutation.inspection import MutationInspector

    app, actor, _, service, request, target, _ = editing
    service.apply(actor, request)
    if loss == "stop":
        with service.repository.leases._write() as session:
            session.get(SystemStateRow, 1).emergency_stop = True
    else:
        changes = {
            "approval": {"approval_id": "foreign"},
            "lease": {"lease_token": "foreign"},
            "hash": {"expected_plan_hash": "0" * 64},
        }
        request = request.model_copy(update=changes[loss])
    with pytest.raises(DomainError):
        MutationInspector(app).inspect(actor, request)
    assert (target / "source.py").read_bytes() == b"value = 2\n"


def test_native_source_inspection_requires_durable_completion_acknowledgement(editing):
    from app.source_mutation.inspection import MutationInspector

    app, actor, _, service, request, target, _ = editing
    service.apply(actor, request)
    before = (target / "source.py").read_bytes()
    with service.repository.leases._write() as session:
        event = session.scalar(
            select(AuditEventRow)
            .where(AuditEventRow.event_type == "self_build.source_mutation.checkpoint_acknowledged")
            .order_by(AuditEventRow.sequence_number.desc())
        )
        event.payload = event.payload | {
            "payload": event.payload["payload"] | {"intentDigest": "foreign"}
        }
    with pytest.raises(DomainError) as failure:
        MutationInspector(app).inspect(actor, request)
    assert failure.value.code == "SOURCE_MUTATION_CHECKPOINT_INVALID"
    assert (target / "source.py").read_bytes() == before


def test_native_source_inspection_refuses_incomplete_journal_without_effects(editing):
    from app.source_mutation.inspection import MutationInspector

    app, actor, _, service, request, target, _ = editing
    service.prepare(actor, request)
    with pytest.raises(DomainError) as failure:
        MutationInspector(app).inspect(actor, request)
    assert failure.value.code == "SOURCE_MUTATION_INSPECTION_INCOMPLETE"
    assert (target / "source.py").read_bytes() == b"value = 1\n"


@pytest.mark.parametrize("artifact", ["original", "stage", "owner", "claim", "marker"])
def test_native_source_inspection_refuses_tampered_artifacts_and_preserves_them(editing, artifact):
    from app.source_mutation.driver import staging_names
    from app.source_mutation.inspection import MutationInspector

    app, actor, _, service, request, target, _ = editing
    completed = service.apply(actor, request)
    private = target.parent / (".jarvis-owner-" + target.name)
    before, after = staging_names(completed.operation_id, 0)
    paths = {
        "original": target / before,
        "stage": target / after,
        "owner": private / "owner.json",
        "claim": private / "target.json",
        "marker": target / ".jarvis-workspace.json",
    }
    path = paths[artifact]
    path.write_bytes(b"foreign artifact")
    with pytest.raises(DomainError):
        MutationInspector(app).inspect(actor, request)
    assert path.read_bytes() == b"foreign artifact"
    assert (target / "source.py").read_bytes() == b"value = 2\n"


def test_native_source_inspection_never_recreates_a_missing_owned_namespace(editing):
    from app.source_mutation.inspection import MutationInspector

    app, actor, _, service, request, target, _ = editing
    service.apply(actor, request)
    private = target.parent / (".jarvis-owner-" + target.name)
    (private / "owner.json").unlink()
    with pytest.raises(DomainError):
        MutationInspector(app).inspect(actor, request)
    assert not (private / "owner.json").exists()
    assert (target / "source.py").read_bytes() == b"value = 2\n"


@pytest.mark.parametrize("identity", ["hash", "path"])
def test_native_source_inspection_rejects_substituted_actual_observer(
    editing, monkeypatch, identity
):
    from app.source_mutation import inspection

    app, actor, _, service, request, target, _ = editing
    service.apply(actor, request)
    native = inspection.CheckoutDriver

    def substituted(observer, authority_check):
        if identity == "hash":
            observer.executable_hash = "0" * 64
        else:
            observer.executable = Path(str(observer.executable).swapcase())
        result = native(observer, authority_check)

        def forbidden(*args, **kwargs):
            raise AssertionError("substituted native tool cannot run")

        result.invoke = forbidden
        return result

    monkeypatch.setattr(inspection, "CheckoutDriver", substituted)
    with pytest.raises(DomainError) as failure:
        inspection.MutationInspector(app).inspect(actor, request)
    assert failure.value.code == "SOURCE_MUTATION_TOOL_CHANGED"
    assert (target / "source.py").read_bytes() == b"value = 2\n"


@pytest.mark.parametrize("topology", ["hardlink", "case_alias"])
def test_native_source_inspection_refuses_unsafe_source_topology(editing, topology):
    import os

    from app.source_mutation.inspection import MutationInspector

    app, actor, _, service, request, target, _ = editing
    service.apply(actor, request)
    if topology == "hardlink":
        os.link(target / "source.py", target / "alias.py")
    else:
        (target / "source.py").rename(target / "SOURCE.py")
    with pytest.raises(DomainError):
        MutationInspector(app).inspect(actor, request)
    assert (target / "source.py").read_bytes() == b"value = 2\n"


def test_native_source_inspection_source_and_metadata_remain_read_pinned(editing, monkeypatch):
    from app.source_mutation.inspection import MutationInspector

    app, actor, _, service, request, target, _ = editing
    service.apply(actor, request)
    inspector = MutationInspector(app)
    native = inspector._read
    blocked = []

    def guarded(directory, name, limit):
        data = native(directory, name, limit)
        if name.endswith(".before"):
            private = target.parent / (".jarvis-owner-" + target.name)
            for path in (
                target / "source.py",
                target / ".git",
                target / ".jarvis-workspace.json",
                private / "owner.json",
                private / "target.json",
            ):
                with pytest.raises(PermissionError):
                    with path.open("r+b") as stream:
                        stream.write(b"foreign")
                blocked.append(path.name)
        return data

    monkeypatch.setattr(inspector, "_read", guarded)
    result = inspector.inspect(actor, request)
    assert result.matches_completed_mutation and len(blocked) == 5


def test_native_source_inspection_revocation_during_scan_never_returns_measurement(
    editing, monkeypatch
):
    from app.source_mutation.inspection import MutationInspector

    app, actor, _, service, request, target, _ = editing
    service.apply(actor, request)
    inspector = MutationInspector(app)
    native = inspector._read

    def stopped(directory, name, limit):
        data = native(directory, name, limit)
        if name.endswith(".before"):
            with service.repository.leases._write() as session:
                session.get(SystemStateRow, 1).emergency_stop = True
        return data

    monkeypatch.setattr(inspector, "_read", stopped)
    with pytest.raises(DomainError):
        inspector.inspect(actor, request)
    assert (target / "source.py").read_bytes() == b"value = 2\n"


def test_native_source_inspection_rechecks_native_completion_checkpoint_before_return(
    editing, monkeypatch
):
    from app.source_mutation.inspection import MutationInspector

    app, actor, _, service, request, target, _ = editing
    service.apply(actor, request)
    inspector = MutationInspector(app)
    read = inspector._read
    native = service.runtime.checkpoints_authorized

    def corrupted(*args):
        return tuple(
            item.model_copy(update={"integrity_digest": "sha256:" + "0" * 64})
            if item.state_reference.startswith("source-mutation:")
            else item
            for item in native(*args)
        )

    def guarded(directory, name, limit):
        data = read(directory, name, limit)
        if name.endswith(".before"):
            monkeypatch.setattr(service.runtime, "checkpoints_authorized", corrupted)
        return data

    monkeypatch.setattr(inspector, "_read", guarded)
    with pytest.raises(DomainError) as failure:
        inspector.inspect(actor, request)
    assert failure.value.code == "SOURCE_MUTATION_CHECKPOINT_INVALID"
    assert (target / "source.py").read_bytes() == b"value = 2\n"


@pytest.mark.parametrize("editing", ["add", "delete"], indirect=True)
def test_native_source_inspection_handles_add_and_delete_completed_state(editing):
    from app.source_mutation.inspection import MutationInspector

    app, actor, _, service, request, target, _ = editing
    completed = service.apply(actor, request)
    result = MutationInspector(app).inspect(actor, request)
    assert result.matches_completed_mutation and not result.execution_authorized
    assert result.operation_id == completed.operation_id
    assert len(result.code_state.changes) == 1
    delta = result.code_state.changes[0]
    if delta.path == "added.py":
        assert delta.before_hash is None and delta.after_hash
        assert (target / "added.py").read_bytes() == b"value = 2\n"
    else:
        assert delta.before_hash and delta.after_hash is None
        assert not (target / "source.py").exists()


def test_native_source_inspection_refuses_empty_foreign_artifact_directory(editing):
    from app.source_mutation.inspection import MutationInspector

    app, actor, _, service, request, target, _ = editing
    service.apply(actor, request)
    foreign = target / ".jarvis-foreign"
    foreign.mkdir()
    with pytest.raises(DomainError) as failure:
        MutationInspector(app).inspect(actor, request)
    assert failure.value.code == "SOURCE_MUTATION_FOREIGN_ARTIFACT"
    assert foreign.is_dir()


def test_native_source_inspection_rejects_case_colliding_sibling_directories(monkeypatch, tmp_path):
    from app.source_mutation import inspection

    monkeypatch.setattr(
        inspection.os, "walk", lambda *args, **kwargs: [(str(tmp_path), ["Alpha", "alpha"], [])]
    )
    with pytest.raises(DomainError) as failure:
        inspection.MutationInspector._check_namespace_names(tmp_path, set())
    assert failure.value.code == "SOURCE_MUTATION_INSPECTION_PATH_COLLISION"


def test_native_source_inspection_absence_probe_never_swallows_access_denial(monkeypatch, tmp_path):
    from contextlib import ExitStack, contextmanager

    from app.source_mutation import inspection

    @contextmanager
    def denied(path):
        raise PermissionError("denied")
        yield

    monkeypatch.setattr(inspection, "read_pin", denied)
    with ExitStack() as stack, pytest.raises(PermissionError):
        inspection.MutationInspector._require_absent(stack, tmp_path / "artifact")


def test_native_source_inspection_refuses_credential_names_before_content_reads(
    monkeypatch, tmp_path
):
    from app.source_mutation import inspection

    monkeypatch.setattr(
        inspection.os, "walk", lambda *args, **kwargs: [(str(tmp_path), [], [".env"])]
    )
    with pytest.raises(DomainError) as failure:
        inspection.MutationInspector._check_namespace_names(tmp_path, set())
    assert failure.value.code == "TOOL_PATH_DENIED"
    assert ".env" not in str(failure.value)


def test_expected_artifact_manifest_protected_read_survives_stop_without_native_claims(editing):
    from app.source_mutation.artifacts import ArtifactManifestReader
    from app.source_mutation.driver import staging_names

    app, actor, _, service, request, target, _ = editing
    completed = service.apply(actor, request)
    reader = ArtifactManifestReader(app)
    result = reader.read(actor, request.editing_record_id)
    before, after = staging_names(completed.operation_id, 0)
    assert [item.path for item in result.artifacts] == [before, after]
    assert result.original_approval_id == completed.original_approval_id
    assert result.checkpoint_id == completed.checkpoint_id
    from hashlib import sha256

    assert (
        result.artifacts[0].expected_identity.digest
        == sha256((target / before).read_bytes()).hexdigest()
    )
    assert not result.native_artifacts_verified and not result.deletion_authorized
    assert not result.execution_authorized and not result.checkout_root_lock_allowed
    with service.repository.leases._write() as session:
        session.get(SystemStateRow, 1).emergency_stop = True
    assert reader.read(actor, request.editing_record_id) == result
    assert (target / "source.py").read_bytes() == b"value = 2\n"


def test_expected_artifact_manifest_pending_intent_refuses_without_source_effects(editing):
    from app.source_mutation.artifacts import ArtifactManifestReader

    app, actor, _, service, request, target, _ = editing
    service.prepare(actor, request)
    with pytest.raises(DomainError) as failure:
        ArtifactManifestReader(app).read(actor, request.editing_record_id)
    assert failure.value.code == "SOURCE_ARTIFACTS_INCOMPLETE"
    assert (target / "source.py").read_bytes() == b"value = 1\n"
    assert not list(target.glob(".jarvis-edit-*"))


def test_expected_artifact_manifest_storage_error_does_not_expose_database_details():
    from types import SimpleNamespace

    from sqlalchemy.exc import SQLAlchemyError

    from app.source_mutation.artifacts import ArtifactManifestReader

    def failed(*args):
        raise SQLAlchemyError("private source text and lease token")

    reader = ArtifactManifestReader.__new__(ArtifactManifestReader)
    reader.history = SimpleNamespace(read=failed)
    with pytest.raises(DomainError) as failure:
        reader.read(None, "0" * 64)
    assert failure.value.code == "SOURCE_ARTIFACTS_STORAGE_FAILED"
    assert "private" not in str(failure.value) and failure.value.__suppress_context__


@pytest.mark.parametrize("editing", ["add", "modify", "delete"], indirect=True)
def test_native_artifact_verification_measures_exact_bounded_contract_without_effects(
    editing, monkeypatch
):
    from app.source_mutation.artifacts import ArtifactManifestReader
    from app.source_mutation.inspection import MutationInspector

    app, actor, _, service, request, target, _ = editing
    service.apply(actor, request)
    before = {
        path.relative_to(target).as_posix(): path.read_bytes()
        for path in target.rglob("*")
        if path.is_file()
    }
    inspector = MutationInspector(app)

    def forbidden(*args, **kwargs):
        raise AssertionError("artifact verification must not perform mutation")

    for name in ("apply", "prepare", "transition", "acknowledge"):
        monkeypatch.setattr(inspector.service, name, forbidden)
    result = inspector.verify_artifacts(actor, request)
    repeated = inspector.verify_artifacts(actor, request)
    assert result.manifest == ArtifactManifestReader(app).read(actor, request.editing_record_id)
    assert result.native_artifacts_verified and result.source_inspection.matches_completed_mutation
    assert not result.execution_authorized and not result.deletion_authorized
    assert not result.durable_artifact_ownership_verified
    assert result.verification_fingerprint == repeated.verification_fingerprint
    for actual in result.measurements:
        path = target / actual.path
        if actual.presence == "retained_original":
            info = path.stat()
            assert (actual.device, actual.inode, actual.byte_count) == (
                info.st_dev,
                info.st_ino,
                info.st_size,
            )
        else:
            assert not path.exists() and actual.inode is None and actual.digest is None
    assert request.lease_token not in result.model_dump_json()
    assert "value =" not in result.model_dump_json()
    assert before == {
        path.relative_to(target).as_posix(): path.read_bytes()
        for path in target.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize("loss", ["approval", "lease", "hash", "stop"])
def test_native_artifact_verification_requires_current_original_authority(editing, loss):
    from app.source_mutation.inspection import MutationInspector

    app, actor, _, service, request, target, _ = editing
    service.apply(actor, request)
    if loss == "stop":
        with service.repository.leases._write() as session:
            session.get(SystemStateRow, 1).emergency_stop = True
    else:
        request = request.model_copy(
            update={
                "approval": {"approval_id": "foreign"},
                "lease": {"lease_token": "foreign"},
                "hash": {"expected_plan_hash": "0" * 64},
            }[loss]
        )
    with pytest.raises(DomainError):
        MutationInspector(app).verify_artifacts(actor, request)
    assert (target / "source.py").read_bytes() == b"value = 2\n"


@pytest.mark.parametrize("artifact", ["backup", "stage", "root_lock", "hardlink"])
def test_native_artifact_verification_refuses_foreign_artifacts_without_cleanup(editing, artifact):
    import os

    from app.source_mutation.driver import staging_names
    from app.source_mutation.inspection import MutationInspector

    app, actor, _, service, request, target, _ = editing
    completed = service.apply(actor, request)
    before, after = staging_names(completed.operation_id, 0)
    if artifact == "hardlink":
        path = target / "alias.py"
        os.link(target / before, path)
    else:
        path = (
            target
            / {"backup": before, "stage": after, "root_lock": ".jarvis-workspace.lock"}[artifact]
        )
        path.write_bytes(b"foreign")
    content = path.read_bytes()
    with pytest.raises(DomainError):
        MutationInspector(app).verify_artifacts(actor, request)
    assert path.read_bytes() == content
    assert (target / "source.py").read_bytes() == b"value = 2\n"


def test_native_artifact_verification_drift_does_not_claim_completed_source_freshness(editing):
    from app.source_mutation.inspection import MutationInspector

    app, actor, _, service, request, target, _ = editing
    service.apply(actor, request)
    (target / "source.py").write_bytes(b"later change\n")
    result = MutationInspector(app).verify_artifacts(actor, request)
    assert (
        result.native_artifacts_verified and not result.source_inspection.matches_completed_mutation
    )
    assert not result.execution_authorized and not result.deletion_authorized
    assert "later change" not in result.model_dump_json()


def test_native_artifact_verification_rechecks_stop_after_backup_measurement(editing, monkeypatch):
    from app.source_mutation.inspection import MutationInspector

    app, actor, _, service, request, target, _ = editing
    service.apply(actor, request)
    inspector = MutationInspector(app)
    native = inspector._read

    def stopped(directory, name, limit):
        data = native(directory, name, limit)
        if name.endswith(".before"):
            with service.repository.leases._write() as session:
                session.get(SystemStateRow, 1).emergency_stop = True
        return data

    monkeypatch.setattr(inspector, "_read", stopped)
    with pytest.raises(DomainError):
        inspector.verify_artifacts(actor, request)
    assert (target / "source.py").read_bytes() == b"value = 2\n"


def test_native_artifact_verification_same_bytes_replacement_never_claims_durable_ownership(
    editing,
):
    from app.source_mutation.driver import staging_names
    from app.source_mutation.inspection import MutationInspector

    app, actor, _, service, request, target, _ = editing
    completed = service.apply(actor, request)
    inspector = MutationInspector(app)
    first = inspector.verify_artifacts(actor, request)
    before, _ = staging_names(completed.operation_id, 0)
    path = target / before
    data = path.read_bytes()
    # Retain the old inode in this isolated fixture to prevent filesystem reuse.
    saved = target.parent / ("saved-original-" + target.name)
    path.rename(saved)
    path.write_bytes(data)
    second = inspector.verify_artifacts(actor, request)
    assert second.measurements[0].inode != first.measurements[0].inode
    assert second.verification_fingerprint != first.verification_fingerprint
    assert second.native_artifacts_verified and second.source_inspection.matches_completed_mutation
    assert not second.durable_artifact_ownership_verified and not second.deletion_authorized
    assert path.read_bytes() == saved.read_bytes() == data


def test_native_artifact_verification_identity_stat_uses_extended_windows_path(
    editing, monkeypatch
):
    from app.source_mutation import inspection

    app, actor, _, service, request, _, _ = editing
    service.apply(actor, request)
    native = inspection.os.stat
    observed = []

    def guarded(path, *args, **kwargs):
        if str(path).endswith(".before"):
            assert str(path).startswith("\\\\?\\")
            observed.append(str(path))
        return native(path, *args, **kwargs)

    monkeypatch.setattr(inspection.os, "stat", guarded)
    assert (
        inspection.MutationInspector(app).verify_artifacts(actor, request).native_artifacts_verified
    )
    assert observed
