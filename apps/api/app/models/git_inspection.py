"""Bounded native repository observation contracts; no source-edit authority."""

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from app.models.self_build import Commit, Contract, Digest, Identifier


class RepositoryInspectionPlan(Contract):
    schema_version: Literal["1.0"] = "1.0"
    operation: Literal["git.repository.inspect"] = "git.repository.inspect"
    workspace_id: Identifier
    workspace_version: int = Field(ge=1)
    workspace_plan_hash: Digest
    repository_identity: str
    base_sha: Commit
    tool_identity_digest: Digest
    tool_sha256: Digest
    inspection_policy_digest: Digest
    plan_hash: Digest


class ApproveRepositoryInspection(Contract):
    workspace_id: Identifier
    expected_plan_hash: Digest
    valid_for_seconds: int = Field(default=900, ge=1, le=3600)


class RepositoryInspectionApproval(Contract):
    approval_id: Identifier
    plan: RepositoryInspectionPlan
    approved_by: Identifier
    expires_at: datetime


class InspectRepositoryRequest(Contract):
    expected_plan_hash: Digest
    approval_id: Identifier
    worker_id: Identifier
    lease_token: str = Field(min_length=1, max_length=200, repr=False)


class RepositoryObservation(Contract):
    repository_identity: str
    base_sha: Commit
    base_tree_sha: Commit
    primary_head_sha: Commit
    origin_tracking_sha: Commit
    origin_evidence_kind: Literal["local_tracking_ref"] = "local_tracking_ref"
    base_matches_origin_tracking_ref: bool
    file_count: int = Field(ge=0, le=4096)
    inventory_digest: Digest
    tool_digest: Digest
    # This observer measures committed metadata, not current working-copy cleanliness.
    checkout_state: Literal["unobserved"] = "unobserved"


class RepositoryInspection(Contract):
    schema_version: Literal["1.0"] = "1.0"
    inspection_id: Identifier
    workspace_id: Identifier
    plan: RepositoryInspectionPlan
    approval_id: Identifier
    worker_id: Identifier
    started_at: datetime
    finished_at: datetime
    observation: RepositoryObservation

    @model_validator(mode="after")
    def coherent_observation(self):
        if (
            self.plan.workspace_id != self.workspace_id
            or self.observation.repository_identity != self.plan.repository_identity
            or self.observation.base_sha != self.plan.base_sha
            or self.observation.tool_digest != self.plan.tool_sha256
        ):
            raise ValueError("inspection lineage is inconsistent")
        if (
            self.started_at.tzinfo is None
            or self.finished_at.tzinfo is None
            or self.started_at > self.finished_at
        ):
            raise ValueError("inspection timestamps must be ordered and timezone aware")
        if self.observation.base_matches_origin_tracking_ref != (
            self.observation.base_sha == self.observation.origin_tracking_sha
        ):
            raise ValueError("base currency must match measured references")
        return self
