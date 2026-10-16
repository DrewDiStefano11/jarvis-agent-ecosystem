"""Protected final checkpoint and append-only acknowledgement; no Git effects."""

from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.agent_runtime.repository import RuntimeExecutionFence
from app.db.models import AuditEventRow
from app.models.agent_runtime import RecordCheckpointCommand
from app.self_build.checkout_driver import pin_read_tree
from app.self_build.commit_metadata import measure_metadata
from app.self_build.commit_metadata_rules import validate_metadata
from app.self_build.commit_position import measure_position
from app.self_build.policy import digest
from app.tool_execution.filesystem import open_directory, open_file, read_bytes

FINISHED = "self_build.commit_execution.checkpoint_acknowledged"


def complete(journal, actor, request, workspace_id, program, process, source_guard):
    """Independently prove actual effects before any successful checkpoint.

    The effect service retains original source/owner pins for this entire call.
    Metadata is fully read-only now: this boundary performs no Git mutation.
    """
    history = journal.history(actor, workspace_id, request.command_id)
    record = history.current
    if record.state == "checkpointed":
        if request.expected_plan_hash != record.plan.plan_hash:
            journal.fail("GIT_COMMIT_FINAL_RECORD_CHANGED")
        return record
    with journal.repository.sessions() as session:
        row = journal.repository.lookup(session, workspace_id)
        journal.preparation.workspace.authorize(actor, row.task_id, session)
        journal.service.permission(actor, row, session)
        policy = journal.preparation.workspace.policies.get(row.repository_id)
    record, policy, proposal = process._validated(policy, record, program)
    if record.state != "committed":
        journal.fail("GIT_COMMIT_FINAL_EFFECT_REQUIRED")
    root = Path(policy.primary_root) / ".git"
    with ExitStack() as stack:
        directory = open_directory(stack, Path(root.anchor), internal=True)
        for part in root.parts[1:]:
            directory = open_directory(stack, directory.path / part, directory, part, internal=True)
        stable = pin_read_tree(stack, root, lambda: process.guard(record))

        def guard(*, check_authority=True):
            source_guard(check_authority=check_authority)
            stable()
            if check_authority:
                process.guard(record)

        log_paths = {
            "logs/refs/heads/" + proposal.branch,
            "worktrees/" + workspace_id + "/logs/HEAD",
        }

        def logs(path):
            if path not in log_paths:
                journal.fail("GIT_COMMIT_REFLOG_SCOPE_CHANGED")
            with open_file(directory, path, 0, internal=True) as fd:
                return read_bytes(fd, 67108864)

        measured = measure_metadata(policy, lambda: guard())
        validate_metadata(
            history,
            program,
            measured,
            read_reflog=logs,
            object_available=lambda index: process.object_available(policy, record, program, index),
        )
        if measure_position(policy, record, program, process).next_step != "checkpoint":
            journal.fail("GIT_COMMIT_FINAL_EFFECT_REQUIRED")
        guard()
        return acknowledge(journal, actor, request, record, guard)


def checkpoint(journal, actor, record):
    record = record.model_copy(update={"state": "committed", "checkpoint_id": None})
    identifier = "checkpoint-" + record.operation_id + "-complete"
    proposal = record.plan.preparation.plan.proposal
    integrity = "sha256:" + digest(record.model_dump(mode="json"))
    metadata = dict(
        workspaceId=record.workspace_id,
        operationId=record.operation_id,
        planHash=record.plan.plan_hash,
        originalApprovalId=record.original_approval_id,
        sourceSnapshotDigest=record.plan.snapshot_digest,
        sourceArtifactManifestHash=record.plan.source_artifact_manifest_hash,
        commitSha=proposal.prospective_commit_sha,
        treeSha=proposal.prospective_tree_sha,
        parentSha=proposal.parent_sha,
    )
    matches = [
        item
        for item in journal.runtime.checkpoints_authorized(proposal.lineage.runtime_run_id, actor)
        if item.checkpoint_id == identifier
    ]
    if len(matches) > 1:
        journal.fail("GIT_COMMIT_FINAL_CHECKPOINT_INVALID")
    if not matches:
        return None, identifier, integrity, metadata
    actual = matches[0]
    if (
        actual.run_id != proposal.lineage.runtime_run_id
        or actual.attempt_id != proposal.lineage.attempt_id
        or actual.integrity_digest != integrity
        or actual.state_reference != "git-commit-execution:" + record.operation_id
        or actual.resume_cursor != "committed"
        or actual.metadata != metadata
    ):
        journal.fail("GIT_COMMIT_FINAL_CHECKPOINT_INVALID")
    return actual, identifier, integrity, metadata


def project_ack(journal, actor, row, record, session):
    events = list(
        session.scalars(
            select(AuditEventRow)
            .where(
                AuditEventRow.event_type == FINISHED,
                AuditEventRow.payload["payload"]["operationId"].as_string() == record.operation_id,
            )
            .limit(2)
        )
    )
    if not events:
        return record
    if record.state != "committed" or len(events) != 1:
        journal.fail("GIT_COMMIT_FINAL_ACKNOWLEDGEMENT_INVALID")
    actual, identifier, _, _ = checkpoint(journal, actor, record)
    event = events[0]
    if (
        actual is None
        or event.task_id != row.task_id
        or event.actor != row.actor_id
        or event.payload["payload"].get("checkpointId") != identifier
        or event.payload["payload"].get("recordDigest") != digest(record.model_dump(mode="json"))
        or event.payload["payload"].get("planHash") != record.plan.plan_hash
    ):
        journal.fail("GIT_COMMIT_FINAL_ACKNOWLEDGEMENT_INVALID")
    return record.model_copy(update={"state": "checkpointed", "checkpoint_id": identifier})


def live(journal, actor, request, expected, session):
    row, attempt, *_ = journal.preparation.live(
        actor, expected.workspace_id, session, request=request
    )
    journal.service.permission(actor, row, session)
    journal.approval(actor, row, expected.plan, request, session)
    journal.preparation.inspection_approval(
        actor, row, expected.plan.preparation.plan.inspection_plan, request, session
    )
    original = journal.intent(session, expected.workspace_id, expected.command_id, row.actor_id)
    if original is None:
        journal.fail("GIT_COMMIT_EXECUTION_NOT_FOUND", 404)
    current, _ = journal.project(actor, row, original[0], session)
    if (
        current != expected
        or attempt != expected.plan.preparation.plan.proposal.lineage.attempt_id
        or request.command_id != expected.command_id
    ):
        journal.fail("GIT_COMMIT_FINAL_RECORD_CHANGED")
    return row


def acknowledge(journal, actor, request, expected, fs_guard):
    """Caller retains complete source and read-only final Git metadata pins.

    This does not accept a measured-state claim from a model/request. The future
    effect service must independently verify the actual final native position
    under those pins before calling this private acknowledgement boundary.
    """
    if expected.state != "committed":
        journal.fail("GIT_COMMIT_FINAL_EFFECT_REQUIRED")
    try:
        with journal.repository.sessions() as session:
            row = journal.repository.lookup(session, expected.workspace_id)
            journal.preparation.workspace.authorize(actor, row.task_id, session)
            journal.service.permission(actor, row, session)
            original = journal.intent(
                session, expected.workspace_id, expected.command_id, row.actor_id
            )
            if original is None:
                journal.fail("GIT_COMMIT_EXECUTION_NOT_FOUND", 404)
            current, _ = journal.project(actor, row, original[0], session)
            if current.state == "checkpointed":
                if (
                    current.model_copy(update={"state": "committed", "checkpoint_id": None})
                    != expected
                    or request.command_id != expected.command_id
                    or request.expected_plan_hash != expected.plan.plan_hash
                ):
                    journal.fail("GIT_COMMIT_FINAL_RECORD_CHANGED")
                # Historical receipt replay is not fresh execution authority.
                return current
            live(journal, actor, request, expected, session)
        fs_guard()
        existing, identifier, integrity, metadata = checkpoint(journal, actor, expected)
        proposal = expected.plan.preparation.plan.proposal
        if existing is None:
            run = journal.runtime.read_run_authorized(proposal.lineage.runtime_run_id, actor)

            def commit_guard(session):
                live(journal, actor, request, expected, session)
                fs_guard(check_authority=False)

            journal.runtime.handle_authorized(
                RecordCheckpointCommand(
                    run_id=proposal.lineage.runtime_run_id,
                    command_id="ack-" + identifier,
                    expected_run_version=run.version,
                    timestamp=datetime.now(UTC),
                    checkpoint_id=identifier,
                    attempt_id=proposal.lineage.attempt_id,
                    state_reference="git-commit-execution:" + expected.operation_id,
                    integrity_digest=integrity,
                    resume_cursor="committed",
                    checkpoint_metadata=metadata,
                ),
                actor,
                execution_fence=RuntimeExecutionFence(
                    task_id=journal.preparation.task_id(expected.workspace_id),
                    worker_id=request.worker_id,
                    lease_token=request.lease_token,
                ),
                commit_guard=commit_guard,
            )
        if checkpoint(journal, actor, expected)[0] is None:
            journal.fail("GIT_COMMIT_FINAL_CHECKPOINT_INVALID")
        with journal.repository.leases._write() as session:
            row = live(journal, actor, request, expected, session)
            fs_guard(check_authority=False)
            journal.preparation.git.event(
                session,
                row,
                actor,
                FINISHED,
                dict(
                    operationId=expected.operation_id,
                    checkpointId=identifier,
                    recordDigest=digest(expected.model_dump(mode="json")),
                    planHash=expected.plan.plan_hash,
                    authorizationApprovalId=request.approval_id,
                ),
            )
            fs_guard(check_authority=False)
        journal.repository.leases.repository.refresh_event_cursor()
        return journal.read(actor, expected.workspace_id, expected.command_id)
    except SQLAlchemyError:
        journal.fail("GIT_COMMIT_EXECUTION_STORAGE_FAILED")
