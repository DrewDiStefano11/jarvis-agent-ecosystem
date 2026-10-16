"""Exact native commit intent; persisted operator approval is resolved by services."""

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from app.models.git_applied_source import AppliedCommitPreparation, AppliedSourceEvidence
from app.models.git_commit_authorization import PreparedCommitRecord
from app.models.self_build import Contract, Digest, Identifier
from app.self_build.policy import digest

CommitPhase = Literal[
    "prepared",
    "objects_verified",
    "index_prepared",
    "indexed",
    "reference_prepared",
    "committed",
    "checkpointed",
]

CommitProgressAction = Literal[
    "object_intent",
    "object_stage",
    "object_directory",
    "object_store_stage",
    "object_verified",
    "objects_verified",
    "index_intent",
    "index_stage",
    "index_verified",
    "reference_intent",
    "reference_verified",
]


class NativeCommitProgress(Contract):
    operation_id: Identifier
    ordinal: int = Field(ge=1, le=49160, strict=True)
    action: CommitProgressAction
    device: int | None = Field(default=None, ge=0, le=2**128 - 1, strict=True)
    inode: int | None = Field(default=None, ge=0, le=2**128 - 1, strict=True)
    previous_digest: Digest
    next_digest: Digest

    @model_validator(mode="after")
    def exact(self):
        claimed = self.action in {
            "object_stage",
            "object_directory",
            "object_store_stage",
            "index_stage",
        }
        if claimed != (self.device is not None and self.inode is not None):
            raise ValueError("only a stage claim carries an exact observed inode")
        if not claimed and (self.device is not None or self.inode is not None):
            raise ValueError("a non-claim action cannot replace staging ownership")
        return self


class NativeCommitPlan(Contract):
    schema_version: Literal["1.0"] = "1.0"
    operation: Literal["git.commit.create"] = "git.commit.create"
    preparation: PreparedCommitRecord
    applied_source: AppliedSourceEvidence
    source_artifact_manifest_hash: Digest
    object_program_fingerprint: Digest
    object_count: int = Field(ge=2, le=8192, strict=True)
    object_payload_bytes: int = Field(ge=1, le=8388608, strict=True)
    index_payload_sha256: Digest
    index_payload_bytes: int = Field(ge=32, le=8388608, strict=True)
    original_index_sha256: Digest
    original_index_device: int = Field(ge=0, strict=True)
    original_index_inode: int = Field(ge=0, strict=True)
    compiler_policy_digest: Digest
    execution_policy_digest: Digest
    snapshot_digest: Digest
    git_metadata_snapshot_digest: Digest
    maximum_output_bytes: Literal[262144] = 262144
    plan_hash: Digest

    @model_validator(mode="after")
    def exact(self):
        AppliedCommitPreparation(preparation=self.preparation, source_evidence=self.applied_source)
        if self.plan_hash != digest(self.model_dump(mode="json", exclude={"plan_hash"})):
            raise ValueError("native commit intent requires its exact bounded plan hash")
        if len(self.model_dump_json().encode()) > self.maximum_output_bytes:
            raise ValueError("native commit intent exceeds its output bound")
        return self


class PreviewNativeCommit(Contract):
    preparation_command_id: Identifier
    inspection_approval_id: Identifier
    expected_inspection_plan_hash: Digest


class ApproveNativeCommitPlan(PreviewNativeCommit):
    workspace_id: Identifier
    expected_plan_hash: Digest
    valid_for_seconds: int = Field(default=900, ge=1, le=3600, strict=True)


class NativeCommitApproval(Contract):
    approval_id: Identifier
    approved_by: Identifier
    expires_at: datetime
    plan: NativeCommitPlan


class RenewNativeCommitApproval(Contract):
    workspace_id: Identifier
    command_id: Identifier
    expected_plan_hash: Digest
    expected_recovery_hash: Digest
    valid_for_seconds: int = Field(default=900, ge=1, le=3600, strict=True)


class ExecuteNativeCommit(PreviewNativeCommit):
    command_id: Identifier
    expected_plan_hash: Digest
    approval_id: Identifier
    worker_id: Identifier
    lease_token: str = Field(min_length=1, max_length=200, repr=False)


class NativeCommitRecord(Contract):
    schema_version: Literal["1.0"] = "1.0"
    operation_id: Identifier = Field(pattern=r"^commit-exec-[a-f0-9]{32}$")
    command_id: Identifier
    workspace_id: Identifier
    plan: NativeCommitPlan
    original_approval_id: Identifier
    state: CommitPhase
    completed_object_count: int = Field(default=0, ge=0, le=8192, strict=True)
    pending_object_index: int | None = Field(default=None, ge=0, le=8191, strict=True)
    staging_device: int | None = Field(default=None, ge=0, strict=True)
    staging_inode: int | None = Field(default=None, ge=0, strict=True)
    object_directory_device: int | None = Field(default=None, ge=0, strict=True)
    object_directory_inode: int | None = Field(default=None, ge=0, strict=True)
    object_store_stage_device: int | None = Field(default=None, ge=0, strict=True)
    object_store_stage_inode: int | None = Field(default=None, ge=0, strict=True)
    index_stage_device: int | None = Field(default=None, ge=0, strict=True)
    index_stage_inode: int | None = Field(default=None, ge=0, strict=True)
    intent_checkpoint_id: Identifier | None = None
    checkpoint_id: Identifier | None
    created_at: datetime

    @model_validator(mode="after")
    def exact(self):
        if (
            self.completed_object_count > self.plan.object_count
            or (
                self.pending_object_index is not None
                and (
                    self.pending_object_index != self.completed_object_count
                    or self.pending_object_index >= self.plan.object_count
                )
            )
            or (self.staging_device is None) != (self.staging_inode is None)
            or (self.object_directory_device is None) != (self.object_directory_inode is None)
            or (self.object_store_stage_device is None) != (self.object_store_stage_inode is None)
            or (self.object_store_stage_inode is not None and self.object_directory_inode is None)
            or (self.index_stage_device is None) != (self.index_stage_inode is None)
            or (self.pending_object_index is None and self.staging_inode is not None)
            or (
                self.pending_object_index is None
                and (
                    self.object_directory_inode is not None
                    or self.object_store_stage_inode is not None
                )
            )
            or (
                self.intent_checkpoint_id is None
                and (
                    self.state != "prepared"
                    or self.completed_object_count != 0
                    or self.pending_object_index is not None
                    or self.index_stage_inode is not None
                )
            )
            or (
                self.state != "prepared"
                and (
                    self.completed_object_count != self.plan.object_count
                    or self.pending_object_index is not None
                )
            )
        ):
            raise ValueError("object progress requires exact durable cursor and staging ownership")
        if (
            self.workspace_id != self.plan.preparation.workspace_id
            or (self.state == "checkpointed") != (self.checkpoint_id is not None)
            or self.created_at.tzinfo is None
        ):
            raise ValueError(
                "native commit record requires exact durable lineage and checkpoint state"
            )
        return self


class CommitRecoveryPosition(Contract):
    # Native inspection must derive these states; caller/model strings grant nothing.
    branch: Literal["parent", "exact_commit", "foreign"]
    index: Literal["original", "installing", "exact_tree", "foreign"]
    objects: Literal["incomplete", "complete", "foreign"]
    next_step: Literal["objects", "index", "reference", "checkpoint", "blocked"]

    @model_validator(mode="after")
    def exact(self):
        if self.branch == "foreign" or self.index == "foreign" or self.objects == "foreign":
            expected = "blocked"
        elif self.branch == "exact_commit":
            expected = (
                "checkpoint"
                if self.index == "exact_tree" and self.objects == "complete"
                else "blocked"
            )
        elif self.index == "exact_tree":
            expected = "reference" if self.objects == "complete" else "blocked"
        elif self.index == "installing":
            expected = "index" if self.objects == "complete" else "blocked"
        else:
            expected = "index" if self.objects == "complete" else "objects"
        if self.next_step != expected:
            raise ValueError("recovery may advance only from measured coherent Git states")
        return self


class NativeCommitRecovery(Contract):
    """Read-only protected recovery observation; never execution authority."""

    operation_id: Identifier
    workspace_id: Identifier
    command_id: Identifier
    original_plan_hash: Digest
    record_digest: Digest
    metadata_snapshot_digest: Digest
    phase: CommitPhase
    position: CommitRecoveryPosition
    next_step: Literal[
        "objects",
        "acknowledge_objects",
        "prepare_index",
        "index",
        "acknowledge_index",
        "prepare_reference",
        "reference",
        "checkpoint",
    ]
    recovery_hash: Digest

    @model_validator(mode="after")
    def exact(self):
        from app.self_build.commit_recovery import recovery_step

        if recovery_step(
            self.phase, self.position
        ) != self.next_step or self.recovery_hash != digest(
            self.model_dump(mode="json", exclude={"recovery_hash"})
        ):
            raise ValueError("recovery observation requires its exact coherent native position")
        return self
