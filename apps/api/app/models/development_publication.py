"""Reference-only protected publication source bridge. Never an effect grant."""

from datetime import datetime
from typing import Literal

from pydantic import field_validator

from app.models.development_validation import Commit, Contract, Digest
from app.models.self_build import Identifier


class PublicationSourceReference(Contract):
    publication_operation_id: Identifier
    expected_head_sha: Commit
    observation_digest: Digest


class PublicationSourceAnchor(Contract):
    schema_version: Literal["1.0"] = "1.0"
    reference: PublicationSourceReference
    task_id: Identifier
    workspace_id: Identifier
    source_edit_record_id: Digest
    source_edit_plan_hash: Digest
    mutation_operation_id: Identifier
    mutation_checkpoint_id: Identifier
    parent_code_state_hash: Digest
    commit_sha: Commit
    tree_sha: Commit
    parent_sha: Commit
    native_checkpoint_id: Identifier
    native_checkpoint_integrity: Digest
    link_hash: Digest
    observed_at: datetime
    historical_only: Literal[True] = True
    mutation_authority: Literal[False] = False
    validation_authority: Literal[False] = False

    @field_validator("observed_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("DEVELOPMENT_TIMESTAMP_INVALID")
        return value
