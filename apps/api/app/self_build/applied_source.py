"""Consume protected source recovery evidence and independently remeasure prepared bytes.

No source apply/replay, Git object writes, staging, ref updates or publication.
"""

from hashlib import sha256
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from app.core.errors import DomainError
from app.models import git_applied_source as contracts
from app.models.git_applied_source import AppliedCommitPreparation, AppliedSourceEvidence
from app.models.git_commit_authorization import PrepareNativeCommitRequest
from app.models.source_mutation_history import MutationHistory
from app.self_build.commit_service import AUTHORIZATION_POLICY_DIGEST, CommitPreparationService
from app.self_build.policy import digest
from app.source_mutation import history as history_module
from app.source_mutation import service as mutation_service
from app.source_mutation.driver import expected_observation
from app.source_mutation.history import MutationHistoryReader
from app.source_mutation.service import intent_digest

PROOF_POLICY_DIGEST = sha256(
    (
        Path(__file__).read_text(encoding="utf-8")
        + Path(contracts.__file__).read_text(encoding="utf-8")
        + Path(history_module.__file__).read_text(encoding="utf-8")
        + AUTHORIZATION_POLICY_DIGEST
        + mutation_service.MUTATION_POLICY_DIGEST
    ).encode()
).hexdigest()


def refuse(code):
    raise DomainError(code, "Applied-source commit binding failed a native evidence check.", 409)


class AppliedSourceCommitService:
    def __init__(self, app):
        self.preparation = CommitPreparationService(app)
        self.history = MutationHistoryReader(app)

    def source(self, actor, workspace_id, editing_record_id):
        value = self.history.read(actor, editing_record_id)
        value = MutationHistory.model_validate(value.model_dump(mode="json"))
        record = value.record
        source = self.preparation.source_record(actor, workspace_id, editing_record_id)
        expected = expected_observation(
            source.plan.observation,
            source.plan.source_plan.changes,
            len(source.plan.source_plan.changes),
        )
        if (
            record.state != "complete"
            or not value.native_checkpoint_present
            or not value.journal_checkpoint_acknowledged
            or record.checkpoint_id is None
            or record.plan.workspace_id != workspace_id
            or record.plan.editing_record_id != editing_record_id
            or record.plan.editing_plan_hash != source.plan.plan_hash
            or record.pending_index is not None
            or record.staging_inode is not None
            or record.completed_count != len(source.plan.source_plan.changes)
            or record.final_observation_digest != digest(expected.model_dump(mode="json"))
        ):
            refuse("GIT_COMMIT_APPLIED_SOURCE_REQUIRED")
        payload = dict(
            schema_version="1.0",
            workspace_id=workspace_id,
            editing_record_id=editing_record_id,
            editing_plan_hash=record.plan.editing_plan_hash,
            creation_operation_id=record.plan.creation_operation_id,
            creation_plan_hash=record.plan.creation_plan_hash,
            ownership_digest=record.plan.ownership_digest,
            runtime_run_id=record.plan.runtime_run_id,
            attempt_id=record.plan.attempt_id,
            worker_id=record.plan.worker_id,
            mutation_operation_id=record.operation_id,
            mutation_plan_hash=record.plan.plan_hash,
            original_mutation_approval_id=record.original_approval_id,
            mutation_checkpoint_id=record.checkpoint_id,
            mutation_integrity_digest=intent_digest(record),
            final_observation_digest=record.final_observation_digest,
            proof_policy_digest=PROOF_POLICY_DIGEST,
        )
        return AppliedSourceEvidence(**payload, proof_hash=digest(payload))

    def bind(self, actor, workspace_id, request):
        try:
            request = PrepareNativeCommitRequest.model_validate(request.model_dump(mode="json"))
            evidence = self.source(actor, workspace_id, request.source_edit_record_id)
            # This performs its own approved, pinned measurement and checkpointed
            # preparation. It never invokes the source mutation apply operation.
            prepared = self.preparation.prepare(actor, workspace_id, request)

            def finalize(plan, filesystem_guard):
                if plan != prepared.plan:
                    refuse("GIT_COMMIT_APPLIED_SOURCE_CHANGED")
                current = self.source(actor, workspace_id, request.source_edit_record_id)
                if current != evidence:
                    refuse("GIT_COMMIT_APPLIED_SOURCE_CHANGED")
                result = AppliedCommitPreparation(preparation=prepared, source_evidence=current)
                # Recheck source receipt and native preparation before returning
                # a current-byte assertion while all filesystem pins remain held.
                if self.preparation.read(actor, workspace_id, request.command_id) != prepared:
                    refuse("GIT_COMMIT_APPLIED_SOURCE_CHANGED")
                filesystem_guard()
                return result

            return self.preparation.measured(
                actor, workspace_id, request, finalize, request=request
            )
        except SQLAlchemyError:
            refuse("GIT_COMMIT_STORAGE_FAILED")
        except (ValueError, TypeError, AttributeError):
            refuse("GIT_COMMIT_APPLIED_SOURCE_INVALID")
