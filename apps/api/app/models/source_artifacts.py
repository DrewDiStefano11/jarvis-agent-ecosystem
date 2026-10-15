"""Bounded expected artifacts confer no ownership, deletion or execution grant."""

import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.models.development_validation import Contract, Digest
from app.models.self_build import Identifier
from app.models.source_change import MAX_FILES, MAX_TOTAL_BYTES, FileIdentity, source_path


class ArtifactExpectation(Contract):
    change_index: int = Field(ge=0, lt=MAX_FILES, strict=True)
    source_path: str
    # A derived sibling can be longer than its canonical source path because
    # the fixed hashed artifact basename replaces a short source basename.
    path: str = Field(min_length=1, max_length=512)
    presence: Literal["retained_original", "absent"]
    expected_identity: FileIdentity | None

    _source = field_validator("source_path", mode="before")(source_path)

    @model_validator(mode="after")
    def exact_sibling(self):
        parent = self.source_path.rpartition("/")[0]
        name = self.path.rpartition("/")[2]
        if (
            not re.fullmatch(r"\.jarvis-edit-[a-f0-9]{64}\.(before|after)", name)
            or self.path != (parent + "/" + name if parent else name)
            or (self.presence == "retained_original") != (self.expected_identity is not None)
            or (self.presence == "retained_original" and not name.endswith(".before"))
        ):
            raise ValueError("SOURCE_ARTIFACT_EXPECTATION_INVALID")
        return self


class ExpectedArtifactManifest(Contract):
    schema_version: Literal["1.0"] = "1.0"
    policy_digest: Digest
    operation_id: Identifier
    mutation_plan_hash: Digest
    mutation_intent_digest: Digest
    editing_record_id: Digest
    editing_plan_hash: Digest
    original_approval_id: Identifier
    checkpoint_id: Identifier
    artifacts: tuple[ArtifactExpectation, ...] = Field(min_length=2, max_length=MAX_FILES * 2)
    retained_total_bytes: int = Field(ge=0, le=MAX_TOTAL_BYTES, strict=True)
    manifest_hash: Digest
    native_artifacts_verified: Literal[False] = False
    execution_authorized: Literal[False] = False
    deletion_authorized: Literal[False] = False
    checkout_root_lock_allowed: Literal[False] = False

    @model_validator(mode="after")
    def bounded_unique(self):
        if len({item.path.casefold() for item in self.artifacts}) != len(self.artifacts):
            raise ValueError("SOURCE_ARTIFACT_PATH_COLLISION")
        if self.retained_total_bytes != sum(
            item.expected_identity.byte_count for item in self.artifacts if item.expected_identity
        ):
            raise ValueError("SOURCE_ARTIFACT_TOTAL_INVALID")
        return self
