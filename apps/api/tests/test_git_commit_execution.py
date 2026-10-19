"""Exact native execution approval derives from real protected source evidence."""

import pytest
from sqlalchemy import select

from app.core.errors import DomainError
from app.db.models import AuditEventRow
from app.models.git_execution import ApproveNativeCommitPlan, PreviewNativeCommit
from app.models.identity import AssignPermissionRequest, CreatePermissionRequest
from app.self_build.commit_execution import APPROVED, NativeCommitService
from app.self_build.policy import digest

pytest_plugins = ["tests.test_git_applied_source"]


@pytest.fixture
def native_subject(applied):
    app, actor, binding, workspace_id, request, _, target = applied
    binding.bind(actor, workspace_id, request)
    native = NativeCommitService(app)
    body = PreviewNativeCommit(
        preparation_command_id=request.command_id,
        inspection_approval_id=request.inspection_approval_id,
        expected_inspection_plan_hash=request.expected_inspection_plan_hash,
    )
    return dict(
        app=app,
        actor=actor,
        native=native,
        body=body,
        workspace_id=workspace_id,
        request=request,
        target=target,
    )


@pytest.fixture
def approved_execution(native_subject):
    from types import SimpleNamespace

    from app.models.git_execution import ExecuteNativeCommit
    from app.self_build.commit_journal import NativeCommitJournal

    app, actor, native, body, workspace_id, request, target = (
        native_subject[name]
        for name in ("app", "actor", "native", "body", "workspace_id", "request", "target")
    )
    permission = app.state.identity_service.create_definition(
        "permission",
        CreatePermissionRequest(
            stable_key="self_build.git.commit.create",
            display_name="Create exact native commit",
            resource_type="task",
            action="commit_create",
        ),
    )
    from app.agent_runtime.authorization import RuntimeActorContext

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
    preview = native.preview(actor, workspace_id, body)
    approval_body = ApproveNativeCommitPlan(
        **body.model_dump(), workspace_id=workspace_id, expected_plan_hash=preview.plan_hash
    )
    approval = native.approve(operator, approval_body)
    execution_request = ExecuteNativeCommit(
        **body.model_dump(),
        command_id="test-native-intent",
        expected_plan_hash=preview.plan_hash,
        approval_id=approval.approval_id,
        worker_id=request.worker_id,
        lease_token=request.lease_token,
    )
    journal = NativeCommitJournal(native)
    return SimpleNamespace(
        **native_subject,
        operator=operator,
        preview=preview,
        approval=approval,
        approval_body=approval_body,
        execution_request=execution_request,
        journal=journal,
    )


def test_native_execution_requires_task_permission(native_subject):
    c = native_subject
    with pytest.raises(DomainError) as denied:
        c["native"].preview(c["actor"], c["workspace_id"], c["body"])
    assert denied.value.code == "GIT_COMMIT_EXECUTION_PERMISSION_DENIED"


def test_native_execution_preview_and_separate_operator_approval(approved_execution):
    c = approved_execution
    app, actor, native, body, workspace_id, request, target = (
        getattr(c, name)
        for name in ("app", "actor", "native", "body", "workspace_id", "request", "target")
    )
    operator, preview, approval, approval_body, execution_request, journal = (
        getattr(c, name)
        for name in (
            "operator",
            "preview",
            "approval",
            "approval_body",
            "execution_request",
            "journal",
        )
    )
    assert preview.operation == "git.commit.create"
    assert preview.applied_source.mutation_checkpoint_id
    assert preview.index_payload_bytes >= 32 and preview.object_count >= 2
    with pytest.raises(DomainError):
        native.approve(actor, approval_body)
    assert approval.plan == preview and approval.approved_by == operator.actor_id
    with native.repository.sessions() as session:
        events = list(
            session.scalars(select(AuditEventRow).where(AuditEventRow.event_type == APPROVED))
        )
        assert len(events) == 1
        assert digest(events[0].payload["payload"]["snapshot"]) == preview.snapshot_digest
    assert request.lease_token not in approval.model_dump_json()
    assert (target / "source.py").read_bytes() == b"value = 2\n"


def test_native_intent_checkpoint_acknowledgement_recovery(approved_execution, monkeypatch):
    from app.self_build.commit_journal import INTENT, INTENT_ACK

    c = approved_execution
    app, actor, native, body, workspace_id, request, target = (
        getattr(c, name)
        for name in ("app", "actor", "native", "body", "workspace_id", "request", "target")
    )
    operator, preview, approval, approval_body, execution_request, journal = (
        getattr(c, name)
        for name in (
            "operator",
            "preview",
            "approval",
            "approval_body",
            "execution_request",
            "journal",
        )
    )
    with pytest.raises(DomainError):
        journal.begin(
            actor,
            workspace_id,
            execution_request.model_copy(update={"worker_id": "foreign-worker"}),
        )
    original_event = native.preparation.git.event

    def interrupted_ack(session, row, actor, event_type, payload):
        if event_type == INTENT_ACK:
            raise RuntimeError(
                "interrupted after real runtime checkpoint before audit acknowledgement"
            )
        return original_event(session, row, actor, event_type, payload)

    monkeypatch.setattr(native.preparation.git, "event", interrupted_ack)
    with pytest.raises(RuntimeError):
        journal.begin(actor, workspace_id, execution_request)
    with native.repository.sessions() as session:
        original_record, _ = journal.intent(
            session, workspace_id, execution_request.command_id, actor.actor_id
        )
    checkpoint = journal.checkpoint(actor, original_record)[0]
    assert checkpoint is not None
    assert (
        journal.read(actor, workspace_id, execution_request.command_id).intent_checkpoint_id is None
    )
    monkeypatch.setattr(native.preparation.git, "event", original_event)
    record = journal.begin(actor, workspace_id, execution_request)
    assert record.intent_checkpoint_id == checkpoint.checkpoint_id
    assert (
        record.original_approval_id == approval.approval_id and record.completed_object_count == 0
    )
    assert journal.read(actor, workspace_id, execution_request.command_id) == record
    history = journal.history(actor, workspace_id, execution_request.command_id)
    assert history.current == record and history.progress == ()
    assert history.original.intent_checkpoint_id is None
    assert digest(history.source_snapshot.model_dump(mode="json")) == record.plan.snapshot_digest
    replacement = native.approve(operator, approval_body)
    renewed_request = execution_request.model_copy(update={"approval_id": replacement.approval_id})
    assert journal.begin(actor, workspace_id, renewed_request) == record
    original_checkpoints = journal.runtime.checkpoints_authorized
    with monkeypatch.context() as fault:
        fault.setattr(
            journal.runtime,
            "checkpoints_authorized",
            lambda run, principal: [
                value.model_copy(update={"integrity_digest": "sha256:" + "0" * 64})
                if value.checkpoint_id == checkpoint.checkpoint_id
                else value
                for value in original_checkpoints(run, principal)
            ],
        )
        with pytest.raises(DomainError) as corrupt:
            journal.read(actor, workspace_id, execution_request.command_id)
        assert corrupt.value.code == "GIT_COMMIT_EXECUTION_CHECKPOINT_INVALID"
    with native.repository.sessions() as session:
        for kind in (INTENT, INTENT_ACK):
            assert (
                len(
                    list(
                        session.scalars(
                            select(AuditEventRow).where(AuditEventRow.event_type == kind)
                        )
                    )
                )
                == 1
            )


def test_native_durable_object_index_and_ref_recovery(approved_execution):
    from pathlib import Path

    from app.self_build.commit_index_install import index_names, install_index
    from app.self_build.commit_source_namespace import source_namespace
    from app.tool_execution.filesystem import windows_native_path

    c = approved_execution
    app, actor, native, body, workspace_id, request, target = (
        getattr(c, name)
        for name in ("app", "actor", "native", "body", "workspace_id", "request", "target")
    )
    operator, preview, approval, approval_body, execution_request, journal = (
        getattr(c, name)
        for name in (
            "operator",
            "preview",
            "approval",
            "approval_body",
            "execution_request",
            "journal",
        )
    )
    record = journal.begin(actor, workspace_id, execution_request)
    renewed_request = execution_request
    captured_plan, snapshot, program, git_metadata = native.measured(
        actor,
        workspace_id,
        body,
        lambda plan, snapshot, program, guard, metadata: (plan, snapshot, program, metadata),
    )
    assert captured_plan == preview
    # The primitive receives a caller-authenticated record. Full durable execution
    # recovery is tested through the service once that execution path is complete.
    policy = native.preparation.workspace.policies.get("jarvis")
    tool = native.preparation.git.tool()
    with native.repository.sessions() as session:
        row = native.repository.lookup(session, workspace_id)
        creation, nonce, _ = native.preparation.creation.intent(row)
    with source_namespace(policy, creation, nonce, snapshot, lambda: None) as (_, source_check):
        record = _exercise_durable_object_stages(
            journal,
            actor,
            renewed_request,
            record,
            policy,
            program,
            tool,
            source_check,
            git_metadata,
        )
    metadata = Path(
        windows_native_path(Path(policy.primary_root) / ".git/worktrees" / workspace_id)
    )
    original = (metadata / "index").read_bytes()
    record = journal.transition(
        actor, renewed_request, record, "index_intent", lambda **kwargs: None
    )
    before, after = index_names(record)
    stage = metadata / after
    from app.self_build.commit_staging import stage_payload

    def claim_index(current, device, inode):
        assert stage.read_bytes() == b""
        return journal.transition(
            actor,
            renewed_request,
            current,
            "index_stage",
            lambda **kwargs: None,
            device=device,
            inode=inode,
        )

    record = stage_payload(policy, record, program, claim_index, lambda current: None)
    identity = stage.stat()
    assert (record.index_stage_device, record.index_stage_inode) == (
        identity.st_dev,
        identity.st_ino,
    )
    # A byte-identical foreign stage must not be adopted from its digest alone.
    with pytest.raises(DomainError):
        install_index(
            policy,
            record.model_copy(update={"index_stage_inode": identity.st_ino + 1}),
            lambda r: None,
        )
    assert (metadata / "index").read_bytes() == original
    with source_namespace(policy, creation, nonce, snapshot, lambda: None) as (_, source_check):
        with pytest.raises(OSError):
            (target / "source.py").write_bytes(b"foreign\n")
        _exercise_index_recovery(policy, record, metadata, original, stage, program, source_check)
    record = journal.transition(
        actor, renewed_request, record, "index_verified", lambda **kwargs: None
    )
    assert journal.read(actor, workspace_id, renewed_request.command_id) == record
    history = journal.history(actor, workspace_id, renewed_request.command_id)
    assert history.current == record
    assert len(history.progress) > 0
    assert any(entry.action == "object_store_stage" for entry in history.progress)
    from app.self_build.commit_metadata import measure_metadata
    from app.self_build.commit_metadata_rules import validate_metadata

    measured = measure_metadata(policy, lambda: None)
    assert validate_metadata(history, program, measured) == (
        "worktrees/" + workspace_id + "/index",
    )
    changed_nodes = tuple(
        node.model_copy(update={"sha256": "0" * 64}) if node.path == "index" else node
        for node in measured.nodes
    )
    payload = measured.model_dump(mode="json", exclude={"snapshot_digest"})
    payload["nodes"] = [node.model_dump(mode="json") for node in changed_nodes]
    from app.models.git_commit_metadata import CommitMetadataSnapshot

    foreign = CommitMetadataSnapshot(**payload, snapshot_digest=digest(payload))
    with pytest.raises(DomainError) as foreign_metadata:
        validate_metadata(history, program, foreign)
    assert foreign_metadata.value.code == "GIT_COMMIT_METADATA_IMMUTABLE_CHANGED"
    # A real ref update must leave the operator's HEAD/index and all unrelated
    # metadata untouched, and a lost acknowledgement must not replay its reflog.
    from app.self_build.commit_process import NativeCommitProcess
    from app.self_build.commit_reference import advance_reference

    original_primary_head = (Path(policy.primary_root) / ".git/HEAD").read_bytes()
    original_primary_index = (Path(policy.primary_root) / ".git/index").read_bytes()
    record = journal.transition(
        actor, renewed_request, record, "reference_intent", lambda **kwargs: None
    )
    history = journal.history(actor, workspace_id, renewed_request.command_id)
    with source_namespace(policy, creation, nonce, snapshot, lambda: None) as (_, source_check):

        def reference_guard(current):
            source_check()
            assert journal.read(actor, workspace_id, renewed_request.command_id) == current

        process = NativeCommitProcess(tool, reference_guard)
        advance_reference(policy, history, program, process)
        first_ref_state = measure_metadata(policy, lambda: None)
        advance_reference(policy, history, program, process)
        assert measure_metadata(policy, lambda: None) == first_ref_state
        assert process.reference_value(policy, record, program) == program.commit_sha
    assert (Path(policy.primary_root) / ".git/HEAD").read_bytes() == original_primary_head
    assert (Path(policy.primary_root) / ".git/index").read_bytes() == original_primary_index
    from pydantic import ValidationError

    for progress in (history.progress[:-1], tuple(reversed(history.progress))):
        with pytest.raises(ValidationError):
            type(history).model_validate(history.model_dump(mode="json") | {"progress": progress})
    assert (target / "source.py").read_bytes() == b"value = 2\n"
    (target / "unknown.py").write_bytes(b"foreign")
    with pytest.raises(DomainError):
        with source_namespace(policy, creation, nonce, snapshot, lambda: None):
            pytest.fail("unknown source entry must refuse")


def _exercise_durable_object_stages(
    journal, actor, request, record, policy, program, tool, source_check, git_metadata
):
    import subprocess
    from pathlib import Path

    from app.self_build.commit_object_install import (
        compressed_object,
        install_object,
        object_stage_name,
    )
    from app.self_build.commit_process import NativeCommitProcess
    from app.self_build.commit_staging import stage_payload
    from app.tool_execution.filesystem import windows_native_path

    def guard(current):
        source_check()
        assert journal.read(actor, current.workspace_id, current.command_id) == current

    process = NativeCommitProcess(tool, guard)
    expected_dirs = {
        node.path: (node.device, node.inode)
        for node in git_metadata.nodes
        if node.kind == "directory"
    }

    with pytest.raises(DomainError) as skipped:
        journal.transition(actor, request, record, "index_intent", lambda **kwargs: source_check())
    assert skipped.value.code == "GIT_COMMIT_EXECUTION_PROGRESS_INVALID"
    assert journal.read(actor, record.workspace_id, record.command_id) == record

    for index, item in enumerate(program.objects):
        prior = record
        record = journal.transition(
            actor, request, record, "object_intent", lambda **kwargs: source_check()
        )
        with pytest.raises(DomainError) as stale:
            journal.transition(
                actor, request, prior, "object_intent", lambda **kwargs: source_check()
            )
        assert stale.value.code == "GIT_COMMIT_EXECUTION_PROGRESS_CONFLICT"
        stage = (
            Path(policy.worktree_root)
            / (".jarvis-owner-" + record.workspace_id)
            / (record.operation_id + "-" + item.sha + ".payload")
        )

        stage = Path(windows_native_path(stage))

        def claim(current, device, inode, stage=stage):
            assert stage.read_bytes() == b""
            return journal.transition(
                actor,
                request,
                current,
                "object_stage",
                lambda **kwargs: source_check(),
                device=device,
                inode=inode,
            )

        if index == 0:

            def claim_interrupted(current, device, inode):
                claim(current, device, inode)
                raise RuntimeError(
                    "interrupted after durable inode claim before first payload byte"
                )

            with pytest.raises(RuntimeError):
                stage_payload(policy, record, program, claim_interrupted, guard)
            assert stage.read_bytes() == b""
            record = journal.read(actor, record.workspace_id, record.command_id)
            with pytest.raises(DomainError) as changed_inode:
                stage_payload(
                    policy,
                    record.model_copy(update={"staging_inode": record.staging_inode + 1}),
                    program,
                    claim,
                    lambda current: None,
                )
            assert changed_inode.value.code == "GIT_COMMIT_STAGE_OWNERSHIP_UNCERTAIN"
            stage.write_bytes(b"bad")
            with pytest.raises(DomainError) as bad_prefix:
                stage_payload(policy, record, program, claim, guard)
            assert bad_prefix.value.code == "GIT_COMMIT_STAGE_PREFIX_INVALID"
            assert stage.read_bytes() == b"bad"
            stage.write_bytes(item.payload[:3])
        record = stage_payload(policy, record, program, claim, guard)
        assert stage.read_bytes() == item.payload
        assert stage_payload(policy, record, program, claim, guard) == record
        assert not process.object_available(policy, record, program, index)

        def claim_directory(current, device, inode):
            return journal.transition(
                actor,
                request,
                current,
                "object_directory",
                lambda **kwargs: source_check(),
                device=device,
                inode=inode,
            )

        def claim_store(current, device, inode):
            return journal.transition(
                actor,
                request,
                current,
                "object_store_stage",
                lambda **kwargs: source_check(),
                device=device,
                inode=inode,
            )

        expected_directory = expected_dirs.get("objects/" + item.sha[:2])
        if index == 0:

            def interrupted_store(current, device, inode):
                claim_store(current, device, inode)
                raise RuntimeError("interrupted after compressed stage inode claim")

            with pytest.raises(RuntimeError):
                install_object(
                    policy,
                    record,
                    program,
                    process,
                    claim_directory,
                    interrupted_store,
                    guard,
                    expected_directory=expected_directory,
                )
            record = journal.read(actor, record.workspace_id, record.command_id)
            object_stage = Path(
                windows_native_path(
                    Path(policy.primary_root)
                    / ".git/objects"
                    / item.sha[:2]
                    / object_stage_name(record, item)
                )
            )
            assert object_stage.read_bytes() == b""
            object_stage.write_bytes(compressed_object(item)[:3])
        record = install_object(
            policy,
            record,
            program,
            process,
            claim_directory,
            claim_store,
            guard,
            expected_directory=expected_directory,
        )
        assert process.object_available(policy, record, program, index)
        assert (
            install_object(
                policy,
                record,
                program,
                process,
                claim_directory,
                claim_store,
                guard,
                expected_directory=expected_directory,
            )
            == record
        )
        if record.object_directory_inode is not None:
            expected_dirs["objects/" + item.sha[:2]] = (
                record.object_directory_device,
                record.object_directory_inode,
            )
        assert (
            subprocess.run(
                [str(tool.executable), "cat-file", item.kind, item.sha],
                cwd=policy.primary_root,
                capture_output=True,
                check=True,
            ).stdout
            == item.payload
        )
        record = journal.transition(
            actor, request, record, "object_verified", lambda **kwargs: source_check()
        )
    record = journal.transition(
        actor, request, record, "objects_verified", lambda **kwargs: source_check()
    )
    return record


def _exercise_index_recovery(policy, record, metadata, original, stage, program, source_check):
    from app.self_build.commit_index_install import index_names, install_index

    before, _ = index_names(record)
    calls = []
    fired = []
    checks = []

    def foreign_name(current):
        source_check()
        checks.append(True)
        if len(checks) > 1 and not fired:
            fired.append(True)
            foreign = metadata / "foreign.lock"
            foreign.write_bytes(b"foreign")
            foreign.unlink()

    with pytest.raises(DomainError) as foreign:
        install_index(policy, record, foreign_name)
    assert foreign.value.code == "GIT_COMMIT_NAMESPACE_CHANGED"
    assert (metadata / "index").read_bytes() == original and stage.exists()

    checks.clear()

    def immutable_registration(current):
        source_check()
        checks.append(True)
        if len(checks) < 3:
            return
        with pytest.raises(OSError):
            (metadata / "HEAD").write_bytes(b"foreign")
        raise RuntimeError("stop before installing index")

    with pytest.raises(RuntimeError):
        install_index(policy, record, immutable_registration)
    assert (metadata / "index").read_bytes() == original

    def interrupted(current):
        source_check()
        calls.append(current.operation_id)
        if not (metadata / "index").exists():
            raise RuntimeError("simulated interruption after original rename")

    with pytest.raises(RuntimeError):
        install_index(policy, record, interrupted)
    assert not (metadata / "index").exists()
    assert (metadata / before).read_bytes() == original and stage.exists()

    def authority(current):
        source_check()
        calls.append(current.operation_id)

    install_index(policy, record, authority)
    assert (metadata / "index").read_bytes() == program.index_payload
    assert (metadata / before).read_bytes() == original and not stage.exists()
    install_index(policy, record, authority)
    assert calls and (metadata / before).read_bytes() == original
