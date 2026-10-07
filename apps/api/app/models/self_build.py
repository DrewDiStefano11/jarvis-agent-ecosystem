"""Development workspace contracts. A reservation is never execution authority."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Commit = Annotated[str, Field(pattern=r"^[a-f0-9]{40}$")]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class WorkspaceIntent(Contract):
    repository_id: Identifier
    runtime_run_id: Identifier
    base_sha: Commit


class WorkspacePlan(Contract):
    schema_version: Literal["1.0"] = "1.0"
    repository_id: Identifier
    repository_identity: str
    policy_digest: Digest
    task_id: str
    runtime_run_id: Identifier
    base_branch: str
    base_sha: Commit
    branch: str
    worktree_key: str
    plan_hash: Digest


class ReserveWorkspaceRequest(WorkspaceIntent):
    expected_plan_hash: Digest
    approval_id: Identifier
    worker_id: Identifier
    lease_token: str = Field(min_length=1, max_length=200, repr=False)


class WorkspaceReservation(Contract):
    schema_version: Literal["1.0"] = "1.0"
    workspace_id: str
    plan: WorkspacePlan
    state: Literal["reserved", "abandoned"]
    recovery_required: bool
    recovery_reason: Literal["lease_lost", "runtime_inactive", "policy_changed"] | None = None
    approval_id: str
    created_by: str
    worker_id: str
    created_at: datetime
    updated_at: datetime
    version: int = Field(ge=1)
    # Unknown until a production Git observer supplies evidence.
    checkout_state: Literal["unobserved"] = "unobserved"


class AbandonWorkspaceRequest(Contract):
    expected_version: int = Field(ge=1)
    expected_plan_hash: Digest
    approval_id: Identifier
    worker_id: Identifier
    lease_token: str = Field(min_length=1, max_length=200, repr=False)


class WorkspaceAbandonPlan(Contract):
    schema_version: Literal["1.0"] = "1.0"
    operation: Literal["workspace.abandon"] = "workspace.abandon"
    workspace_id: Identifier
    workspace_version: int = Field(ge=1)
    workspace: WorkspacePlan
    worker_id: Identifier
    # Tombstoning preserves files and task/runtime ownership.
    removes_files: Literal[False] = False
    releases_lease: Literal[False] = False
    plan_hash: Digest


class ApproveWorkspaceAbandonRequest(Contract):
    workspace_id: Identifier
    expected_plan_hash: Digest
    valid_for_seconds: int = Field(default=900, ge=1, le=3600)


class WorkspaceAbandonApproval(Contract):
    approval_id: Identifier
    plan: WorkspaceAbandonPlan
    approved_by: Identifier
    expires_at: datetime


class ApproveWorkspaceRequest(WorkspaceIntent):
    expected_plan_hash: Digest
    valid_for_seconds: int = Field(default=900, ge=1, le=3600)


class WorkspaceApproval(Contract):
    approval_id: Identifier
    plan: WorkspacePlan
    approved_by: Identifier
    expires_at: datetime
