"""Append-only native intent and pre-effect checkpoint; no Git effects or route.

The original operator approval and complete source snapshot remain immutable.
Retries authenticate a current approval separately and recover the actual native
checkpoint before acknowledging it. This journal does not grant effect authority.
"""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.agent_runtime.authorization import RuntimeActorContext
from app.agent_runtime.repository import RuntimeExecutionFence
from app.db.models import AuditEventRow
from app.models.agent_runtime import RecordCheckpointCommand
from app.models.git_changes import GitChangeObservation
from app.models.git_commit_metadata import CommitMetadataSnapshot
from app.models.git_execution import (
    NativeCommitPlan,
    NativeCommitProgress,
    NativeCommitRecord,
    NativeCommitRecovery,
)
from app.self_build.commit_progress import advance, apply_progress
from app.self_build.policy import digest

INTENT = "self_build.commit_execution.prepared"
INTENT_ACK = "self_build.commit_execution.intent_checkpoint_acknowledged"
PROGRESS = "self_build.commit_execution.progress"


class NativeCommitJournal:
    def __init__(self, service):
        self.service = service
        self.preparation = service.preparation
        self.repository = service.repository
        self.runtime = self.preparation.runtime

    def fail(self, code, status=409):
        from app.self_build.commit_execution import refuse

        refuse(code, status)

    def approval(self, actor, row, plan, request, session):
        from app.self_build.commit_execution import APPROVED, EXECUTION_POLICY_DIGEST

        if (
            request.expected_plan_hash != plan.plan_hash
            or request.preparation_command_id != plan.preparation.command_id
            or plan.execution_policy_digest != EXECUTION_POLICY_DIGEST
        ):
            self.fail("GIT_COMMIT_EXECUTION_PLAN_CHANGED")
        event = session.get(
            AuditEventRow,
            request.approval_id,
        )
        try:
            payload = event.payload["payload"]
            expiry = datetime.fromisoformat(payload["expiresAt"])
            snapshot = GitChangeObservation.model_validate(payload["snapshot"])
            metadata = CommitMetadataSnapshot.model_validate(payload["gitMetadata"])
            valid = (
                event.event_type == APPROVED
                and event.task_id == row.task_id
                and event.actor not in {actor.actor_id, row.actor_id}
                and NativeCommitPlan.model_validate(payload["plan"]) == plan
                and digest(snapshot.model_dump(mode="json")) == plan.snapshot_digest
                and metadata.snapshot_digest == plan.git_metadata_snapshot_digest
                and expiry.tzinfo is not None
                and expiry > datetime.now(UTC)
            )
            if valid and ("recovery" in payload or "originalApprovalId" in payload):
                recovery = NativeCommitRecovery.model_validate(payload["recovery"])
                original = self.intent(session, row.id, request.command_id, row.actor_id)
                valid = original is not None and (
                    recovery.workspace_id == row.id
                    and recovery.command_id == request.command_id
                    and recovery.operation_id == original[0].operation_id
                    and recovery.original_plan_hash == plan.plan_hash
                    and original[0].plan == plan
                    and payload["originalApprovalId"] == original[0].original_approval_id
                    and request.approval_id != original[0].original_approval_id
                )
                if valid:
                    # Validate all persisted progress first, then find the measured
                    # prefix. Later progress must not invalidate a valid renewal.
                    current, _ = self.project(actor, row, original[0], session)
                    projected = original[0].model_copy(
                        update={"intent_checkpoint_id": current.intent_checkpoint_id}
                    )
                    valid = self.recovery_position_matches(recovery, projected)
                    events = session.scalars(
                        select(AuditEventRow)
                        .where(
                            AuditEventRow.event_type == PROGRESS,
                            AuditEventRow.payload["payload"]["operationId"].as_string()
                            == recovery.operation_id,
                        )
                        .order_by(
                            AuditEventRow.payload["payload"]["progress"]["ordinal"].as_integer()
                        )
                    )
                    for entry in events:
                        projected = apply_progress(
                            projected,
                            NativeCommitProgress.model_validate(
                                entry.payload["payload"]["progress"]
                            ),
                        )
                        valid = valid or self.recovery_position_matches(recovery, projected)
        except (AttributeError, KeyError, ValueError, TypeError):
            valid = False
        if not valid:
            self.fail("GIT_COMMIT_EXECUTION_APPROVAL_REQUIRED", 403)
        operator = RuntimeActorContext(actor_id=event.actor, stable_key="persisted-operator")
        self.preparation.operator(operator, row, session)
        self.service.permission(operator, row, session)
        return snapshot, metadata

    @staticmethod
    def recovery_position_matches(recovery, record):
        return (
            record.intent_checkpoint_id is not None
            and recovery.phase == record.state
            and recovery.record_digest == digest(record.model_dump(mode="json"))
        )

    def intent(self, session, workspace_id, command_id, actor_id):
        matches = list(
            session.scalars(
                select(AuditEventRow)
                .where(
                    AuditEventRow.event_type == INTENT,
                    AuditEventRow.actor == actor_id,
                    AuditEventRow.payload["payload"]["record"]["workspace_id"].as_string()
                    == workspace_id,
                    AuditEventRow.payload["payload"]["record"]["command_id"].as_string()
                    == command_id,
                )
                .limit(2)
            )
        )
        if not matches:
            return None
        if len(matches) != 1:
            self.fail("GIT_COMMIT_EXECUTION_JOURNAL_INVALID")
        event = matches[0]
        try:
            record = NativeCommitRecord.model_validate(event.payload["payload"]["record"])
            snapshot = GitChangeObservation.model_validate(event.payload["payload"]["snapshot"])
            metadata = CommitMetadataSnapshot.model_validate(
                event.payload["payload"]["gitMetadata"]
            )
            row = self.repository.lookup(session, workspace_id)
            from app.self_build.commit_execution import APPROVED

            original = session.get(AuditEventRow, record.original_approval_id)
            lineage = record.plan.preparation.plan.proposal.lineage
            if (
                record.workspace_id != workspace_id
                or record.command_id != command_id
                or record.state != "prepared"
                or record.intent_checkpoint_id is not None
                or record.completed_object_count != 0
                or record.pending_object_index is not None
                or record.index_stage_inode is not None
                or record.checkpoint_id is not None
                or lineage.actor_id != actor_id
                or lineage.runtime_run_id != row.runtime_run_id
                or event.task_id != row.task_id
                or digest(snapshot.model_dump(mode="json")) != record.plan.snapshot_digest
                or metadata.snapshot_digest != record.plan.git_metadata_snapshot_digest
                or original is None
                or original.event_type != APPROVED
                or original.task_id != row.task_id
                or original.actor == row.actor_id
                or NativeCommitPlan.model_validate(original.payload["payload"]["plan"])
                != record.plan
                or GitChangeObservation.model_validate(original.payload["payload"]["snapshot"])
                != snapshot
                or CommitMetadataSnapshot.model_validate(original.payload["payload"]["gitMetadata"])
                != metadata
            ):
                self.fail("GIT_COMMIT_EXECUTION_JOURNAL_INVALID")
            return record, snapshot
        except (KeyError, TypeError, ValueError):
            self.fail("GIT_COMMIT_EXECUTION_JOURNAL_INVALID")

    def checkpoint(self, actor, record):
        identifier = "checkpoint-" + record.operation_id + "-intent"
        lineage = record.plan.preparation.plan.proposal.lineage
        integrity = "sha256:" + digest(record.model_dump(mode="json"))
        metadata = dict(
            workspaceId=record.workspace_id,
            operationId=record.operation_id,
            planHash=record.plan.plan_hash,
            originalApprovalId=record.original_approval_id,
            snapshotDigest=record.plan.snapshot_digest,
        )
        matches = [
            item
            for item in self.runtime.checkpoints_authorized(lineage.runtime_run_id, actor)
            if item.checkpoint_id == identifier
        ]
        if len(matches) > 1:
            self.fail("GIT_COMMIT_EXECUTION_CHECKPOINT_INVALID")
        if not matches:
            return None, identifier, integrity, metadata
        checkpoint = matches[0]
        if (
            checkpoint.attempt_id != lineage.attempt_id
            or checkpoint.integrity_digest != integrity
            or checkpoint.state_reference != "git-commit-execution:" + record.operation_id
            or checkpoint.resume_cursor != "prepared"
            or checkpoint.metadata != metadata
        ):
            self.fail("GIT_COMMIT_EXECUTION_CHECKPOINT_INVALID")
        return checkpoint, identifier, integrity, metadata

    def live(self, actor, record, request, session):
        row, attempt, *_ = self.preparation.live(
            actor, record.workspace_id, session, request=request
        )
        self.service.permission(actor, row, session)
        self.approval(actor, row, record.plan, request, session)
        self.preparation.inspection_approval(
            actor, row, record.plan.preparation.plan.inspection_plan, request, session
        )
        current = self.intent(session, record.workspace_id, record.command_id, row.actor_id)
        if (
            current is None
            or current[0] != record
            or attempt != record.plan.preparation.plan.proposal.lineage.attempt_id
        ):
            self.fail("GIT_COMMIT_EXECUTION_JOURNAL_INVALID")
        return row

    def acknowledge(self, actor, request, record, fs_guard):
        existing, identifier, integrity, metadata = self.checkpoint(actor, record)
        lineage = record.plan.preparation.plan.proposal.lineage
        if existing is None:
            run = self.runtime.read_run_authorized(lineage.runtime_run_id, actor)

            def commit_guard(session):
                self.live(actor, record, request, session)
                fs_guard(check_authority=False)

            self.runtime.handle_authorized(
                RecordCheckpointCommand(
                    run_id=lineage.runtime_run_id,
                    command_id="ack-" + identifier,
                    expected_run_version=run.version,
                    timestamp=datetime.now(UTC),
                    checkpoint_id=identifier,
                    attempt_id=lineage.attempt_id,
                    state_reference="git-commit-execution:" + record.operation_id,
                    integrity_digest=integrity,
                    resume_cursor="prepared",
                    checkpoint_metadata=metadata,
                ),
                actor,
                execution_fence=RuntimeExecutionFence(
                    task_id=self.preparation.task_id(record.workspace_id),
                    worker_id=request.worker_id,
                    lease_token=request.lease_token,
                ),
                commit_guard=commit_guard,
            )
        if self.checkpoint(actor, record)[0] is None:
            self.fail("GIT_COMMIT_EXECUTION_CHECKPOINT_INVALID")
        with self.repository.leases._write() as session:
            row = self.live(actor, record, request, session)
            acks = list(
                session.scalars(
                    select(AuditEventRow)
                    .where(
                        AuditEventRow.event_type == INTENT_ACK,
                        AuditEventRow.payload["payload"]["operationId"].as_string()
                        == record.operation_id,
                    )
                    .limit(2)
                )
            )
            if not acks:
                self.preparation.git.event(
                    session,
                    row,
                    actor,
                    INTENT_ACK,
                    dict(
                        operationId=record.operation_id,
                        checkpointId=identifier,
                        planHash=record.plan.plan_hash,
                        authorizationApprovalId=request.approval_id,
                    ),
                )
            elif (
                len(acks) != 1
                or acks[0].task_id != row.task_id
                or acks[0].actor != row.actor_id
                or acks[0].payload["payload"].get("checkpointId") != identifier
                or acks[0].payload["payload"].get("planHash") != record.plan.plan_hash
            ):
                self.fail("GIT_COMMIT_EXECUTION_JOURNAL_INVALID")
            fs_guard(check_authority=False)
        self.repository.leases.repository.refresh_event_cursor()
        return record.model_copy(update={"intent_checkpoint_id": identifier})

    def begin(self, actor, workspace_id, request):
        def persist(plan, snapshot, program, fs_guard, git_metadata):
            with self.repository.leases._write() as session:
                row, *_ = self.preparation.live(actor, workspace_id, session, request=request)
                self.service.permission(actor, row, session)
                approved_snapshot, approved_metadata = self.approval(
                    actor, row, plan, request, session
                )
                if approved_snapshot != snapshot or approved_metadata != git_metadata:
                    self.fail("GIT_COMMIT_EXECUTION_SNAPSHOT_CHANGED")
                current = self.intent(session, workspace_id, request.command_id, actor.actor_id)
                if current is not None:
                    record, original_snapshot = current
                    if record.plan != plan or original_snapshot != snapshot:
                        self.fail("GIT_COMMIT_EXECUTION_COMMAND_CONFLICT")
                else:
                    count = list(
                        session.scalars(
                            select(AuditEventRow.id)
                            .where(
                                AuditEventRow.event_type == INTENT,
                                AuditEventRow.payload["payload"]["record"][
                                    "workspace_id"
                                ].as_string()
                                == workspace_id,
                            )
                            .limit(32)
                        )
                    )
                    if len(count) >= 32:
                        self.fail("GIT_COMMIT_EXECUTION_LIMIT")
                    record = NativeCommitRecord(
                        operation_id="commit-exec-" + uuid4().hex,
                        command_id=request.command_id,
                        workspace_id=workspace_id,
                        plan=plan,
                        original_approval_id=request.approval_id,
                        state="prepared",
                        checkpoint_id=None,
                        created_at=datetime.now(UTC),
                    )
                    self.preparation.git.event(
                        session,
                        row,
                        actor,
                        INTENT,
                        dict(
                            record=record.model_dump(mode="json"),
                            snapshot=snapshot.model_dump(mode="json"),
                            gitMetadata=git_metadata.model_dump(mode="json"),
                        ),
                    )
                fs_guard(check_authority=False)
            self.repository.leases.repository.refresh_event_cursor()
            return self.acknowledge(actor, request, record, fs_guard)

        try:
            return self.service.measured(actor, workspace_id, request, persist, request=request)
        except SQLAlchemyError:
            self.fail("GIT_COMMIT_EXECUTION_STORAGE_FAILED")

    def read(self, actor, workspace_id, command_id):
        """Protected durable evidence read; approval expiry never erases history.

        An acknowledgement is accepted only with its real runtime checkpoint.
        A checkpoint without its audit acknowledgement is an observable gap and
        does not grant filesystem or Git effect authority.
        """
        try:
            with self.repository.sessions() as session:
                row = self.repository.lookup(session, workspace_id)
                self.preparation.workspace.authorize(actor, row.task_id, session)
                self.service.permission(actor, row, session)
                current = self.intent(session, workspace_id, command_id, row.actor_id)
                if current is None:
                    self.fail("GIT_COMMIT_EXECUTION_NOT_FOUND", 404)
                record, _ = current
                return self.project(actor, row, record, session)[0]
        except SQLAlchemyError:
            self.fail("GIT_COMMIT_EXECUTION_STORAGE_FAILED")

    def project(self, actor, row, record, session):
        acks = list(
            session.scalars(
                select(AuditEventRow)
                .where(
                    AuditEventRow.event_type == INTENT_ACK,
                    AuditEventRow.payload["payload"]["operationId"].as_string()
                    == record.operation_id,
                )
                .limit(2)
            )
        )
        events = list(
            session.scalars(
                select(AuditEventRow)
                .where(
                    AuditEventRow.event_type == PROGRESS,
                    AuditEventRow.payload["payload"]["operationId"].as_string()
                    == record.operation_id,
                )
                .order_by(AuditEventRow.payload["payload"]["progress"]["ordinal"].as_integer())
                .limit(49161)
            )
        )
        if not acks:
            if events:
                self.fail("GIT_COMMIT_EXECUTION_CHECKPOINT_INVALID")
            return record, 0
        checkpoint, identifier, *_ = self.checkpoint(actor, record)
        if (
            checkpoint is None
            or len(acks) != 1
            or acks[0].task_id != row.task_id
            or acks[0].actor != row.actor_id
            or acks[0].payload["payload"].get("checkpointId") != identifier
            or acks[0].payload["payload"].get("planHash") != record.plan.plan_hash
        ):
            self.fail("GIT_COMMIT_EXECUTION_CHECKPOINT_INVALID")
        projected = record.model_copy(update={"intent_checkpoint_id": identifier})
        if len(events) > 5 * record.plan.object_count + 6:
            self.fail("GIT_COMMIT_EXECUTION_PROGRESS_LIMIT")
        for ordinal, event in enumerate(events, 1):
            try:
                progress = NativeCommitProgress.model_validate(event.payload["payload"]["progress"])
                if (
                    progress.ordinal != ordinal
                    or event.task_id != row.task_id
                    or event.actor != row.actor_id
                ):
                    self.fail("GIT_COMMIT_EXECUTION_JOURNAL_INVALID")
                projected = apply_progress(projected, progress)
            except (KeyError, TypeError, ValueError):
                self.fail("GIT_COMMIT_EXECUTION_JOURNAL_INVALID")
        from app.self_build.commit_completion import project_ack

        return project_ack(self, actor, row, projected, session), len(events)

    def history(self, actor, workspace_id, command_id):
        """Resolve complete protected claims; a projected cursor loses past inodes.

        No caller-provided history is imported. Original approval, task ownership,
        real intent checkpoint and each append-only progress event are validated
        before exposing this private receipt to metadata recovery.
        """
        from app.models.git_commit_history import NativeCommitHistory

        try:
            with self.repository.sessions() as session:
                row = self.repository.lookup(session, workspace_id)
                self.preparation.workspace.authorize(actor, row.task_id, session)
                self.service.permission(actor, row, session)
                original = self.intent(session, workspace_id, command_id, row.actor_id)
                if original is None:
                    self.fail("GIT_COMMIT_EXECUTION_NOT_FOUND", 404)
                record, snapshot = original
                current, count = self.project(actor, row, record, session)
                if current.intent_checkpoint_id is None:
                    self.fail("GIT_COMMIT_EXECUTION_CHECKPOINT_INVALID")
                intent_events = list(
                    session.scalars(
                        select(AuditEventRow)
                        .where(
                            AuditEventRow.event_type == INTENT,
                            AuditEventRow.payload["payload"]["record"]["operation_id"].as_string()
                            == record.operation_id,
                        )
                        .limit(2)
                    )
                )
                if len(intent_events) != 1:
                    self.fail("GIT_COMMIT_EXECUTION_JOURNAL_INVALID")
                events = list(
                    session.scalars(
                        select(AuditEventRow)
                        .where(
                            AuditEventRow.event_type == PROGRESS,
                            AuditEventRow.payload["payload"]["operationId"].as_string()
                            == record.operation_id,
                        )
                        .order_by(
                            AuditEventRow.payload["payload"]["progress"]["ordinal"].as_integer()
                        )
                        .limit(49161)
                    )
                )
                if len(events) != count:
                    self.fail("GIT_COMMIT_EXECUTION_JOURNAL_INVALID")
                try:
                    result = NativeCommitHistory(
                        original=record,
                        current=current,
                        source_snapshot=snapshot,
                        metadata_snapshot=CommitMetadataSnapshot.model_validate(
                            intent_events[0].payload["payload"]["gitMetadata"]
                        ),
                        progress=tuple(
                            NativeCommitProgress.model_validate(
                                event.payload["payload"]["progress"]
                            )
                            for event in events
                        ),
                    )
                    if any(
                        event.task_id != row.task_id or event.actor != row.actor_id
                        for event in events
                    ):
                        raise ValueError
                except (KeyError, TypeError, ValueError):
                    self.fail("GIT_COMMIT_EXECUTION_JOURNAL_INVALID")
                return result
        except SQLAlchemyError:
            self.fail("GIT_COMMIT_EXECUTION_STORAGE_FAILED")

    def transition(self, actor, request, expected, action, fs_guard, *, device=None, inode=None):
        """Caller retains native pins and verifies the effect/claim before this CAS.

        No model-authored replacement record, deletion or rewrite is accepted.
        Repeated/stale advancement refuses; recovery reads the actual projection.
        """
        try:
            with self.repository.leases._write() as session:
                row, attempt, *_ = self.preparation.live(
                    actor, expected.workspace_id, session, request=request
                )
                self.service.permission(actor, row, session)
                self.approval(actor, row, expected.plan, request, session)
                self.preparation.inspection_approval(
                    actor, row, expected.plan.preparation.plan.inspection_plan, request, session
                )
                original = self.intent(
                    session, expected.workspace_id, expected.command_id, row.actor_id
                )
                if original is None:
                    self.fail("GIT_COMMIT_EXECUTION_NOT_FOUND", 404)
                current, ordinal = self.project(actor, row, original[0], session)
                if (
                    current != expected
                    or request.command_id != current.command_id
                    or attempt != current.plan.preparation.plan.proposal.lineage.attempt_id
                ):
                    self.fail("GIT_COMMIT_EXECUTION_PROGRESS_CONFLICT")
                try:
                    following = advance(current, action, device=device, inode=inode)
                    progress = NativeCommitProgress(
                        operation_id=current.operation_id,
                        ordinal=ordinal + 1,
                        action=action,
                        device=device,
                        inode=inode,
                        previous_digest=digest(current.model_dump(mode="json")),
                        next_digest=digest(following.model_dump(mode="json")),
                    )
                except (TypeError, ValueError):
                    self.fail("GIT_COMMIT_EXECUTION_PROGRESS_INVALID")
                fs_guard(check_authority=False)
                self.preparation.git.event(
                    session,
                    row,
                    actor,
                    PROGRESS,
                    dict(
                        operationId=current.operation_id,
                        authorizationApprovalId=request.approval_id,
                        progress=progress.model_dump(mode="json"),
                    ),
                )
                fs_guard(check_authority=False)
            self.repository.leases.repository.refresh_event_cursor()
            return following
        except SQLAlchemyError:
            self.fail("GIT_COMMIT_EXECUTION_STORAGE_FAILED")
