"""Separate native approval and checkpointed preparation; no Git commit effects."""

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.models.git_commit import CommitMetadata, CommitPreparationPlan
from app.models.self_build import Contract, Digest, Identifier
from app.models.source_change import source_path
from app.self_build.policy import digest


class CommitSelection(Contract):
    source_edit_record_id: Digest
    selection: tuple[str, ...] = Field(min_length=1, max_length=64)
    metadata: CommitMetadata

    @field_validator("selection")
    @classmethod
    def exact_selection(cls, values):
        for value in values:
            source_path(value)
        if len(set(map(str.casefold, values))) != len(values):
            raise ValueError("unique exact source paths required")
        return tuple(sorted(values))


class CommitInspectionPlan(Contract):
    schema_version: Literal["1.0"] = "1.0"
    operation: Literal["git.commit.inspect"] = "git.commit.inspect"
    workspace_id: Identifier
    creation_operation_id: Identifier
    creation_plan_hash: Digest
    creation_checkpoint_id: Identifier
    source_edit_record_id: Digest
    source_edit_record_plan_hash: Digest
    actor_id: Identifier
    runtime_run_id: Identifier
    attempt_id: Identifier
    worker_id: Identifier
    tool_identity_digest: Digest
    selection: tuple[str, ...] = Field(min_length=1, max_length=64)
    metadata: CommitMetadata
    authorization_policy_digest: Digest
    plan_hash: Digest

    @model_validator(mode="after")
    def coherent(self):
        if self.plan_hash != digest(self.model_dump(mode="json", exclude={"plan_hash"})):
            raise ValueError("inspection requires its exact bounded plan hash")
        return self


class ApproveCommitInspection(CommitSelection):
    workspace_id: Identifier
    expected_plan_hash: Digest
    valid_for_seconds: int = Field(default=900, ge=1, le=3600, strict=True)


class CommitInspectionApproval(Contract):
    approval_id: Identifier
    approved_by: Identifier
    expires_at: datetime
    plan: CommitInspectionPlan


class PreviewCommitPreparation(CommitSelection):
    inspection_approval_id: Identifier
    expected_inspection_plan_hash: Digest


class CommitAuthorizationPlan(Contract):
    schema_version: Literal["1.0"] = "1.0"
    operation: Literal["git.commit.prepare.authorize"] = "git.commit.prepare.authorize"
    workspace_id: Identifier
    source_edit_record_id: Digest
    source_edit_record_plan_hash: Digest
    inspection_plan: CommitInspectionPlan
    proposal: CommitPreparationPlan
    authorization_policy_digest: Digest
    plan_hash: Digest

    @model_validator(mode="after")
    def coherent(self):
        scope, lineage = self.inspection_plan, self.proposal.lineage
        if (
            self.workspace_id != self.proposal.workspace_id
            or self.inspection_plan.workspace_id != self.workspace_id
            or self.inspection_plan.source_edit_record_id != self.source_edit_record_id
            or self.inspection_plan.source_edit_record_plan_hash
            != self.source_edit_record_plan_hash
            or self.source_edit_record_id != self.proposal.lineage.source_edit_record_id
            or scope.actor_id != lineage.actor_id
            or scope.runtime_run_id != lineage.runtime_run_id
            or scope.attempt_id != lineage.attempt_id
            or scope.worker_id != lineage.worker_id
            or scope.creation_operation_id != self.proposal.creation_operation_id
            or scope.creation_plan_hash != self.proposal.creation_plan_hash
            or scope.metadata != self.proposal.metadata
            or scope.selection != tuple(file.path for file in self.proposal.files)
            or self.plan_hash != digest(self.model_dump(mode="json", exclude={"plan_hash"}))
        ):
            raise ValueError("authorization plan requires exact immutable source lineage")
        return self


class ApproveNativeCommitPreparation(PreviewCommitPreparation):
    workspace_id: Identifier
    expected_plan_hash: Digest
    valid_for_seconds: int = Field(default=900, ge=1, le=3600, strict=True)


class CommitPreparationApproval(Contract):
    approval_id: Identifier
    approved_by: Identifier
    expires_at: datetime
    plan: CommitAuthorizationPlan


class PrepareNativeCommitRequest(PreviewCommitPreparation):
    command_id: Identifier
    expected_plan_hash: Digest
    approval_id: Identifier
    worker_id: Identifier
    lease_token: str = Field(min_length=1, max_length=200, repr=False)


class PreparedCommitRecord(Contract):
    schema_version: Literal["1.0"] = "1.0"
    operation_id: Identifier
    command_id: Identifier
    workspace_id: Identifier
    plan: CommitAuthorizationPlan
    approval_id: Identifier
    inspection_approval_id: Identifier
    state: Literal["prepared", "checkpointed"]
    checkpoint_id: Identifier | None
    created_at: datetime

    @model_validator(mode="after")
    def coherent(self):
        if (
            self.plan.workspace_id != self.workspace_id
            or (self.state == "checkpointed") != (self.checkpoint_id is not None)
            or self.created_at.tzinfo is None
        ):
            raise ValueError(
                "prepared record requires immutable lineage and measured checkpoint state"
            )
        return self
