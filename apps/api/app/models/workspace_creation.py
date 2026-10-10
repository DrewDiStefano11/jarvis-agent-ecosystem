"""Exact native workspace creation contracts. Plans/markers never grant authority."""

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from app.models.self_build import Commit, Contract, Digest, Identifier, WorkspacePlan


class WorkspaceCreationPlan(Contract):
    schema_version: Literal["1.0"] = "1.0"
    operation: Literal["workspace.materialize"] = "workspace.materialize"
    workspace_id: Identifier
    workspace_version: int = Field(ge=1)
    workspace: WorkspacePlan
    inspection_id: Identifier
    base_tree_sha: Commit
    inventory_digest: Digest
    file_count: int = Field(ge=0, le=4096)
    tool_identity_digest: Digest
    tool_sha256: Digest
    creation_policy_digest: Digest
    mutation_platform: Literal["windows"] = "windows"
    maximum_file_bytes: Literal[8388608] = 8388608
    maximum_total_bytes: Literal[67108864] = 67108864
    # Initial creation registers reading only. Source writes need separate approval.
    allowed_tools: tuple[Literal["workspace.list"], Literal["workspace.read"]] = (
        "workspace.list",
        "workspace.read",
    )
    read_prefixes: tuple[Literal["."]] = (".",)
    write_prefixes: tuple[()] = ()
    plan_hash: Digest

    @model_validator(mode="after")
    def generated_identity(self):
        if (
            self.workspace_id != self.workspace.worktree_key
            or not self.workspace_id.startswith("jarvis-")
            or len(self.workspace_id) != 39
            or any(char not in "0123456789abcdef" for char in self.workspace_id[7:])
            or self.workspace.branch != "codex/" + self.workspace_id
        ):
            raise ValueError("creation must use the reserved generated namespace and branch")
        return self


class PreviewWorkspaceCreation(Contract):
    inspection_id: Identifier


class ApproveWorkspaceCreation(PreviewWorkspaceCreation):
    workspace_id: Identifier
    expected_plan_hash: Digest
    valid_for_seconds: int = Field(default=900, ge=1, le=3600)


class WorkspaceCreationApproval(Contract):
    approval_id: Identifier
    plan: WorkspaceCreationPlan
    approved_by: Identifier
    expires_at: datetime


class CreateWorkspaceRequest(PreviewWorkspaceCreation):
    expected_plan_hash: Digest
    approval_id: Identifier
    worker_id: Identifier
    lease_token: str = Field(min_length=1, max_length=200, repr=False)


class WorkspaceCreationRecord(Contract):
    operation_id: Identifier
    workspace_id: Identifier
    plan: WorkspaceCreationPlan
    approval_id: Identifier
    worker_id: Identifier
    attempt_id: Identifier
    state: Literal["prepared", "git_created", "materializing", "finalizing", "ready", "interrupted"]
    checkpoint_id: Identifier | None
    ownership_digest: Digest
    registration_digest: Digest | None = None
    source_digest: Digest | None = None
    completed_file_count: int = Field(ge=0, le=4096)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def measured_lineage(self):
        if self.workspace_id != self.plan.workspace_id:
            raise ValueError("creation record must retain its original workspace")
        if self.completed_file_count > self.plan.file_count:
            raise ValueError("creation acknowledgement exceeds approved inventory")
        if self.state == "prepared" and (
            self.completed_file_count != 0
            or self.registration_digest is not None
            or self.source_digest is not None
        ):
            raise ValueError("preparation cannot acknowledge filesystem effects")
        if self.state in {"git_created", "materializing", "finalizing", "ready"} and (
            self.registration_digest is None
        ):
            raise ValueError("native phases require measured Git registration")
        if self.state == "git_created" and (
            self.completed_file_count != 0 or self.source_digest is not None
        ):
            raise ValueError("registration alone cannot acknowledge source content")
        if self.state == "materializing" and (
            self.completed_file_count == 0 or self.source_digest is None
        ):
            raise ValueError("materialization requires measured nonempty source prefix")
        if self.state in {"finalizing", "ready"} and (
            self.completed_file_count != self.plan.file_count or self.source_digest is None
        ):
            raise ValueError("finalization requires complete measured source evidence")
        if self.state == "ready" and (
            self.checkpoint_id is None or self.completed_file_count != self.plan.file_count
        ):
            raise ValueError("ready requires an acknowledged complete inventory and checkpoint")
        if (
            self.created_at.tzinfo is None
            or self.updated_at.tzinfo is None
            or self.created_at > self.updated_at
        ):
            raise ValueError("creation timestamps must be ordered and timezone aware")
        return self
