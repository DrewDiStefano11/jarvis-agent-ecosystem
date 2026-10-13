"""Protected recorded recovery evidence without filesystem reads or effects."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.db.models import AuditEventRow
from app.models.source_mutation import MutationPlan, MutationRecord
from app.models.source_mutation_history import MutationHistory
from app.self_build.policy import digest
from app.source_mutation.driver import refuse
from app.source_mutation.repository import SourceMutationRow
from app.source_mutation.service import (
    MUTATION_POLICY_DIGEST,
    SourceMutationService,
    intent_digest,
)


class MutationHistoryReader:
    def __init__(self, app):
        self.service = SourceMutationService(app)

    def read(self, actor, editing_record_id):
        try:
            return self._read(actor, editing_record_id)
        except SQLAlchemyError:
            refuse("SOURCE_MUTATION_STORAGE_FAILED")

    def _read(self, actor, editing_record_id):
        # Protected planning reads authenticate task read + workspace permission,
        # but deliberately require no live lease, write grant or current attempt.
        proposal = self.service.planning.read(actor, editing_record_id)
        with self.service.repository.sessions() as session:
            stored = session.get(SourceMutationRow, editing_record_id)
            if stored is None:
                refuse("SOURCE_MUTATION_INTENT_REQUIRED")
            row = self.service.repository.lookup(session, proposal.plan.workspace_id)
            reservation = self.service.repository.contract(row)
            creation, _, creation_digest = self.service.creation.intent(row)
            try:
                native_creation, creation_identifier, _ = self.service.creation.checkpoint(
                    actor, creation, creation_digest
                )
            except (ValueError, TypeError, AttributeError, KeyError):
                refuse("SOURCE_MUTATION_CREATION_CHECKPOINT_INVALID")
            creation_payload = AuditEventRow.payload["payload"]
            creation_acknowledged = (
                session.scalar(
                    select(AuditEventRow.id)
                    .where(
                        AuditEventRow.task_id == row.task_id,
                        AuditEventRow.event_type
                        == "self_build.workspace_creation.checkpoint_acknowledged",
                        creation_payload["operationId"].as_string() == creation.operation_id,
                        creation_payload["checkpointId"].as_string() == creation_identifier,
                        creation_payload["intentDigest"].as_string() == creation_digest,
                    )
                    .limit(1)
                )
                is not None
            )
            if (
                creation.state != "ready"
                or native_creation is None
                or creation.checkpoint_id != creation_identifier
                or not creation_acknowledged
            ):
                refuse("SOURCE_MUTATION_CREATION_CHECKPOINT_INVALID")
            try:
                record = MutationRecord.model_validate(stored.payload)
                plan = record.plan
                expected = dict(
                    schema_version="1.0",
                    editing_record_id=editing_record_id,
                    editing_plan_hash=proposal.plan.plan_hash,
                    workspace_id=row.id,
                    task_id=row.task_id,
                    creation_operation_id=creation.operation_id,
                    creation_plan_hash=creation.plan.plan_hash,
                    ownership_digest=creation.ownership_digest,
                    runtime_run_id=row.runtime_run_id,
                    attempt_id=creation.attempt_id,
                    worker_id=row.worker_id,
                    mutation_policy_digest=MUTATION_POLICY_DIGEST,
                )
                if (
                    stored.operation_id != record.operation_id
                    or stored.editing_record_id != editing_record_id
                    or stored.integrity_digest != intent_digest(record)
                    or plan != MutationPlan(**expected, plan_hash=digest(expected))
                    or proposal.plan.workspace_plan_hash != reservation.plan.plan_hash
                    or proposal.plan.runtime_attempt_id != creation.attempt_id
                ):
                    raise ValueError("historical lineage mismatch")
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
                    or (
                        record.state in {"complete", "finalizing"}
                        and record.completed_count != total
                    )
                ):
                    raise ValueError("historical position mismatch")
                original = session.get(AuditEventRow, record.original_approval_id)
                run = self.service.repository.runtime(session, row.runtime_run_id)
                expires = datetime.fromisoformat(original.payload["payload"]["expiresAt"])
                if (
                    original is None
                    or original.event_type != "self_build.source_mutation.approved"
                    or original.task_id != plan.task_id
                    or expires.tzinfo is None
                    or original.actor in {row.actor_id, run.agent_id}
                    or MutationPlan.model_validate(original.payload["payload"]["plan"]) != plan
                ):
                    raise ValueError("historical approval mismatch")
                # The mutable row and its rehash cannot establish provenance.
                # Reconstruct its initial position to bind immutable references
                # to the original append-only preparation transaction.
                initial = record.model_copy(
                    update=dict(
                        state="prepared",
                        completed_count=0,
                        pending_index=None,
                        staging_device=None,
                        staging_inode=None,
                        prefix_digest=None,
                        final_observation_digest=None,
                        checkpoint_id=None,
                    )
                )
                audit_payload = AuditEventRow.payload["payload"]
                prepared = session.scalar(
                    select(AuditEventRow.id)
                    .where(
                        AuditEventRow.task_id == plan.task_id,
                        AuditEventRow.event_type == "self_build.source_mutation.prepared",
                        audit_payload["operationId"].as_string() == record.operation_id,
                        audit_payload["editingRecordId"].as_string() == editing_record_id,
                        audit_payload["planHash"].as_string() == plan.plan_hash,
                        audit_payload["intentDigest"].as_string() == intent_digest(initial),
                        audit_payload["authorizationApprovalId"].as_string()
                        == record.original_approval_id,
                    )
                    .limit(1)
                )
                # Sequence order is authoritative even when clocks regress.
                # Older valid phases cannot authorize a projection rollback.
                latest = session.scalar(
                    select(AuditEventRow)
                    .where(
                        AuditEventRow.task_id == plan.task_id,
                        AuditEventRow.event_type.in_(
                            (
                                "self_build.source_mutation.prepared",
                                "self_build.source_mutation.phase_recorded",
                            )
                        ),
                        audit_payload["editingRecordId"].as_string() == editing_record_id,
                        audit_payload["planHash"].as_string() == plan.plan_hash,
                    )
                    .order_by(AuditEventRow.sequence_number.desc())
                    .limit(1)
                )
                if (
                    prepared is None
                    or latest is None
                    or latest.payload["payload"]["operationId"] != record.operation_id
                    or latest.payload["payload"]["intentDigest"] != intent_digest(record)
                ):
                    raise ValueError("historical journal audit mismatch")
            except (ValueError, AttributeError, TypeError, KeyError):
                refuse("SOURCE_MUTATION_HISTORY_INVALID")
            # checkpoint_id is deliberately outside the native intent digest.
            # Only the append-only acknowledgement transaction proves that the
            # journal acknowledged this exact operation and recovery position.
            payload = AuditEventRow.payload["payload"]
            audit_acknowledged = (
                session.scalar(
                    select(AuditEventRow.id)
                    .where(
                        AuditEventRow.task_id == plan.task_id,
                        AuditEventRow.event_type
                        == "self_build.source_mutation.checkpoint_acknowledged",
                        payload["operationId"].as_string() == record.operation_id,
                        payload["editingRecordId"].as_string() == editing_record_id,
                        payload["planHash"].as_string() == plan.plan_hash,
                        payload["intentDigest"].as_string() == intent_digest(record),
                    )
                    .limit(1)
                )
                is not None
            )
        try:
            checkpoint, identifier, _, _ = self.service.checkpoint(actor, record)
        except (ValueError, TypeError, AttributeError, KeyError):
            refuse("SOURCE_MUTATION_CHECKPOINT_INVALID")
        claimed = record.checkpoint_id is not None
        if audit_acknowledged and not claimed:
            refuse("SOURCE_MUTATION_CHECKPOINT_INVALID")
        if claimed and (checkpoint is None or record.checkpoint_id != identifier):
            refuse("SOURCE_MUTATION_CHECKPOINT_INVALID")
        return MutationHistory(
            record=record,
            native_checkpoint_present=checkpoint is not None,
            journal_checkpoint_acknowledged=claimed and audit_acknowledged,
        )
