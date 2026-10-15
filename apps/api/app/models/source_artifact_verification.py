"""Ephemeral artifact measurements never establish durable deletion ownership."""

from typing import Literal

from pydantic import Field, model_validator

from app.models.development_validation import Contract, Digest
from app.models.source_artifacts import ExpectedArtifactManifest
from app.models.source_change import MAX_FILE_BYTES, MAX_FILES
from app.models.source_mutation_inspection import MutationInspection


class ArtifactMeasurement(Contract):
    path: str = Field(min_length=1, max_length=512)
    presence: Literal["retained_original", "absent"]
    digest: Digest | None
    byte_count: int | None = Field(ge=0, le=MAX_FILE_BYTES, strict=True)
    device: int | None = Field(ge=0, le=2**64 - 1, strict=True)
    inode: int | None = Field(gt=0, le=2**128 - 1, strict=True)

    @model_validator(mode="after")
    def exact_presence(self):
        values = (self.digest, self.byte_count, self.device, self.inode)
        if (self.presence == "absent" and any(value is not None for value in values)) or (
            self.presence == "retained_original" and any(value is None for value in values)
        ):
            raise ValueError("SOURCE_ARTIFACT_MEASUREMENT_INVALID")
        return self


class NativeArtifactVerification(Contract):
    schema_version: Literal["1.0"] = "1.0"
    manifest: ExpectedArtifactManifest
    source_inspection: MutationInspection
    measurements: tuple[ArtifactMeasurement, ...] = Field(min_length=2, max_length=MAX_FILES * 2)
    verification_fingerprint: Digest
    native_artifacts_verified: Literal[True] = True
    execution_authorized: Literal[False] = False
    deletion_authorized: Literal[False] = False
    durable_artifact_ownership_verified: Literal[False] = False

    @model_validator(mode="after")
    def exact_expectations(self):
        if (
            self.source_inspection.operation_id != self.manifest.operation_id
            or self.source_inspection.mutation_plan_hash != self.manifest.mutation_plan_hash
            or self.source_inspection.checkpoint_id != self.manifest.checkpoint_id
            or len(self.measurements) != len(self.manifest.artifacts)
        ):
            raise ValueError("SOURCE_ARTIFACT_VERIFICATION_LINEAGE_INVALID")
        for actual, expected in zip(self.measurements, self.manifest.artifacts, strict=True):
            if (
                actual.path != expected.path
                or actual.presence != expected.presence
                or (
                    expected.expected_identity is not None
                    and (actual.digest, actual.byte_count)
                    != (expected.expected_identity.digest, expected.expected_identity.byte_count)
                )
            ):
                raise ValueError("SOURCE_ARTIFACT_VERIFICATION_MISMATCH")
        return self
