"""Native owned commit preparation journal. Object/ref/index writes remain unavailable."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.agent_runtime.authorization import RuntimeActorContext
from app.agent_runtime.repository import RuntimeExecutionFence
from app.core.errors import DomainError
from app.db.models import AuditEventRow
from app.models import git_commit_authorization as contracts
from app.models.agent_runtime import RecordCheckpointCommand
from app.models.git_commit import CommitLineage
from app.models.git_commit_authorization import (
    CommitAuthorizationPlan,
    CommitInspectionApproval,
    CommitInspectionPlan,
    CommitPreparationApproval,
    CommitSelection,
    PreparedCommitRecord,
)
from app.self_build import native_artifacts as artifact_policy
from app.self_build.change_verifier import ChangeVerifier
from app.self_build.commit_planner import PREPARATION_POLICY_DIGEST, propose_commit
from app.self_build.creation_service import ownership
from app.self_build.native_artifacts import mutation_artifacts
from app.self_build.policy import digest
from app.source_edit_planning.service import EDITING_POLICY_DIGEST, SourceEditingPlanService

AUTHORIZATION_POLICY_DIGEST = sha256(
    (
        Path(__file__).read_text(encoding="utf-8")
        + Path(contracts.__file__).read_text(encoding="utf-8")
        + PREPARATION_POLICY_DIGEST
        + EDITING_POLICY_DIGEST
        + Path(artifact_policy.__file__).read_text(encoding="utf-8")
    ).encode()
).hexdigest()
PREPARED = "self_build.commit_preparation.prepared"
ACKNOWLEDGED = "self_build.commit_preparation.checkpoint_acknowledged"
APPROVED = "self_build.commit_preparation.approved"
INSPECTION_APPROVED = "self_build.commit_preparation.inspection_approved"


def fail(code, status=409):
    raise DomainError(code, "Commit preparation failed a required native authority check.", status)


class CommitPreparationService:
    def __init__(self, app):
        self.app = app
        self.git = app.state.self_build_git_service
        self.creation = app.state.self_build_creation_service
        self.workspace = self.git.workspace
        self.repository = self.git.repository
        self.runtime = app.state.agent_runtime_service
        self.source = SourceEditingPlanService(app)

    def permission(self, actor, row, session):
        decision = self.workspace.identity.check_permission_resource_access(
            actor.actor_id, "self_build.git.commit.prepare", "task", row.task_id, session=session
        )
        if not decision.allowed:
            fail("GIT_COMMIT_PREPARATION_PERMISSION_DENIED", 403)

    def live(self, actor, workspace_id, session, *, request=None):
        row = self.repository.lookup(session, workspace_id)
        self.workspace.authorize(actor, row.task_id, session, write=True)
        self.repository.contract(row)
        self.permission(actor, row, session)
        self.git.permission(actor, row.task_id, session)
        policy = self.workspace.policies.get(row.repository_id)
        run = self.repository.runtime(session, row.runtime_run_id)
        if (
            row.state != "reserved"
            or self.repository.reason(session, row, digest(policy.model_dump()))
            or run.state != "running"
            or not run.active_attempt_id
            or row.actor_id != run.agent_id
        ):
            fail("GIT_COMMIT_OWNER_INACTIVE")
        if request is not None:
            if (
                actor.actor_id != row.actor_id
                or request.worker_id != row.worker_id
                or digest(request.lease_token) != row.lease_fingerprint
            ):
                fail("GIT_COMMIT_OWNER_MISMATCH")
            self.repository.leases._require_lease(
                session, row.task_id, request.worker_id, request.lease_token, datetime.now(UTC)
            )
        creation, nonce, _ = self.creation.intent(row)
        if creation.state != "ready" or creation.checkpoint_id is None:
            fail("GIT_COMMIT_WORKSPACE_NOT_READY")
        if creation.ownership_digest != digest(ownership(creation, nonce)):
            fail("GIT_COMMIT_WORKSPACE_INVALID")
        return row, run.active_attempt_id, policy, creation, nonce

    def source_record(self, actor, workspace_id, record_id):
        source = self.source.read(actor, record_id)
        with self.repository.sessions() as session:
            row = self.repository.lookup(session, workspace_id)
            events = list(
                session.scalars(
                    select(AuditEventRow)
                    .where(
                        AuditEventRow.event_type == "self_build.source_edit_plan.persisted",
                        AuditEventRow.task_id == row.task_id,
                        AuditEventRow.payload["payload"]["recordId"].as_string() == record_id,
                    )
                    .limit(2)
                )
            )
            if (
                source.plan.workspace_id != workspace_id
                or source.actor_id != row.actor_id
                or source.plan.runtime_run_id != row.runtime_run_id
                or len(events) != 1
            ):
                fail("GIT_COMMIT_SOURCE_RECORD_INVALID")
            event = events[0]
            payload = event.payload.get("payload", {})
            if (
                event.actor != source.actor_id
                or payload.get("planHash") != source.plan.plan_hash
                or payload.get("workspaceId") != workspace_id
                or payload.get("runtimeRunId") != row.runtime_run_id
            ):
                fail("GIT_COMMIT_SOURCE_RECORD_INVALID")
        return source

    def operator(self, actor, row, session):
        self.git.operator(actor, row, session)
        self.permission(actor, row, session)
        if self.app.state.remote_control_service.access.actor_id != actor.actor_id:
            fail("GIT_COMMIT_OPERATOR_REQUIRED", 403)

    def inspection_plan(self, actor, workspace_id, body):
        source = self.source_record(actor, workspace_id, body.source_edit_record_id)
        artifacts = mutation_artifacts(self.app, actor, source)
        acknowledged = self.creation.read(actor, workspace_id)
        with self.repository.leases._write() as session:
            row, attempt, _, creation, _ = self.live(actor, workspace_id, session)
            if creation != acknowledged:
                fail("GIT_COMMIT_WORKSPACE_INVALID")
            tool = self.git.tool()
            selection = CommitSelection.model_validate(
                body.model_dump(
                    mode="json", include={"source_edit_record_id", "selection", "metadata"}
                )
            )
            payload = dict(
                schema_version="1.1",
                operation="git.commit.inspect",
                workspace_id=workspace_id,
                creation_operation_id=creation.operation_id,
                creation_plan_hash=creation.plan.plan_hash,
                creation_checkpoint_id=creation.checkpoint_id,
                source_edit_record_id=source.record_id,
                source_edit_record_plan_hash=source.plan.plan_hash,
                actor_id=row.actor_id,
                runtime_run_id=row.runtime_run_id,
                attempt_id=attempt,
                worker_id=row.worker_id,
                tool_identity_digest=digest([str(tool.executable), tool.executable_hash]),
                selection=list(selection.selection),
                metadata=selection.metadata.model_dump(mode="json"),
                authorization_policy_digest=AUTHORIZATION_POLICY_DIGEST,
                native_artifact_manifest_digest=artifacts.fingerprint,
            )
            return CommitInspectionPlan(**payload, plan_hash=digest(payload))

    def approve_inspection(self, actor, body):
        plan = self.inspection_plan(actor, body.workspace_id, body)
        with self.repository.leases._write() as session:
            row, attempt, _, creation, _ = self.live(actor, body.workspace_id, session)
            self.operator(actor, row, session)
            if (
                body.expected_plan_hash != plan.plan_hash
                or attempt != plan.attempt_id
                or creation.plan.plan_hash != plan.creation_plan_hash
            ):
                fail("GIT_COMMIT_PLAN_CHANGED")
            expiry = datetime.now(UTC) + timedelta(seconds=body.valid_for_seconds)
            event = self.git.event(
                session,
                row,
                actor,
                INSPECTION_APPROVED,
                dict(plan=plan.model_dump(mode="json"), expiresAt=expiry.isoformat()),
            )
            result = CommitInspectionApproval(
                approval_id=event.id, approved_by=actor.actor_id, expires_at=expiry, plan=plan
            )
        self.repository.leases.repository.refresh_event_cursor()
        return result

    def inspection_approval(self, actor, row, plan, body, session):
        tool = self.git.tool()
        if digest([str(tool.executable), tool.executable_hash]) != plan.tool_identity_digest:
            fail("GIT_COMMIT_TOOL_CHANGED")
        event = session.get(AuditEventRow, body.inspection_approval_id)
        try:
            payload = event.payload["payload"]
            expiry = datetime.fromisoformat(payload["expiresAt"])
            valid = (
                body.expected_inspection_plan_hash == plan.plan_hash
                and event.event_type == INSPECTION_APPROVED
                and event.task_id == row.task_id
                and event.actor != row.actor_id
                and CommitInspectionPlan.model_validate(payload["plan"]) == plan
                and expiry.tzinfo is not None
                and expiry > datetime.now(UTC)
            )
        except (AttributeError, KeyError, ValueError, TypeError):
            valid = False
        if not valid:
            fail("GIT_COMMIT_INSPECTION_APPROVAL_REQUIRED", 403)
        self.operator(
            RuntimeActorContext(actor_id=event.actor, stable_key="persisted-operator"), row, session
        )

    def approval(self, actor, row, plan, request, session):
        if request.expected_plan_hash != plan.plan_hash:
            fail("GIT_COMMIT_PLAN_CHANGED")
        event = session.get(AuditEventRow, request.approval_id)
        try:
            payload = event.payload["payload"]
            expiry = datetime.fromisoformat(payload["expiresAt"])
            valid = (
                event.event_type == APPROVED
                and event.task_id == row.task_id
                and event.actor != actor.actor_id
                and event.actor != row.actor_id
                and CommitAuthorizationPlan.model_validate(payload["plan"]) == plan
                and expiry.tzinfo is not None
                and expiry > datetime.now(UTC)
            )
        except (AttributeError, KeyError, ValueError, TypeError):
            valid = False
        if not valid:
            fail("GIT_COMMIT_APPROVAL_REQUIRED", 403)
        self.operator(
            RuntimeActorContext(actor_id=event.actor, stable_key="persisted-operator"), row, session
        )

    def measured(self, actor, workspace_id, body, callback=None, *, request=None):
        inspection_plan = self.inspection_plan(actor, workspace_id, body)
        source = self.source_record(actor, workspace_id, body.source_edit_record_id)
        artifacts = mutation_artifacts(self.app, actor, source)
        if artifacts.fingerprint != inspection_plan.native_artifact_manifest_digest:
            fail("GIT_COMMIT_SOURCE_RECORD_INVALID")
        # Validate the actual acknowledged creation checkpoint, not its ID alone.
        initial_creation = self.creation.read(actor, workspace_id)
        with self.repository.leases._write() as session:
            row, attempt, policy, creation, nonce = self.live(
                actor, workspace_id, session, request=request
            )
            self.inspection_approval(actor, row, inspection_plan, body, session)
            if creation != initial_creation:
                fail("GIT_COMMIT_WORKSPACE_INVALID")
            lineage = CommitLineage(
                actor_id=row.actor_id,
                runtime_run_id=row.runtime_run_id,
                attempt_id=attempt,
                worker_id=row.worker_id,
                source_edit_record_id=source.record_id,
                source_edit_checkpoint_id=None,
                source_edit_plan_hash=source.plan.source_plan.plan_hash,
                original_references=source.plan.source_plan.references,
            )

        def guard():
            with self.repository.leases._write() as session:
                current, current_attempt, _, current_creation, current_nonce = self.live(
                    actor, workspace_id, session, request=request
                )
                if (
                    current_attempt != attempt
                    or current_creation != creation
                    or current_nonce != nonce
                ):
                    fail("GIT_COMMIT_OWNER_CHANGED")
                self.inspection_approval(actor, current, inspection_plan, body, session)
                if request is not None and bound[0] is not None:
                    self.approval(actor, current, bound[0], request, session)
            if self.source_record(actor, workspace_id, body.source_edit_record_id) != source:
                fail("GIT_COMMIT_SOURCE_RECORD_INVALID")
            if mutation_artifacts(self.app, actor, source) != artifacts:
                fail("GIT_COMMIT_SOURCE_RECORD_INVALID")
            if self.creation.read(actor, workspace_id) != creation:
                fail("GIT_COMMIT_WORKSPACE_INVALID")

        bound = [None]

        def finalize(observation, filesystem_guard):
            proposal = propose_commit(
                observation,
                source.plan.source_plan,
                source.plan.observation,
                lineage,
                body.metadata,
                body.selection,
            )
            payload = dict(
                schema_version="1.0",
                operation="git.commit.prepare.authorize",
                workspace_id=workspace_id,
                source_edit_record_id=source.record_id,
                source_edit_record_plan_hash=source.plan.plan_hash,
                proposal=proposal.model_dump(mode="json"),
                inspection_plan=inspection_plan.model_dump(mode="json"),
                authorization_policy_digest=AUTHORIZATION_POLICY_DIGEST,
            )
            plan = CommitAuthorizationPlan(**payload, plan_hash=digest(payload))
            if len(plan.model_dump_json().encode()) > 131072:
                fail("GIT_COMMIT_OUTPUT_LIMIT")
            bound[0] = plan
            guard()
            return plan if callback is None else callback(plan, filesystem_guard)

        verifier = ChangeVerifier(self.git.tool(), guard)
        return verifier.inspect(
            policy, creation, nonce, finalize=finalize, native_artifacts=artifacts.artifacts
        )

    def preview(self, actor, workspace_id, body):
        return self.measured(actor, workspace_id, body)

    def approve(self, actor, body):
        def persist(plan, guard):
            with self.repository.leases._write() as session:
                row, *_ = self.live(actor, body.workspace_id, session)
                self.operator(actor, row, session)
                if body.expected_plan_hash != plan.plan_hash:
                    fail("GIT_COMMIT_PLAN_CHANGED")
                expiry = datetime.now(UTC) + timedelta(seconds=body.valid_for_seconds)
                event = self.git.event(
                    session,
                    row,
                    actor,
                    APPROVED,
                    dict(plan=plan.model_dump(mode="json"), expiresAt=expiry.isoformat()),
                )
                guard(check_authority=False)
                result = CommitPreparationApproval(
                    approval_id=event.id, approved_by=actor.actor_id, expires_at=expiry, plan=plan
                )
            self.repository.leases.repository.refresh_event_cursor()
            return result

        return self.measured(actor, body.workspace_id, body, persist)

    def preparation(self, session, workspace_id, command_id, actor_id):
        matches = list(
            session.scalars(
                select(AuditEventRow)
                .where(
                    AuditEventRow.event_type == PREPARED,
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
            fail("GIT_COMMIT_JOURNAL_INVALID")
        event = matches[0]
        try:
            record = PreparedCommitRecord.model_validate(event.payload["payload"]["record"])
            workspace = self.repository.lookup(session, workspace_id)
            if (
                record.plan.proposal.lineage.actor_id != actor_id
                or record.state != "prepared"
                or record.checkpoint_id is not None
                or event.task_id != workspace.task_id
                or record.workspace_id != workspace_id
                or record.command_id != command_id
                or record.plan.proposal.lineage.runtime_run_id != workspace.runtime_run_id
            ):
                fail("GIT_COMMIT_JOURNAL_INVALID")
            return record
        except (KeyError, TypeError, ValueError):
            fail("GIT_COMMIT_JOURNAL_INVALID")

    def checkpoint(self, actor, record):
        identifier = "checkpoint-" + record.operation_id
        lineage = record.plan.proposal.lineage
        integrity = "sha256:" + digest(record.model_dump(mode="json"))
        metadata = dict(
            workspaceId=record.workspace_id,
            operationId=record.operation_id,
            planHash=record.plan.plan_hash,
            approvalId=record.approval_id,
            inspectionApprovalId=record.inspection_approval_id,
        )
        matches = [
            value
            for value in self.runtime.checkpoints_authorized(lineage.runtime_run_id, actor)
            if value.checkpoint_id == identifier
        ]
        if not matches:
            return None, identifier, integrity, metadata
        value = matches[0]
        if (
            len(matches) != 1
            or value.attempt_id != lineage.attempt_id
            or value.integrity_digest != integrity
            or value.state_reference != "git-commit-preparation:" + record.operation_id
            or value.resume_cursor != "prepared"
            or value.metadata != metadata
        ):
            fail("GIT_COMMIT_CHECKPOINT_INVALID")
        return value, identifier, integrity, metadata

    def acknowledge(self, actor, request, record, fs_guard):
        existing, identifier, integrity, metadata = self.checkpoint(actor, record)
        lineage = record.plan.proposal.lineage
        if existing is None:
            snapshot = self.runtime.read_run_authorized(lineage.runtime_run_id, actor)

            def commit_guard(session):
                row, attempt, *_ = self.live(actor, record.workspace_id, session, request=request)
                self.approval(actor, row, record.plan, request, session)
                self.inspection_approval(actor, row, record.plan.inspection_plan, request, session)
                if (
                    attempt != lineage.attempt_id
                    or self.preparation(
                        session, record.workspace_id, request.command_id, actor.actor_id
                    )
                    != record
                ):
                    fail("GIT_COMMIT_JOURNAL_INVALID")
                fs_guard(check_authority=False)

            self.runtime.handle_authorized(
                RecordCheckpointCommand(
                    run_id=lineage.runtime_run_id,
                    command_id="ack-" + identifier,
                    expected_run_version=snapshot.version,
                    timestamp=datetime.now(UTC),
                    checkpoint_id=identifier,
                    attempt_id=lineage.attempt_id,
                    state_reference="git-commit-preparation:" + record.operation_id,
                    integrity_digest=integrity,
                    resume_cursor="prepared",
                    checkpoint_metadata=metadata,
                ),
                actor,
                execution_fence=RuntimeExecutionFence(
                    task_id=self.task_id(record.workspace_id),
                    worker_id=request.worker_id,
                    lease_token=request.lease_token,
                ),
                commit_guard=commit_guard,
            )
        acknowledged, *_ = self.checkpoint(actor, record)
        if acknowledged is None:
            fail("GIT_COMMIT_CHECKPOINT_INVALID")
        with self.repository.leases._write() as session:
            row, attempt, *_ = self.live(actor, record.workspace_id, session, request=request)
            self.approval(actor, row, record.plan, request, session)
            self.inspection_approval(actor, row, record.plan.inspection_plan, request, session)
            if attempt != lineage.attempt_id:
                fail("GIT_COMMIT_OWNER_CHANGED")
            acknowledgements = list(
                session.scalars(
                    select(AuditEventRow)
                    .where(
                        AuditEventRow.event_type == ACKNOWLEDGED,
                        AuditEventRow.payload["payload"]["operationId"].as_string()
                        == record.operation_id,
                    )
                    .limit(2)
                )
            )
            if not acknowledgements:
                self.git.event(
                    session,
                    row,
                    actor,
                    ACKNOWLEDGED,
                    dict(
                        operationId=record.operation_id,
                        checkpointId=identifier,
                        planHash=record.plan.plan_hash,
                        authorizationApprovalId=request.approval_id,
                        authorizationInspectionApprovalId=request.inspection_approval_id,
                    ),
                )
            elif (
                len(acknowledgements) != 1
                or acknowledgements[0].payload["payload"].get("checkpointId") != identifier
                or acknowledgements[0].payload["payload"].get("planHash") != record.plan.plan_hash
            ):
                fail("GIT_COMMIT_JOURNAL_INVALID")
            fs_guard(check_authority=False)
        self.repository.leases.repository.refresh_event_cursor()
        return record.model_copy(update={"state": "checkpointed", "checkpoint_id": identifier})

    def task_id(self, workspace_id):
        with self.repository.sessions() as session:
            return self.repository.lookup(session, workspace_id).task_id

    def prepare(self, actor, workspace_id, request):
        def persist(plan, fs_guard):
            with self.repository.leases._write() as session:
                row, *_ = self.live(actor, workspace_id, session, request=request)
                self.approval(actor, row, plan, request, session)
                record = self.preparation(session, workspace_id, request.command_id, actor.actor_id)
                if record is not None:
                    if record.plan != plan:
                        fail("GIT_COMMIT_COMMAND_CONFLICT")
                else:
                    count = list(
                        session.scalars(
                            select(AuditEventRow.id)
                            .where(
                                AuditEventRow.event_type == PREPARED,
                                AuditEventRow.payload["payload"]["record"][
                                    "workspace_id"
                                ].as_string()
                                == workspace_id,
                            )
                            .limit(32)
                        )
                    )
                    if len(count) >= 32:
                        fail("GIT_COMMIT_PREPARATION_LIMIT")
                    record = PreparedCommitRecord(
                        operation_id="commit-prepare-" + uuid4().hex,
                        command_id=request.command_id,
                        workspace_id=workspace_id,
                        plan=plan,
                        approval_id=request.approval_id,
                        inspection_approval_id=request.inspection_approval_id,
                        state="prepared",
                        checkpoint_id=None,
                        created_at=datetime.now(UTC),
                    )
                    self.git.event(
                        session, row, actor, PREPARED, dict(record=record.model_dump(mode="json"))
                    )
                fs_guard(check_authority=False)
            self.repository.leases.repository.refresh_event_cursor()
            return self.acknowledge(actor, request, record, fs_guard)

        try:
            return self.measured(actor, workspace_id, request, persist, request=request)
        except SQLAlchemyError:
            fail("GIT_COMMIT_STORAGE_FAILED")

    def read(self, actor, workspace_id, command_id):
        with self.repository.sessions() as session:
            row = self.repository.lookup(session, workspace_id)
            self.workspace.authorize(actor, row.task_id, session)
            self.permission(actor, row, session)
            record = self.preparation(session, workspace_id, command_id, row.actor_id)
            if record is None:
                fail("GIT_COMMIT_PREPARATION_NOT_FOUND", 404)
            acks = list(
                session.scalars(
                    select(AuditEventRow)
                    .where(
                        AuditEventRow.event_type == ACKNOWLEDGED,
                        AuditEventRow.payload["payload"]["operationId"].as_string()
                        == record.operation_id,
                    )
                    .limit(2)
                )
            )
        if not acks:
            return record
        checkpoint, identifier, *_ = self.checkpoint(actor, record)
        if (
            checkpoint is None
            or len(acks) != 1
            or acks[0].payload["payload"].get("checkpointId") != identifier
            or acks[0].payload["payload"].get("planHash") != record.plan.plan_hash
        ):
            fail("GIT_COMMIT_CHECKPOINT_INVALID")
        return record.model_copy(update={"state": "checkpointed", "checkpoint_id": identifier})
