"""Native exact-plan authority, durable intent and checkpoint-fenced source edits."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.agent_runtime.authorization import RuntimeActorContext
from app.agent_runtime.repository import RuntimeExecutionFence
from app.db.models import AuditEventRow
from app.models import source_mutation as contracts
from app.models.agent_runtime import RecordCheckpointCommand
from app.models.source_mutation import (
    ApplySourceMutation,
    ApproveSourceMutation,
    MutationApproval,
    MutationPlan,
    MutationRecord,
)
from app.self_build.checkout_driver import CheckoutDriver
from app.self_build.creation_service import (
    CREATION_POLICY_DIGEST,
    WorkspaceCreationService,
    ownership,
)
from app.self_build.policy import digest
from app.source_edit_planning.repository import SourceEditingPlanRow
from app.source_edit_planning.service import EDITING_POLICY_DIGEST, SourceEditingPlanService
from app.source_mutation import driver, native, repository
from app.source_mutation.driver import SourceMutationDriver, allowed_changes, refuse
from app.source_mutation.repository import SourceMutationRow

MUTATION_POLICY_DIGEST = sha256(
    (
        "".join(
            Path(module).read_text(encoding="utf-8")
            for module in (
                __file__,
                contracts.__file__,
                driver.__file__,
                native.__file__,
                repository.__file__,
            )
        )
        + CREATION_POLICY_DIGEST
        + EDITING_POLICY_DIGEST
    ).encode()
).hexdigest()


def intent_digest(record):
    payload = record.model_dump(mode="json", exclude={"checkpoint_id", "updated_at"})
    if payload["state"] == "finalizing":
        payload["state"] = "complete"
    return digest(payload)


class SourceMutationService:
    """Internal service only. No HTTP/model importer, worker or execution tool."""

    def __init__(self, app):
        self.app = app
        self.creation = WorkspaceCreationService(app)
        self.planning = SourceEditingPlanService(app)
        self.workspace = self.creation.workspace
        self.repository = self.creation.repository
        self.runtime = self.creation.runtime

    def permission(self, actor, row, session):
        self.workspace.authorize(actor, row.task_id, session, write=True)
        decision = self.workspace.identity.check_permission_resource_access(
            actor.actor_id,
            "self_build.workspace.source_edit",
            "task",
            row.task_id,
            session=session,
        )
        if not decision.allowed:
            refuse("SOURCE_MUTATION_PERMISSION_DENIED")

    def operator(self, actor, row, session):
        self.creation.git.operator(actor, row, session)
        self.permission(actor, row, session)
        access = getattr(self.app.state, "remote_control_service", None)
        if access is None or access.access.actor_id != actor.actor_id:
            refuse("SOURCE_MUTATION_OPERATOR_REQUIRED")

    def plan_in_session(self, actor, record_id, session):
        stored = session.get(SourceEditingPlanRow, record_id)
        if stored is None:
            refuse("SOURCE_MUTATION_PLAN_NOT_FOUND")
        row = self.repository.lookup(session, stored.workspace_id)
        self.permission(actor, row, session)
        proposal = self.planning.record(stored)
        reservation = self.repository.contract(row)
        policy = self.workspace.policies.get(row.repository_id)
        if row.state != "reserved" or self.repository.reason(
            session, row, digest(policy.model_dump())
        ):
            refuse("SOURCE_MUTATION_OWNER_INACTIVE")
        creation, nonce, creation_digest = self.creation.intent(row)
        tool = self.creation.git.tool()
        if (
            tool.executable_hash != creation.plan.tool_sha256
            or digest([str(tool.executable), tool.executable_hash])
            != creation.plan.tool_identity_digest
        ):
            refuse("SOURCE_MUTATION_TOOL_CHANGED")
        run = self.repository.runtime(session, row.runtime_run_id)
        if (
            creation.state != "ready"
            or creation.plan.creation_policy_digest != CREATION_POLICY_DIGEST
            or creation.plan.workspace != reservation.plan
            or run.state != "running"
            or not run.active_attempt_id
            or creation.attempt_id != run.active_attempt_id
            or proposal.plan.runtime_attempt_id != run.active_attempt_id
            or proposal.plan.workspace_id != row.id
            or proposal.plan.task_id != row.task_id
            or proposal.plan.runtime_run_id != row.runtime_run_id
            or proposal.plan.workspace_version != row.version
            or proposal.plan.workspace_plan_hash != reservation.plan.plan_hash
            or proposal.plan.source_plan.references.original_plan_hash != reservation.plan.plan_hash
            or proposal.plan.source_plan.references.authorization_reference != row.approval_id
            or proposal.plan.source_plan.source.repository_identity
            != reservation.plan.repository_identity
            or proposal.plan.source_plan.source.head_sha != reservation.plan.base_sha
            or proposal.plan.source_plan.source.base_sha != reservation.plan.base_sha
        ):
            refuse("SOURCE_MUTATION_LINEAGE_CONFLICT")
        checkpoint, identifier, _ = self.creation.checkpoint(actor, creation, creation_digest)
        if checkpoint is None or identifier != creation.checkpoint_id:
            refuse("SOURCE_MUTATION_CREATION_CHECKPOINT_INVALID")
        allowed_changes(proposal.plan.source_plan)
        payload = dict(
            schema_version="1.0",
            editing_record_id=record_id,
            editing_plan_hash=proposal.plan.plan_hash,
            workspace_id=row.id,
            task_id=row.task_id,
            creation_operation_id=creation.operation_id,
            creation_plan_hash=creation.plan.plan_hash,
            ownership_digest=creation.ownership_digest,
            runtime_run_id=row.runtime_run_id,
            attempt_id=run.active_attempt_id,
            worker_id=row.worker_id,
            mutation_policy_digest=MUTATION_POLICY_DIGEST,
        )
        return row, MutationPlan(**payload, plan_hash=digest(payload)), proposal, creation, nonce

    def preview(self, actor, record_id):
        with self.repository.sessions() as session:
            return self.plan_in_session(actor, record_id, session)[1]

    def approve(self, actor, request):
        try:
            request = ApproveSourceMutation.model_validate(
                request.model_dump(mode="json", warnings=False)
            )
        except (ValueError, AttributeError, TypeError):
            refuse("SOURCE_MUTATION_REQUEST_INVALID")
        with self.repository.leases._write() as session:
            row, plan, _, _, _ = self.plan_in_session(actor, request.editing_record_id, session)
            self.operator(actor, row, session)
            if plan.plan_hash != request.expected_plan_hash:
                refuse("SOURCE_MUTATION_PLAN_CHANGED")
            expires = datetime.now(UTC) + timedelta(seconds=request.valid_for_seconds)
            event = self.creation.git.event(
                session,
                row,
                actor,
                "self_build.source_mutation.approved",
                {
                    "plan": plan.model_dump(mode="json"),
                    "expiresAt": expires.isoformat(),
                },
            )
            result = MutationApproval(
                approval_id=event.id, plan=plan, approved_by=actor.actor_id, expires_at=expires
            )
        self.repository.leases.repository.refresh_event_cursor()
        return result

    def fence(self, actor, request, session):
        row, plan, proposal, creation, nonce = self.plan_in_session(
            actor, request.editing_record_id, session
        )
        if request.expected_plan_hash != plan.plan_hash:
            refuse("SOURCE_MUTATION_PLAN_CHANGED")
        self.repository.leases._require_lease(
            session, row.task_id, request.worker_id, request.lease_token, datetime.now(UTC)
        )
        run = self.repository.runtime(session, row.runtime_run_id)
        if (
            request.worker_id != row.worker_id
            or digest(request.lease_token) != row.lease_fingerprint
            or self.repository.executor(session, run) != request.worker_id
            or actor.actor_id != row.actor_id
        ):
            refuse("SOURCE_MUTATION_OWNER_CONFLICT")
        event = session.get(AuditEventRow, request.approval_id)
        try:
            payload = event.payload["payload"]
            expires = datetime.fromisoformat(payload["expiresAt"])
            valid = (
                event.event_type == "self_build.source_mutation.approved"
                and event.task_id == row.task_id
                and event.actor != actor.actor_id
                and MutationPlan.model_validate(payload["plan"]) == plan
                and expires.tzinfo is not None
                and expires > datetime.now(UTC)
            )
        except (AttributeError, KeyError, TypeError, ValueError):
            valid = False
        if not valid:
            refuse("SOURCE_MUTATION_APPROVAL_REQUIRED")
        self.operator(
            RuntimeActorContext(actor_id=event.actor, stable_key="persisted-operator"), row, session
        )
        return row, plan, proposal, creation, nonce

    def intent(self, stored, plan, session):
        try:
            record = MutationRecord.model_validate(stored.payload)
            if (
                stored.editing_record_id != plan.editing_record_id
                or stored.operation_id != record.operation_id
                or record.plan != plan
                or stored.integrity_digest != intent_digest(record)
                or record.completed_count > 64
            ):
                raise ValueError("inconsistent intent")
            event = session.get(AuditEventRow, record.original_approval_id)
            payload = event.payload["payload"]
            expires = datetime.fromisoformat(payload["expiresAt"])
            workspace = self.repository.lookup(session, plan.workspace_id)
            run = self.repository.runtime(session, plan.runtime_run_id)
            if (
                event.event_type != "self_build.source_mutation.approved"
                or event.task_id != plan.task_id
                or event.actor in {workspace.actor_id, run.agent_id}
                or MutationPlan.model_validate(payload["plan"]) != plan
                or expires.tzinfo is None
            ):
                raise ValueError("inconsistent original approval")
            return record
        except (AttributeError, KeyError, TypeError, ValueError):
            refuse("SOURCE_MUTATION_INTENT_INVALID")

    def current(self, actor, request):
        with self.repository.leases._write() as session:
            _, plan, proposal, creation, nonce = self.fence(actor, request, session)
            stored = session.get(SourceMutationRow, request.editing_record_id)
            if stored is None:
                refuse("SOURCE_MUTATION_INTENT_REQUIRED")
            record = self.intent(stored, plan, session)
            total = len(proposal.plan.source_plan.changes)
            expected_prefix = digest(
                [
                    item.model_dump(mode="json")
                    for item in proposal.plan.source_plan.preview[: record.completed_count]
                ]
            )
            if (
                record.completed_count > total
                or (record.completed_count and record.prefix_digest != expected_prefix)
                or (record.pending_index is not None and record.pending_index >= total)
                or (record.state in {"complete", "finalizing"} and record.completed_count != total)
            ):
                refuse("SOURCE_MUTATION_INTENT_INVALID")
            return record, proposal, creation, nonce

    def event(self, session, row, actor, record, phase, approval_id):
        self.creation.git.event(
            session,
            row,
            actor,
            "self_build.source_mutation." + phase,
            {
                "operationId": record.operation_id,
                "editingRecordId": record.plan.editing_record_id,
                "planHash": record.plan.plan_hash,
                "intentDigest": intent_digest(record),
                "state": record.state,
                "completedCount": record.completed_count,
                "pendingIndex": record.pending_index,
                "authorizationApprovalId": approval_id,
            },
        )

    def prepare(self, actor, request):
        with self.repository.leases._write() as session:
            row, plan, _, _, _ = self.fence(actor, request, session)
            stored = session.get(SourceMutationRow, request.editing_record_id)
            if stored is None:
                existing_lineage = session.scalar(
                    select(SourceMutationRow.operation_id)
                    .join(
                        SourceEditingPlanRow,
                        SourceMutationRow.editing_record_id == SourceEditingPlanRow.id,
                    )
                    .where(SourceEditingPlanRow.workspace_id == row.id)
                    .limit(1)
                )
                if existing_lineage is not None:
                    refuse("SOURCE_MUTATION_WORKSPACE_ALREADY_EDITED")
                now = datetime.now(UTC)
                record = MutationRecord(
                    operation_id="source-edit-" + uuid4().hex,
                    plan=plan,
                    original_approval_id=request.approval_id,
                    state="prepared",
                    completed_count=0,
                    created_at=now,
                    updated_at=now,
                )
                session.add(
                    SourceMutationRow(
                        editing_record_id=request.editing_record_id,
                        operation_id=record.operation_id,
                        payload=record.model_dump(mode="json"),
                        integrity_digest=intent_digest(record),
                    )
                )
                self.event(session, row, actor, record, "prepared", request.approval_id)
            else:
                record = self.intent(stored, plan, session)
        self.repository.leases.repository.refresh_event_cursor()
        return record

    def checkpoint(self, actor, record):
        cursor = record.state + "-" + str(record.completed_count) + "-" + str(record.pending_index)
        if record.state == "finalizing":
            cursor = cursor.replace("finalizing", "complete", 1)
        identifier = "checkpoint-" + record.operation_id + "-" + cursor
        if record.staging_inode is not None:
            cursor += "-staged"
            identifier += "-staged"
        metadata = dict(
            operationId=record.operation_id,
            planHash=record.plan.plan_hash,
            completedCount=record.completed_count,
            pendingIndex=record.pending_index,
            stagingDevice=record.staging_device,
            stagingInode=record.staging_inode,
        )
        found = [
            item
            for item in self.runtime.checkpoints_authorized(record.plan.runtime_run_id, actor)
            if item.checkpoint_id == identifier
        ]
        if found and (
            len(found) != 1
            or found[0].attempt_id != record.plan.attempt_id
            or found[0].integrity_digest != "sha256:" + intent_digest(record)
            or found[0].state_reference != "source-mutation:" + record.operation_id
            or found[0].resume_cursor != cursor
            or found[0].metadata != metadata
        ):
            refuse("SOURCE_MUTATION_CHECKPOINT_INVALID")
        return found[0] if found else None, identifier, cursor, metadata

    def acknowledge(self, actor, request, record, publication_guard=None):
        found, identifier, cursor, metadata = self.checkpoint(actor, record)
        if record.checkpoint_id is not None and (
            found is None or record.checkpoint_id != identifier
        ):
            refuse("SOURCE_MUTATION_CHECKPOINT_INVALID")
        if found is None:
            snapshot = self.runtime.read_run_authorized(record.plan.runtime_run_id, actor)

            def guard(session):
                if publication_guard:
                    publication_guard()
                _, plan, _, _, _ = self.fence(actor, request, session)
                current = self.intent(
                    session.get(SourceMutationRow, request.editing_record_id), plan, session
                )
                if intent_digest(current) != intent_digest(record):
                    refuse("SOURCE_MUTATION_POSITION_CONFLICT")

            self.runtime.handle_authorized(
                RecordCheckpointCommand(
                    run_id=record.plan.runtime_run_id,
                    command_id="ack-" + identifier,
                    expected_run_version=snapshot.version,
                    timestamp=datetime.now(UTC),
                    checkpoint_id=identifier,
                    attempt_id=record.plan.attempt_id,
                    state_reference="source-mutation:" + record.operation_id,
                    integrity_digest="sha256:" + intent_digest(record),
                    resume_cursor=cursor,
                    checkpoint_metadata=metadata,
                ),
                actor,
                execution_fence=RuntimeExecutionFence(
                    task_id=record.plan.task_id,
                    worker_id=request.worker_id,
                    lease_token=request.lease_token,
                ),
                commit_guard=guard,
            )
            confirmed, _, _, _ = self.checkpoint(actor, record)
            if confirmed is None:
                refuse("SOURCE_MUTATION_CHECKPOINT_INVALID")
        with self.repository.leases._write() as session:
            row, plan, _, _, _ = self.fence(actor, request, session)
            stored = session.get(SourceMutationRow, request.editing_record_id)
            current = self.intent(stored, plan, session)
            if intent_digest(current) != intent_digest(record):
                refuse("SOURCE_MUTATION_POSITION_CONFLICT")
            if current.checkpoint_id is None:
                current = current.model_copy(
                    update={
                        "checkpoint_id": identifier,
                        "state": "complete" if current.state == "finalizing" else current.state,
                        "updated_at": datetime.now(UTC),
                    }
                )
                stored.payload = current.model_dump(mode="json")
                self.event(
                    session, row, actor, current, "checkpoint_acknowledged", request.approval_id
                )
            if publication_guard:
                publication_guard()
        self.repository.leases.repository.refresh_event_cursor()
        return current

    def transition(self, actor, request, previous, publication_guard=None, **changes):
        if changes.keys() - {
            "state",
            "completed_count",
            "pending_index",
            "staging_device",
            "staging_inode",
            "prefix_digest",
            "final_observation_digest",
        }:
            refuse("SOURCE_MUTATION_TRANSITION_INVALID")
        with self.repository.leases._write() as session:
            row, plan, _, _, _ = self.fence(actor, request, session)
            stored = session.get(SourceMutationRow, request.editing_record_id)
            current = self.intent(stored, plan, session)
            if current != previous:
                refuse("SOURCE_MUTATION_POSITION_CONFLICT")
            payload = (
                current.model_dump()
                | changes
                | dict(checkpoint_id=None, updated_at=datetime.now(UTC))
            )
            updated = MutationRecord.model_validate(payload)
            stored.payload, stored.integrity_digest = (
                updated.model_dump(mode="json"),
                intent_digest(updated),
            )
            self.event(session, row, actor, updated, "phase_recorded", request.approval_id)
        self.repository.leases.repository.refresh_event_cursor()
        return self.acknowledge(actor, request, updated, publication_guard)

    def apply(self, actor, request):
        try:
            request = ApplySourceMutation.model_validate(
                request.model_dump(mode="json", warnings=False)
            )
            return self._apply(actor, request)
        except SQLAlchemyError:
            refuse("SOURCE_MUTATION_STORAGE_FAILED")
        except (AttributeError, TypeError, ValueError):
            refuse("SOURCE_MUTATION_REQUEST_INVALID")

    def _apply(self, actor, request):
        record = self.prepare(actor, request)
        record, proposal, creation, nonce = self.current(actor, request)
        if record.state != "finalizing":
            record = self.acknowledge(actor, request, record)

        def authority_check():
            self.current(actor, request)

        checkout = CheckoutDriver(self.creation.git.tool(), authority_check)
        mutator = SourceMutationDriver(checkout)
        policy = self.workspace.policies.get(creation.plan.workspace.repository_id)

        def begin(index):
            nonlocal record
            if record.state == "complete" or record.pending_index == index:
                return
            if record.completed_count != index or record.pending_index is not None:
                refuse("SOURCE_MUTATION_POSITION_CONFLICT")
            record = self.transition(actor, request, record, state="editing", pending_index=index)

        def acknowledged(count, prefix):
            nonlocal record
            if record.state == "complete":
                return
            if count != record.completed_count + 1 or record.pending_index != count - 1:
                refuse("SOURCE_MUTATION_POSITION_CONFLICT")
            record = self.transition(
                actor,
                request,
                record,
                state="editing",
                pending_index=None,
                staging_device=None,
                staging_inode=None,
                completed_count=count,
                prefix_digest=prefix,
            )

        def claim_stage(index, device_inode):
            nonlocal record
            if record.pending_index != index or record.staging_inode is not None:
                refuse("SOURCE_MUTATION_POSITION_CONFLICT")
            record = self.transition(
                actor,
                request,
                record,
                staging_device=device_inode[0],
                staging_inode=device_inode[1],
            )

        def finalize(observation_digest, guard):
            nonlocal record
            if record.state in {"complete", "finalizing"}:
                if record.final_observation_digest != observation_digest:
                    refuse("SOURCE_MUTATION_AFTER_CONFLICT")
                return self.acknowledge(actor, request, record, guard)
            return self.transition(
                actor,
                request,
                record,
                guard,
                state="finalizing",
                final_observation_digest=observation_digest,
            )

        return mutator.apply(
            policy,
            creation.plan,
            ownership(creation, nonce),
            proposal.plan.source_plan,
            proposal.plan.observation,
            record.operation_id,
            original_code_state=proposal.plan.original_code_state,
            expected_code_state=proposal.plan.expected_code_state,
            completed=record.completed_count,
            pending=record.pending_index,
            staging_identity=(record.staging_device, record.staging_inode)
            if record.staging_inode is not None
            else None,
            claim_stage=claim_stage,
            begin=begin,
            acknowledge=acknowledged,
            finalize=finalize,
        )
