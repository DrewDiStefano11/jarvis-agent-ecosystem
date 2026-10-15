"""Native applied-source receipt tied to an exact prepared commit; no Git effects."""

from typing import Literal

from pydantic import model_validator

from app.models.git_commit_authorization import PreparedCommitRecord
from app.models.self_build import Contract, Digest, Identifier
from app.self_build.policy import digest


class AppliedSourceEvidence(Contract):
    schema_version: Literal["1.0"] = "1.0"
    workspace_id: Identifier
    editing_record_id: Digest
    editing_plan_hash: Digest
    creation_operation_id: Identifier
    creation_plan_hash: Digest
    ownership_digest: Digest
    runtime_run_id: Identifier
    attempt_id: Identifier
    worker_id: Identifier
    mutation_operation_id: Identifier
    mutation_plan_hash: Digest
    original_mutation_approval_id: Identifier
    mutation_checkpoint_id: Identifier
    mutation_integrity_digest: Digest
    final_observation_digest: Digest
    proof_policy_digest: Digest
    proof_hash: Digest

    @model_validator(mode="after")
    def exact(self):
        if self.proof_hash != digest(self.model_dump(mode="json", exclude={"proof_hash"})):
            raise ValueError("applied-source proof must retain its exact original provenance")
        return self


class AppliedCommitPreparation(Contract):
    preparation: PreparedCommitRecord
    source_evidence: AppliedSourceEvidence
    current_source_measured: Literal[True] = True
    git_commit_created: Literal[False] = False

    @model_validator(mode="after")
    def exact(self):
        record, evidence = self.preparation, self.source_evidence
        proposal = record.plan.proposal
        scope = proposal.lineage
        if (
            record.state != "checkpointed"
            or record.checkpoint_id is None
            or evidence.workspace_id != record.workspace_id
            or evidence.editing_record_id != record.plan.source_edit_record_id
            or evidence.editing_plan_hash != record.plan.source_edit_record_plan_hash
            or evidence.creation_operation_id != proposal.creation_operation_id
            or evidence.creation_plan_hash != proposal.creation_plan_hash
            or evidence.ownership_digest != proposal.ownership_digest
            or evidence.runtime_run_id != scope.runtime_run_id
            or evidence.attempt_id != scope.attempt_id
            or evidence.worker_id != scope.worker_id
        ):
            raise ValueError(
                "applied source and prepared commit require the same exact native lineage"
            )
        return self
