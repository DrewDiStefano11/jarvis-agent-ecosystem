"""Exact native editing approval and protected recovery contracts."""

from datetime import datetime
from typing import Literal

from pydantic import Field, model_serializer, model_validator

from app.models.development_validation import Contract, Digest
from app.models.self_build import Identifier


class MutationPlan(Contract):
    schema_version: Literal["1.0"] = "1.0"
    editing_record_id: Digest
    editing_plan_hash: Digest
    workspace_id: Identifier
    task_id: Identifier
    creation_operation_id: Identifier
    creation_plan_hash: Digest
    ownership_digest: Digest
    runtime_run_id: Identifier
    attempt_id: Identifier
    worker_id: Identifier
    mutation_policy_digest: Digest
    plan_hash: Digest


class ApproveSourceMutation(Contract):
    editing_record_id: Digest
    expected_plan_hash: Digest
    valid_for_seconds: int = Field(default=900, ge=1, le=3600, strict=True)


class MutationApproval(Contract):
    approval_id: Identifier
    plan: MutationPlan
    approved_by: Identifier
    expires_at: datetime


class ApplySourceMutation(Contract):
    editing_record_id: Digest
    expected_plan_hash: Digest
    approval_id: Identifier
    worker_id: Identifier
    lease_token: str = Field(min_length=1, max_length=200, repr=False)


class OriginalIdentity(Contract):
    change_index: int = Field(ge=0, le=63, strict=True)
    device: int = Field(ge=0, strict=True)
    inode: int = Field(ge=1, strict=True)


class MutationRecord(Contract):
    operation_id: Identifier
    plan: MutationPlan
    original_approval_id: Identifier
    state: Literal["prepared", "editing", "finalizing", "complete"]
    completed_count: int = Field(ge=0, le=64, strict=True)
    pending_index: int | None = Field(default=None, ge=0, le=63, strict=True)
    staging_device: int | None = Field(default=None, ge=0, strict=True)
    staging_inode: int | None = Field(default=None, ge=0, strict=True)
    original_identities: tuple[OriginalIdentity, ...] = Field(default=(), max_length=64)
    prefix_digest: Digest | None = None
    final_observation_digest: Digest | None = None
    checkpoint_id: Identifier | None = None
    created_at: datetime
    updated_at: datetime

    @model_serializer(mode="wrap")
    def legacy_shape(self, handler):
        payload = handler(self)
        if not self.original_identities:
            # Preserve original serialized records and their checkpoint hashes.
            payload.pop("original_identities", None)
            payload.pop("originalIdentities", None)
        return payload

    @model_validator(mode="after")
    def position(self):
        indices = [item.change_index for item in self.original_identities]
        if indices != sorted(set(indices)) or any(
            index >= self.completed_count and index != self.pending_index for index in indices
        ):
            raise ValueError("SOURCE_MUTATION_ORIGINAL_IDENTITY_INVALID")
        if (self.staging_device is None) != (self.staging_inode is None) or (
            self.staging_inode is not None and self.pending_index is None
        ):
            raise ValueError("SOURCE_MUTATION_STAGING_INVALID")
        if (
            self.created_at.tzinfo is None
            or self.updated_at.tzinfo is None
            or self.created_at > self.updated_at
        ):
            raise ValueError("SOURCE_MUTATION_TIME_INVALID")
        if self.pending_index is not None and (
            self.state != "editing" or self.pending_index != self.completed_count
        ):
            raise ValueError("SOURCE_MUTATION_POSITION_INVALID")
        if self.state == "prepared" and (
            self.completed_count
            or self.prefix_digest
            or self.final_observation_digest
            or self.pending_index is not None
        ):
            raise ValueError("SOURCE_MUTATION_POSITION_INVALID")
        if bool(self.completed_count) != bool(self.prefix_digest):
            raise ValueError("SOURCE_MUTATION_POSITION_INVALID")
        if (self.state in {"finalizing", "complete"}) != bool(self.final_observation_digest):
            raise ValueError("SOURCE_MUTATION_POSITION_INVALID")
        if self.state == "complete" and self.checkpoint_id is None:
            raise ValueError("SOURCE_MUTATION_POSITION_INVALID")
        return self
