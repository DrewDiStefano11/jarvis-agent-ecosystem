"""Native source reads and durable model proposals, never fixture coding-quality proof."""

import os
from hashlib import sha256

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.core.errors import DomainError
from app.db.models import AuditEventRow, OutboxEventRow, SystemStateRow
from app.development_agent.context import RepositoryContextReader
from app.development_agent.proposals import DevelopmentProposalService, compile_proposal
from app.models.development_agent import ContextSelection, DevelopmentProposal
from app.models.identity import AssignPermissionRequest
from app.models.source_change import PlanReferences
from app.source_edit_planning.service import SourceEditingPlanService

pytest_plugins = ["tests.test_workspace_creation_service"]


@pytest.fixture
def development(creation):
    app, actor, operator, service, workspace_id, authority, permission = creation
    service.create(actor, workspace_id, authority)
    policy = service.workspace.policies.get("jarvis")
    from pathlib import Path

    target = Path(policy.worktree_root) / workspace_id
    reader = RepositoryContextReader(app)
    selection = ContextSelection(paths=("source.py", "new.py"))
    context = reader.inspect(actor, workspace_id, authority, selection)
    return app, actor, operator, authority, permission, target, reader, context


def proposal(context, **changes):
    payload = dict(
        context_hash=context.context_hash,
        summary="Add a reusable value accessor",
        completion_criteria=("The accessor returns the configured value.",),
        files=(
            dict(
                operation="modify",
                path="source.py",
                before_digest=sha256(b"value = 1\n").hexdigest(),
                text="value = 2\n",
            ),
            dict(
                operation="add",
                path="new.py",
                before_digest=None,
                text="from source import value\n\ndef current_value():\n    return value\n",
            ),
        ),
    )
    payload.update(changes)
    return DevelopmentProposal(**payload)


def test_invalid_replay_input_does_not_echo_source_in_warnings_or_errors():
    import traceback
    import warnings

    from app.models.workspace_creation import CreateWorkspaceRequest
    from tests.test_development_navigation import context

    native = context()
    secret = "private-source-invalid-proposal"
    invalid = proposal(native).model_copy(update={"schema_version": secret})
    authority = CreateWorkspaceRequest(
        inspection_id="inspection-1",
        expected_plan_hash="a" * 64,
        approval_id="approval-1",
        worker_id="worker-1",
        lease_token="private-fixture-lease",
    )
    service = object.__new__(DevelopmentProposalService)
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        with pytest.raises(DomainError) as error:
            service.persist(
                object(), native.workspace_id, authority, native.selection, "invalid", invalid
            )
    assert error.value.code == "DEVELOPMENT_PROPOSAL_INVALID"
    assert secret not in "".join(traceback.format_exception(error.value))
    assert secret not in "".join(str(warning.message) for warning in captured)


def references(app, workspace_id):
    service = app.state.self_build_workspace_service
    with service.repository.sessions() as session:
        row = service.repository.lookup(session, workspace_id)
        reservation = service.repository.contract(row)
        return PlanReferences(
            original_plan_hash=reservation.plan.plan_hash,
            authorization_reference=row.approval_id,
        )


def test_native_context_has_exact_source_complete_topology_and_lineage(development):
    _, actor, _, authority, _, target, reader, context = development
    assert context.task_id == "task-demo" and context.runtime_run_id == "run-1"
    entries = {item.path: item for item in context.observation.entries}
    assert entries["source.py"].content.text == "value = 1\n"
    assert entries["new.py"].kind == "absent"
    assert context.code_state.changes == ()
    assert reader.inspect(actor, context.workspace_id, authority, context.selection) == context
    assert authority.lease_token not in context.model_dump_json()
    assert str(target) not in context.model_dump_json()
    assert "value = 1" not in repr(context)


def test_multi_file_proposal_is_persisted_under_pins_without_source_changes(development):
    app, actor, _, authority, _, target, _, context = development
    service = DevelopmentProposalService(app)
    args = (
        actor,
        context.workspace_id,
        authority,
        context.selection,
        "develop-1",
        proposal(context),
    )
    result = service.persist(*args)
    assert service.persist(*args) == result
    assert SourceEditingPlanService(app).read(actor, result.record_id) == result
    from app.main import create_app

    restarted = create_app(database_url=app.state.settings.database_url)
    try:
        assert (
            restarted.state.development_proposal_service.plans.read(actor, result.record_id)
            == result
        )
    finally:
        restarted.state.engine.dispose()
    assert result.plan.runtime_attempt_id == context.runtime_attempt_id
    assert [item.path for item in result.plan.source_plan.changes] == ["new.py", "source.py"]
    assert {item.path for item in result.plan.expected_code_state.changes} == {
        "new.py",
        "source.py",
    }
    assert result.plan.evidence_status == "unmeasured" and not result.plan.mutation_supported
    assert (target / "source.py").read_bytes() == b"value = 1\n" and not (
        target / "new.py"
    ).exists()
    with app.state.repository.session_factory() as session:
        audit = list(
            session.scalars(
                select(AuditEventRow).where(
                    AuditEventRow.event_type == "self_build.source_edit_plan.persisted"
                )
            )
        )
        outbox = list(
            session.scalars(
                select(OutboxEventRow).where(
                    OutboxEventRow.event_type == "self_build.source_edit_plan.persisted"
                )
            )
        )
        assert len(audit) == len(outbox) == 1
        assert "value = 2" not in str(audit[0].payload)


def test_stale_source_refuses_before_plan_persistence(development):
    app, actor, _, authority, _, target, reader, context = development
    (target / "source.py").write_bytes(b"value = 3\n")
    with pytest.raises(DomainError) as error:
        DevelopmentProposalService(app).persist(
            actor, context.workspace_id, authority, context.selection, "stale", proposal(context)
        )
    assert error.value.code == "DEVELOPMENT_CONTEXT_STALE"
    fresh = reader.inspect(actor, context.workspace_id, authority, context.selection)
    assert fresh.context_hash != context.context_hash
    assert fresh.code_state.changes[0].after_hash == sha256(b"value = 3\n").hexdigest()


def test_durable_replay_ignores_later_source_but_binds_exact_input(development, monkeypatch):
    app, actor, _, authority, _, target, _, context = development
    service = DevelopmentProposalService(app)
    args = (actor, context.workspace_id, authority, context.selection, "durable-replay")
    original = proposal(context)
    record = service.persist(*args, original)
    (target / "source.py").write_bytes(b"value = 3\n")

    def unexpected_observation(*args, **kwargs):
        raise AssertionError("durable command replay must not remeasure source")

    monkeypatch.setattr(service.reader, "inspect", unexpected_observation)
    assert service.persist(*args, original) == record
    for changed in (
        proposal(context, summary="A different summary"),
        proposal(context, completion_criteria=("A different criterion",)),
        proposal(context, context_hash="f" * 64),
        proposal(context, files=original.files[:1]),
    ):
        with pytest.raises(DomainError) as error:
            service.persist(*args, changed)
        assert error.value.code == "SOURCE_EDIT_COMMAND_CONFLICT"
    with pytest.raises(DomainError, match="integrity"):
        service.persist(*args, original, allow_delete=True)
    with pytest.raises(DomainError) as error:
        service.persist(
            actor,
            context.workspace_id,
            authority,
            ContextSelection(paths=("source.py",)),
            "durable-replay",
            original,
        )
    assert error.value.code == "SOURCE_EDIT_COMMAND_CONFLICT"
    assert (target / "source.py").read_bytes() == b"value = 3\n"
    with app.state.repository.session_factory() as session:
        audit = list(
            session.scalars(
                select(AuditEventRow).where(
                    AuditEventRow.event_type == "self_build.source_edit_plan.persisted"
                )
            )
        )
        outbox = list(
            session.scalars(
                select(OutboxEventRow).where(
                    OutboxEventRow.event_type == "self_build.source_edit_plan.persisted"
                )
            )
        )
    assert len(audit) == len(outbox) == 1
    assert len(audit[0].payload["payload"]["originRequestHash"]) == 64
    assert (
        outbox[0].envelope["payload"]["originRequestHash"]
        == audit[0].payload["payload"]["originRequestHash"]
    )


def test_durable_replay_still_requires_live_authority(development):
    app, actor, _, authority, permission, _, _, context = development
    service = DevelopmentProposalService(app)
    args = (
        actor,
        context.workspace_id,
        authority,
        context.selection,
        "live-replay",
        proposal(context),
    )
    service.persist(*args)
    with app.state.repository.session_factory.begin() as session:
        session.get(SystemStateRow, 1).emergency_stop = True
    with pytest.raises(DomainError) as error:
        service.persist(*args)
    assert error.value.code == "EMERGENCY_STOP_ACTIVE"
    with app.state.repository.session_factory.begin() as session:
        session.get(SystemStateRow, 1).emergency_stop = False
    app.state.identity_service.assign_permission(
        actor.actor_id, AssignPermissionRequest(effect="deny", **permission)
    )
    with pytest.raises(DomainError) as error:
        service.persist(*args)
    assert error.value.code == "SELF_BUILD_CREATION_PERMISSION_DENIED"


def test_stop_and_revoked_operator_block_context(development):
    app, actor, operator, authority, permission, _, reader, context = development
    with app.state.repository.session_factory() as session:
        session.get(SystemStateRow, 1).emergency_stop = True
        session.commit()
    with pytest.raises(DomainError) as stopped:
        reader.inspect(actor, context.workspace_id, authority, context.selection)
    assert stopped.value.code == "EMERGENCY_STOP_ACTIVE"
    with app.state.repository.session_factory() as session:
        session.get(SystemStateRow, 1).emergency_stop = False
        session.commit()
    app.state.identity_service.assign_permission(
        operator.actor_id, AssignPermissionRequest(effect="deny", **permission)
    )
    with pytest.raises(DomainError) as denied:
        reader.inspect(actor, context.workspace_id, authority, context.selection)
    assert denied.value.code == "SELF_BUILD_CREATION_PERMISSION_DENIED"


def test_final_authority_recheck_prevents_late_context_return(development, monkeypatch):
    app, actor, _, authority, _, _, reader, context = development
    native = reader.creation.current
    count = [0]

    def current(*args, **kwargs):
        count[0] += 1
        result = native(*args, **kwargs)
        if count[0] == 3:
            with app.state.repository.session_factory() as session:
                session.get(SystemStateRow, 1).emergency_stop = True
                session.commit()
        return result

    monkeypatch.setattr(reader.creation, "current", current)
    with pytest.raises(DomainError):
        reader.inspect(actor, context.workspace_id, authority, context.selection)


def test_no_raw_source_in_invalid_model_errors(development):
    _, _, _, _, _, _, _, context = development
    data = proposal(context).model_dump()
    data["files"][0]["text"] = "\x00SENSITIVE-SOURCE"
    with pytest.raises(ValidationError) as error:
        DevelopmentProposal.model_validate(data)
    assert "SENSITIVE-SOURCE" not in str(error.value)


@pytest.mark.parametrize(
    "change,code",
    [
        (dict(context_hash="f" * 64), "DEVELOPMENT_CONTEXT_STALE"),
        (
            dict(
                files=(
                    dict(
                        operation="modify",
                        path="foreign.py",
                        before_digest="a" * 64,
                        text="foreign",
                    ),
                )
            ),
            "DEVELOPMENT_PATH_NOT_SELECTED",
        ),
        (
            dict(
                files=(
                    dict(
                        operation="modify", path="source.py", before_digest="a" * 64, text="changed"
                    ),
                )
            ),
            "DEVELOPMENT_BEFORE_CONFLICT",
        ),
        (
            dict(
                files=(
                    dict(
                        operation="delete",
                        path="source.py",
                        before_digest=sha256(b"value = 1\n").hexdigest(),
                        text=None,
                    ),
                )
            ),
            "DEVELOPMENT_DELETE_DENIED",
        ),
    ],
)
def test_untrusted_proposals_cannot_substitute_scope_or_originals(development, change, code):
    app, _, _, _, _, _, _, context = development
    with pytest.raises(DomainError) as error:
        compile_proposal(
            proposal(context, **change), context, references(app, context.workspace_id)
        )
    assert error.value.code == code


def test_deletion_requires_trusted_opt_in(development):
    app, _, _, _, _, _, _, context = development
    deletion = proposal(
        context,
        files=(
            dict(
                operation="delete",
                path="source.py",
                before_digest=sha256(b"value = 1\n").hexdigest(),
                text=None,
            ),
        ),
    )
    intent = compile_proposal(
        deletion, context, references(app, context.workspace_id), allow_delete=True
    )
    assert intent.changes[0].after is None


def test_hardlinks_and_binary_source_refuse(development):
    app, actor, _, authority, _, target, reader, context = development
    os.link(target / "source.py", target / "alias.py")
    with pytest.raises(DomainError):
        reader.inspect(actor, context.workspace_id, authority, context.selection)
    (target / "alias.py").unlink()
    (target / "source.py").write_bytes(b"\xff")
    with pytest.raises(DomainError) as error:
        reader.inspect(actor, context.workspace_id, authority, context.selection)
    assert error.value.code == "DEVELOPMENT_SOURCE_ENCODING"


@pytest.mark.parametrize(
    "paths",
    [
        ("../source.py",),
        (".env",),
        ("source.py", "SOURCE.py"),
        (".jarvis-workspace.json",),
        ("folder/.jarvis-artifact",),
    ],
)
def test_selection_refuses_unsafe_paths(paths):
    with pytest.raises((ValueError, DomainError)):
        ContextSelection(paths=paths)


def test_context_proposal_applies_real_native_changes_with_independent_approval(development):
    from app.models.identity import CreatePermissionRequest
    from app.models.source_mutation import ApplySourceMutation, ApproveSourceMutation
    from app.source_mutation.service import SourceMutationService

    app, actor, operator, authority, _, target, reader, context = development
    (target / "docs").mkdir()
    (target / "docs/context.txt").write_bytes(b"Unchanged reference material.\n")
    selection = ContextSelection(paths=("source.py", "new.py", "docs/context.txt"))
    context = reader.inspect(actor, context.workspace_id, authority, selection)
    plans = DevelopmentProposalService(app)
    record = plans.persist(
        actor, context.workspace_id, authority, selection, "native-development", proposal(context)
    )
    observed_paths = {item.path for item in record.plan.observation.entries}
    assert "docs" in observed_paths and "docs/context.txt" not in observed_paths
    assert {".git", ".jarvis-workspace.json"} <= observed_paths
    permission = app.state.identity_service.create_definition(
        "permission",
        CreatePermissionRequest(
            stable_key="self_build.workspace.source_edit",
            display_name="Approve bounded development edits",
            resource_type="task",
            action="source_edit",
        ),
    )
    for principal in (actor, operator):
        app.state.identity_service.assign_permission(
            principal.actor_id,
            AssignPermissionRequest(
                permission_id=permission.id,
                effect="allow",
                resource_type="task",
                resource_id="task-demo",
            ),
        )
    mutations = SourceMutationService(app)
    plan = mutations.preview(actor, record.record_id)
    approval = mutations.approve(
        operator,
        ApproveSourceMutation(
            editing_record_id=record.record_id, expected_plan_hash=plan.plan_hash
        ),
    )
    request = ApplySourceMutation(
        editing_record_id=record.record_id,
        expected_plan_hash=plan.plan_hash,
        approval_id=approval.approval_id,
        worker_id=authority.worker_id,
        lease_token=authority.lease_token,
    )
    with pytest.raises(DomainError):
        mutations.apply(actor, request.model_copy(update={"approval_id": "foreign-approval"}))
    assert (target / "source.py").read_bytes() == b"value = 1\n"
    result = mutations.apply(actor, request)
    assert result.state == "complete" and result.completed_count == 2 and result.checkpoint_id
    assert (target / "source.py").read_bytes() == b"value = 2\n"
    assert (target / "new.py").read_bytes().startswith(b"from source import value")
    assert (target / "docs/context.txt").read_bytes() == b"Unchanged reference material.\n"
    assert SourceMutationService(app).apply(actor, request) == result
    assert (
        plans.persist(
            actor,
            context.workspace_id,
            authority,
            selection,
            "native-development",
            proposal(context),
        )
        == record
    )
    from app.main import create_app

    restarted = create_app(database_url=app.state.settings.database_url)
    try:
        # Restore the same trusted operator configuration, never caller/model authority.
        for name in type(app.state.settings).model_fields:
            if name.startswith("self_build_"):
                setattr(restarted.state.settings, name, getattr(app.state.settings, name))
        restarted.state.remote_control_service = app.state.remote_control_service
        assert (
            DevelopmentProposalService(restarted).persist(
                actor,
                context.workspace_id,
                authority,
                selection,
                "native-development",
                proposal(context),
            )
            == record
        )
        restored = SourceEditingPlanService(restarted).read(actor, record.record_id)
        assert restored == record and restored.plan.task_id == result.plan.task_id
        from app.source_mutation.repository import SourceMutationRow

        recovery = SourceMutationService(restarted)
        with restarted.state.repository.session_factory() as session:
            stored = session.get(SourceMutationRow, record.record_id)
            acknowledged = recovery.intent(stored, result.plan, session)
        assert acknowledged == result
        assert recovery.checkpoint(actor, acknowledged)[0].checkpoint_id == result.checkpoint_id
        assert (target / "source.py").read_bytes() == b"value = 2\n"
        assert (target / "new.py").read_bytes().startswith(b"from source import value")
    finally:
        restarted.state.engine.dispose()
