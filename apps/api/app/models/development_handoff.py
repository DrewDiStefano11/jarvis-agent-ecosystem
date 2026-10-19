"""Fresh native source handoff. Validation results and publication authority are separate."""

from typing import Literal

from pydantic import model_validator

from app.development_validation.planner import digest
from app.models.development_dispatch import DevelopmentIteration
from app.models.development_validation import Contract, ValidationPlan
from app.models.source_artifact_verification import NativeArtifactVerification
from app.models.source_mutation_history import MutationHistory


class DevelopmentHandoff(Contract):
    schema_version: Literal["1.0"] = "1.0"
    iteration: DevelopmentIteration
    source_history: MutationHistory
    native_source: NativeArtifactVerification
    requested_validation: ValidationPlan
    validation_required: Literal[True] = True
    validation_passed: Literal[False] = False
    publication_ready: Literal[False] = False
    execution_authorized: Literal[False] = False

    @model_validator(mode="after")
    def exact_lineage(self):
        iteration = self.iteration
        mutation = self.source_history.record
        plan = mutation.plan
        measured = self.native_source.source_inspection
        if (
            iteration.state != "mutation_recorded"
            or plan.task_id != iteration.task_id
            or plan.runtime_run_id != iteration.runtime_run_id
            or plan.attempt_id != iteration.attempt_id
            or plan.workspace_id != iteration.workspace_id
            or plan.editing_record_id != iteration.editing_record_id
            or plan.editing_plan_hash != iteration.editing_plan_hash
            or mutation.state != "complete"
            or not self.source_history.native_checkpoint_present
            or not self.source_history.journal_checkpoint_acknowledged
            or measured.operation_id != mutation.operation_id
            or measured.mutation_plan_hash != plan.plan_hash
            or measured.checkpoint_id != mutation.checkpoint_id
            or self.requested_validation.code_state_hash
            != digest(measured.code_state.model_dump(mode="json"))
            or self.requested_validation.plan_hash
            != digest(self.requested_validation.model_dump(mode="json", exclude={"plan_hash"}))
            or not measured.matches_completed_mutation
        ):
            raise ValueError("DEVELOPMENT_HANDOFF_LINEAGE_INVALID")
        return self
