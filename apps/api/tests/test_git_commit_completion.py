"""A final native checkpoint requires actual effects, not an optimistic cursor."""

from types import SimpleNamespace

import pytest

from app.agent_runtime.authorization import RuntimeActorContext
from app.core.errors import DomainError
from app.models.git_execution import (
    ApproveNativeCommitPlan,
    ExecuteNativeCommit,
    PreviewNativeCommit,
)
from app.models.identity import AssignPermissionRequest, CreatePermissionRequest
from app.self_build.commit_completion import FINISHED, checkpoint, complete
from app.self_build.commit_execution import NativeCommitService
from app.self_build.commit_journal import NativeCommitJournal
from app.self_build.commit_process import NativeCommitProcess
from app.self_build.commit_source_namespace import source_namespace
from app.self_build.native_validation_state import NativeValidationStateReader

pytest_plugins = ["tests.test_git_applied_source"]


@pytest.fixture
def context(applied):
    app, actor, binding, workspace_id, request, _, target = applied
    binding.bind(actor, workspace_id, request)
    native = NativeCommitService(app)
    permission = app.state.identity_service.create_definition(
        "permission",
        CreatePermissionRequest(
            stable_key="self_build.git.commit.create",
            display_name="Create exact native commit",
            resource_type="task",
            action="commit_create",
        ),
    )
    operator = RuntimeActorContext(
        actor_id=app.state.remote_control_service.access.actor_id, stable_key="persisted-operator"
    )
    for principal in (actor, operator):
        app.state.identity_service.assign_permission(
            principal.actor_id,
            AssignPermissionRequest(
                permission_id=permission.id,
                resource_type="task",
                resource_id="task-demo",
                effect="allow",
            ),
        )
    body = PreviewNativeCommit(
        preparation_command_id=request.command_id,
        inspection_approval_id=request.inspection_approval_id,
        expected_inspection_plan_hash=request.expected_inspection_plan_hash,
    )
    plan, snapshot, program, metadata = native.measured(
        actor,
        workspace_id,
        body,
        lambda plan, snapshot, program, guard, metadata: (plan, snapshot, program, metadata),
    )
    approval = native.approve(
        operator,
        ApproveNativeCommitPlan(
            **body.model_dump(), workspace_id=workspace_id, expected_plan_hash=plan.plan_hash
        ),
    )
    execution = ExecuteNativeCommit(
        **body.model_dump(),
        command_id="completion-proof",
        expected_plan_hash=plan.plan_hash,
        approval_id=approval.approval_id,
        worker_id=request.worker_id,
        lease_token=request.lease_token,
    )
    journal = NativeCommitJournal(native)
    record = journal.begin(actor, workspace_id, execution)
    policy = native.preparation.workspace.policies.get("jarvis")
    with native.repository.sessions() as session:
        row = native.repository.lookup(session, workspace_id)
        creation, nonce, _ = native.preparation.creation.intent(row)
    return SimpleNamespace(
        app=app,
        actor=actor,
        native=native,
        journal=journal,
        record=record,
        request=execution,
        policy=policy,
        snapshot=snapshot,
        program=program,
        metadata=metadata,
        creation=creation,
        nonce=nonce,
        target=target,
        tool=native.preparation.git.tool(),
    )


def transition(context, record, action, **claims):
    return context.journal.transition(
        context.actor, context.request, record, action, lambda **kwargs: None, **claims
    )


def test_forged_completed_cursor_cannot_create_final_checkpoint(context):
    c = context
    record = c.record
    for index in range(record.plan.object_count):
        record = transition(c, record, "object_intent")
        record = transition(c, record, "object_stage", device=1, inode=index + 100)
        record = transition(c, record, "object_verified")
    for action in ("objects_verified", "index_intent"):
        record = transition(c, record, action)
    record = transition(c, record, "index_stage", device=1, inode=999)
    for action in ("index_verified", "reference_intent", "reference_verified"):
        record = transition(c, record, action)
    process = NativeCommitProcess(c.tool, lambda current: None)
    before = c.journal.runtime.checkpoints_authorized(
        record.plan.preparation.plan.proposal.lineage.runtime_run_id, c.actor
    )
    with source_namespace(c.policy, c.creation, c.nonce, c.snapshot, lambda: None) as (
        _,
        source_guard,
    ):
        with pytest.raises(DomainError):
            complete(
                c.journal, c.actor, c.request, record.workspace_id, c.program, process, source_guard
            )
    assert (
        c.journal.runtime.checkpoints_authorized(
            record.plan.preparation.plan.proposal.lineage.runtime_run_id, c.actor
        )
        == before
    )
    assert c.journal.read(c.actor, record.workspace_id, record.command_id).state == "committed"


def test_lost_final_ack_reuses_real_checkpoint_and_preserves_original_evidence(
    context, monkeypatch
):
    c = context
    from app.self_build.commit_index_install import install_index
    from app.self_build.commit_object_install import install_object
    from app.self_build.commit_reference import advance_reference
    from app.self_build.commit_staging import stage_payload

    record = c.record
    reader = NativeValidationStateReader(c.native, lambda row, session: c.record.command_id)
    before_state = reader.capture(c.actor, c.record.workspace_id, c.record.command_id)
    assert before_state.head_sha == c.record.plan.preparation.plan.proposal.parent_sha
    with source_namespace(c.policy, c.creation, c.nonce, c.snapshot, lambda: None) as (
        _,
        source_guard,
    ):

        def guard(current):
            source_guard()
            assert c.journal.read(c.actor, current.workspace_id, current.command_id) == current

        process = NativeCommitProcess(c.tool, guard)
        directories = {
            node.path: (node.device, node.inode)
            for node in c.metadata.nodes
            if node.kind == "directory"
        }
        for index, item in enumerate(c.program.objects):
            record = transition(c, record, "object_intent")
            record = stage_payload(
                c.policy,
                record,
                c.program,
                lambda current, device, inode: transition(
                    c, current, "object_stage", device=device, inode=inode
                ),
                guard,
            )
            record = install_object(
                c.policy,
                record,
                c.program,
                process,
                lambda current, device, inode: transition(
                    c, current, "object_directory", device=device, inode=inode
                ),
                lambda current, device, inode: transition(
                    c, current, "object_store_stage", device=device, inode=inode
                ),
                guard,
                expected_directory=directories.get("objects/" + item.sha[:2]),
            )
            if record.object_directory_inode is not None:
                directories["objects/" + item.sha[:2]] = (
                    record.object_directory_device,
                    record.object_directory_inode,
                )
            assert process.object_available(c.policy, record, c.program, index)
            record = transition(c, record, "object_verified")
        record = transition(c, record, "objects_verified")
        record = transition(c, record, "index_intent")
        record = stage_payload(
            c.policy,
            record,
            c.program,
            lambda current, device, inode: transition(
                c, current, "index_stage", device=device, inode=inode
            ),
            guard,
        )
        install_index(c.policy, record, guard)
        record = transition(c, record, "index_verified")
        record = transition(c, record, "reference_intent")
        advance_reference(
            c.policy,
            c.journal.history(c.actor, record.workspace_id, record.command_id),
            c.program,
            process,
        )
        record = transition(c, record, "reference_verified")
        original_event = c.native.preparation.git.event

        def interrupted(session, row, actor, kind, payload):
            if kind == FINISHED:
                raise RuntimeError("lost final acknowledgement")
            return original_event(session, row, actor, kind, payload)

        monkeypatch.setattr(c.native.preparation.git, "event", interrupted)
        with pytest.raises(RuntimeError, match="lost final acknowledgement"):
            complete(
                c.journal, c.actor, c.request, record.workspace_id, c.program, process, source_guard
            )
        checkpoints = c.journal.runtime.checkpoints_authorized(
            record.plan.preparation.plan.proposal.lineage.runtime_run_id, c.actor
        )
        assert any(
            item.checkpoint_id == "checkpoint-" + record.operation_id + "-complete"
            for item in checkpoints
        )
        assert c.journal.read(c.actor, record.workspace_id, record.command_id).state == "committed"
        monkeypatch.setattr(c.native.preparation.git, "event", original_event)
        # A persisted final checkpoint is evidence only for its exact run,
        # attempt, state and source/commit identity. Never adopt a near match.
        saved_reader = c.journal.runtime.checkpoints_authorized
        identifier = "checkpoint-" + record.operation_id + "-complete"
        for replacement in (
            {"run_id": "run-foreign"},
            {"attempt_id": "attempt-foreign"},
            {"integrity_digest": "sha256:" + "0" * 64},
            {"resume_cursor": "prepared"},
            {"state_reference": "git-commit-execution:foreign"},
            {"metadata": {}},
        ):

            def substituted(run_id, actor, replacement=replacement):
                return [
                    item.model_copy(update=replacement)
                    if item.checkpoint_id == identifier
                    else item
                    for item in saved_reader(run_id, actor)
                ]

            with monkeypatch.context() as patch:
                patch.setattr(c.journal.runtime, "checkpoints_authorized", substituted)
                with pytest.raises(DomainError) as error:
                    checkpoint(c.journal, c.actor, record)
                assert error.value.code == "GIT_COMMIT_FINAL_CHECKPOINT_INVALID"
        # Even after the native checkpoint exists, a stale worker cannot append
        # the successful acknowledgement or obtain fresh execution authority.
        with pytest.raises(DomainError):
            complete(
                c.journal,
                c.actor,
                c.request.model_copy(update={"worker_id": "worker-foreign"}),
                record.workspace_id,
                c.program,
                process,
                source_guard,
            )
        assert c.journal.read(c.actor, record.workspace_id, record.command_id).state == "committed"
        finished = complete(
            c.journal, c.actor, c.request, record.workspace_id, c.program, process, source_guard
        )
        assert finished.state == "checkpointed"
        assert (
            finished.plan == c.record.plan
            and finished.original_approval_id == c.record.original_approval_id
        )
        assert (
            c.journal.history(c.actor, record.workspace_id, record.command_id).current == finished
        )
        assert (
            complete(
                c.journal, c.actor, c.request, record.workspace_id, c.program, process, source_guard
            )
            == finished
        )
        assert (
            c.journal.runtime.checkpoints_authorized(
                record.plan.preparation.plan.proposal.lineage.runtime_run_id, c.actor
            )
            == checkpoints
        )
        # Recovery after the branch/index changed must retain the original
        # preparation/source snapshot, rather than adopt a new baseline.
        recovered = c.native.recovery(c.actor, record.workspace_id, record.command_id)
        assert recovered.phase == "checkpointed"
        assert recovered.next_step == "checkpoint"
        assert recovered.original_plan_hash == c.record.plan.plan_hash
        assert recovered.position.branch == "exact_commit"
        assert recovered.position.index == "exact_tree"
        committed_state = reader.capture(c.actor, record.workspace_id, record.command_id)
        assert committed_state.head_sha == c.program.commit_sha
        assert committed_state.base_sha == before_state.base_sha
        assert committed_state.changes == before_state.changes
        assert committed_state != before_state
        assert c.journal.history(
            c.actor, record.workspace_id, record.command_id
        ).original == c.record.model_copy(update={"intent_checkpoint_id": None})
        with pytest.raises(DomainError):
            complete(
                c.journal,
                c.actor,
                c.request.model_copy(update={"expected_plan_hash": "0" * 64}),
                record.workspace_id,
                c.program,
                process,
                source_guard,
            )
