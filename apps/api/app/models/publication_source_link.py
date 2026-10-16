"""Protected historical commit/source/observation reference for Campaign A."""

from typing import Literal

from pydantic import Field, model_validator

from app.development_validation.planner import digest as code_digest
from app.models.development_validation import CodeState
from app.models.github_publication import PullRequestObservation
from app.models.self_build import Commit, Contract, Digest, Identifier
from app.self_build.policy import digest


class PublicationSourceLink(Contract):
    schema_version: Literal["1.0"] = "1.0"
    workspace_id: Identifier
    task_id: Identifier
    runtime_run_id: Identifier
    attempt_id: Identifier
    publication_operation_id: Identifier
    publication_digest: Digest
    publication_checkpoint_id: Identifier
    native_operation_id: Identifier
    native_command_id: Identifier
    native_plan_hash: Digest
    native_checkpoint_id: Identifier
    native_checkpoint_integrity: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    source_snapshot_digest: Digest
    applied_source_proof_hash: Digest
    mutation_operation_id: Identifier
    mutation_checkpoint_id: Identifier
    source_edit_record_id: Digest
    source_edit_plan_hash: Digest
    parent_sha: Commit
    commit_sha: Commit
    tree_sha: Commit
    original_code_state: CodeState
    original_code_state_hash: Digest
    observation: PullRequestObservation
    observation_digest: Digest
    historical_only: Literal[True] = True
    mutation_authority: Literal[False] = False
    validation_authority: Literal[False] = False
    link_hash: Digest

    @model_validator(mode="after")
    def exact(self):
        if (
            self.original_code_state.head_sha != self.parent_sha
            or self.observation.head_sha != self.commit_sha
            or self.observation.head_sha_after_collection != self.commit_sha
            or self.original_code_state_hash
            != code_digest(self.original_code_state.model_dump(mode="json"))
            or self.observation_digest != digest(self.observation.model_dump(mode="json"))
            or self.link_hash != digest(self.model_dump(mode="json", exclude={"link_hash"}))
            or not self.native_checkpoint_integrity.startswith("sha256:")
            or len(self.native_checkpoint_integrity) != 71
        ):
            raise ValueError("historical source link requires exact protected identities")
        return self
