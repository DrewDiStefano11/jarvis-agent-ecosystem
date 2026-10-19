"""Protected historical correction source. It grants no new workspace or effect."""

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.models.development_agent import RepositoryContext
from app.models.development_dispatch import utc
from app.models.development_validation import Contract, Digest, FileChange
from app.models.self_build import Identifier
from app.models.source_artifact_verification import NativeArtifactVerification
from app.models.source_change import SourceContent


class BaselineFile(Contract):
    change: FileChange
    content: SourceContent | None = Field(repr=False)

    @model_validator(mode="after")
    def exact_bytes(self):
        if self.change.after_hash is None:
            if self.content is not None:
                raise ValueError("DEVELOPMENT_BASELINE_CONTENT_INVALID")
        elif self.content is None or (self.content.digest, self.content.mode) != (
            self.change.after_hash,
            self.change.after_mode,
        ):
            raise ValueError("DEVELOPMENT_BASELINE_CONTENT_INVALID")
        return self


class BaselineSnapshot(Contract):
    schema_version: Literal["1.0"] = "1.0"
    baseline_id: Digest
    admission_digest: Digest
    dispatch_id: Digest
    task_id: Identifier
    actor_id: Identifier
    editing_record_id: Digest
    editing_plan_hash: Digest
    mutation_operation_id: Identifier
    mutation_checkpoint_id: Identifier
    mutation_integrity_digest: Digest
    context: RepositoryContext = Field(repr=False)
    native_verification: NativeArtifactVerification = Field(repr=False)
    files: tuple[BaselineFile, ...] = Field(max_length=64, repr=False)
    captured_at: datetime
    snapshot_hash: Digest
    current_source_measured: Literal[False] = False
    execution_authorized: Literal[False] = False

    _utc = field_validator("captured_at")(utc)


class BaselineRecord(Contract):
    snapshot: BaselineSnapshot = Field(repr=False)
    audit_id: Identifier


class BaselineSummary(Contract):
    schema_version: Literal["1.0"] = "1.0"
    baseline_id: Digest
    snapshot_hash: Digest
    dispatch_id: Digest
    task_id: Identifier
    workspace_id: Identifier
    mutation_checkpoint_id: Identifier
    code_state_hash: Digest
    captured_at: datetime
    current_source_measured: Literal[False] = False
    execution_authorized: Literal[False] = False
    validation_passed: Literal[False] = False
