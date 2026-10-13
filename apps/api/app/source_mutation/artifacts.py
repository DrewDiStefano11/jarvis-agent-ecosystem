"""Pure expected artifact derivation and protected historical metadata reads."""

from hashlib import sha256
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from app.core.errors import DomainError
from app.models import source_artifacts as contracts
from app.models.source_artifacts import ArtifactExpectation, ExpectedArtifactManifest
from app.models.source_edit_planning import EditingPlan
from app.models.source_mutation import MutationRecord
from app.self_build.policy import digest
from app.source_change.planner import verify_source_plan
from app.source_edit_planning.projection import project_source_state
from app.source_edit_planning.service import EDITING_POLICY_DIGEST
from app.source_mutation.driver import allowed_changes, expected_observation, refuse, staging_names
from app.source_mutation.history import MutationHistoryReader
from app.source_mutation.service import MUTATION_POLICY_DIGEST, intent_digest

MAX_MANIFEST_BYTES = 524288
ARTIFACT_POLICY_DIGEST = sha256(
    (
        Path(__file__).read_text(encoding="utf-8")
        + Path(contracts.__file__).read_text(encoding="utf-8")
        + MUTATION_POLICY_DIGEST
        + EDITING_POLICY_DIGEST
    ).encode()
).hexdigest()


def plan_expected_artifacts(record, editing_plan):
    """Validate immutable inputs; never establish native evidence or authority."""
    try:
        record = MutationRecord.model_validate(record.model_dump(mode="json", warnings=False))
        editing_plan = EditingPlan.model_validate(
            editing_plan.model_dump(mode="json", warnings=False)
        )
        if record.state != "complete":
            refuse("SOURCE_ARTIFACTS_INCOMPLETE")
        plan = record.plan
        if (
            digest(plan.model_dump(mode="json", exclude={"plan_hash"})) != plan.plan_hash
            or plan.mutation_policy_digest != MUTATION_POLICY_DIGEST
            or editing_plan.editing_policy_digest != EDITING_POLICY_DIGEST
            or digest(editing_plan.model_dump(mode="json", exclude={"plan_hash"}))
            != editing_plan.plan_hash
            or plan.editing_plan_hash != editing_plan.plan_hash
            or plan.workspace_id != editing_plan.workspace_id
            or plan.task_id != editing_plan.task_id
            or plan.runtime_run_id != editing_plan.runtime_run_id
            or plan.attempt_id != editing_plan.runtime_attempt_id
            or editing_plan.source_plan.references.original_plan_hash
            != editing_plan.workspace_plan_hash
        ):
            refuse("SOURCE_ARTIFACTS_LINEAGE_INVALID")
        source = editing_plan.source_plan
        verify_source_plan(source, editing_plan.observation)
        allowed_changes(source)
        state, validation = project_source_state(source, editing_plan.original_code_state)
        if state != editing_plan.expected_code_state or validation != editing_plan.validation_plan:
            refuse("SOURCE_ARTIFACTS_LINEAGE_INVALID")
        total = len(source.changes)
        checkpoint_id = f"checkpoint-{record.operation_id}-complete-{total}-None"
        if record.original_identities:
            identity_digest = digest(
                [item.model_dump(mode="json") for item in record.original_identities]
            )
            checkpoint_id += "-originals-" + identity_digest[:16]
        if (
            record.completed_count != total
            or record.pending_index is not None
            or record.staging_inode is not None
            or record.prefix_digest
            != digest([item.model_dump(mode="json") for item in source.preview])
            or record.final_observation_digest
            != digest(
                expected_observation(editing_plan.observation, source.changes, total).model_dump(
                    mode="json"
                )
            )
            or record.checkpoint_id != checkpoint_id
        ):
            refuse("SOURCE_ARTIFACTS_POSITION_INVALID")
        artifacts = []
        for index, change in enumerate(source.changes):
            parent = change.path.rpartition("/")[0]
            before, after = staging_names(record.operation_id, index)
            for name, identity in ((before, change.before), (after, None)):
                artifacts.append(
                    ArtifactExpectation(
                        change_index=index,
                        source_path=change.path,
                        path=parent + "/" + name if parent else name,
                        presence="retained_original" if identity else "absent",
                        expected_identity=identity,
                    )
                )
        payload = dict(
            schema_version="1.0",
            policy_digest=ARTIFACT_POLICY_DIGEST,
            operation_id=record.operation_id,
            mutation_plan_hash=plan.plan_hash,
            mutation_intent_digest=intent_digest(record),
            editing_record_id=plan.editing_record_id,
            editing_plan_hash=plan.editing_plan_hash,
            original_approval_id=record.original_approval_id,
            checkpoint_id=record.checkpoint_id,
            artifacts=[item.model_dump(mode="json") for item in artifacts],
            retained_total_bytes=sum(
                item.expected_identity.byte_count for item in artifacts if item.expected_identity
            ),
            native_artifacts_verified=False,
            execution_authorized=False,
            deletion_authorized=False,
            checkout_root_lock_allowed=False,
        )
        result = ExpectedArtifactManifest(**payload, manifest_hash=digest(payload))
        if len(result.model_dump_json().encode("utf-8")) > MAX_MANIFEST_BYTES:
            refuse("SOURCE_ARTIFACTS_METADATA_LIMIT")
        return result
    except (AttributeError, TypeError, ValueError):
        refuse("SOURCE_ARTIFACTS_INPUT_INVALID")


class ArtifactManifestReader:
    def __init__(self, app):
        self.history = MutationHistoryReader(app)

    def read(self, actor, editing_record_id):
        try:
            history = self.history.read(actor, editing_record_id)
            if history.record.state != "complete":
                refuse("SOURCE_ARTIFACTS_INCOMPLETE")
            if not history.native_checkpoint_present or not history.journal_checkpoint_acknowledged:
                refuse("SOURCE_ARTIFACTS_POSITION_INVALID")
            proposal = self.history.service.planning.read(actor, editing_record_id)
            if proposal.record_id != history.record.plan.editing_record_id:
                refuse("SOURCE_ARTIFACTS_LINEAGE_INVALID")
            return plan_expected_artifacts(history.record, proposal.plan)
        except SQLAlchemyError:
            refuse("SOURCE_ARTIFACTS_STORAGE_FAILED")
        except DomainError as error:
            if error.code in {"SOURCE_MUTATION_STORAGE_FAILED", "SOURCE_EDIT_STORAGE_FAILED"}:
                refuse("SOURCE_ARTIFACTS_STORAGE_FAILED")
            raise


def verify_expected_artifacts(manifest, record, editing_plan):
    try:
        manifest = ExpectedArtifactManifest.model_validate(
            manifest.model_dump(mode="json", warnings=False)
        )
    except (AttributeError, TypeError, ValueError):
        refuse("SOURCE_ARTIFACTS_INPUT_INVALID")
    expected = plan_expected_artifacts(record, editing_plan)
    if manifest != expected:
        refuse("SOURCE_ARTIFACTS_MANIFEST_CHANGED")
    return expected
