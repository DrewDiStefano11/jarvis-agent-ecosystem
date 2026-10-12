"""Protected recorded recovery evidence without filesystem reads or effects."""

from datetime import datetime

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
            native_creation, creation_identifier, _ = self.service.creation.checkpoint(
                actor, creation, creation_digest
            )
            if (
                creation.state != "ready"
                or native_creation is None
                or creation.checkpoint_id != creation_identifier
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
            except (ValueError, AttributeError, TypeError, KeyError):
                refuse("SOURCE_MUTATION_HISTORY_INVALID")
        checkpoint, identifier, _, _ = self.service.checkpoint(actor, record)
        acknowledged = record.checkpoint_id is not None
        if acknowledged and (checkpoint is None or record.checkpoint_id != identifier):
            refuse("SOURCE_MUTATION_CHECKPOINT_INVALID")
        return MutationHistory(
            record=record,
            native_checkpoint_present=checkpoint is not None,
            journal_checkpoint_acknowledged=acknowledged,
        )
