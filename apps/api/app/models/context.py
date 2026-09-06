from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.agent_runtime import SafeMetadataValue, normalize_safe_metadata


class ContextContract(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class TrustLevel(StrEnum):
    SYSTEM_POLICY = "system_policy"
    TRUSTED_CONFIGURATION = "trusted_configuration"
    OPERATOR_INSTRUCTION = "operator_instruction"
    TASK_REQUEST = "task_request"
    TRUSTED_VALIDATOR = "trusted_validator"
    TRUSTED_TOOL_RESULT = "trusted_tool_result"
    APPROVED_ARTIFACT = "approved_artifact"
    REPOSITORY_CONTENT = "repository_content"
    EXTERNAL_CONTENT = "external_content"
    PRIOR_MODEL_OUTPUT = "prior_model_output"
    UNKNOWN = "unknown"


class ContextSourceType(StrEnum):
    SYSTEM_POLICY = "system_policy"
    OPERATOR_INSTRUCTION = "operator_instruction"
    TASK_REQUEST = "task_request"
    REPOSITORY_FILE = "repository_file"
    ARTIFACT = "artifact"
    TOOL_RESULT = "tool_result"
    VALIDATOR_RESULT = "validator_result"
    PRIOR_MODEL_OUTPUT = "prior_model_output"
    EXTERNAL_DOCUMENT = "external_document"
    MANUAL_NOTE = "manual_note"


class InjectionSeverity(StrEnum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ExclusionReason(StrEnum):
    SOURCE_TYPE_DENIED = "source_type_denied"
    TRUST_LEVEL_DENIED = "trust_level_denied"
    WRONG_PROJECT = "wrong_project"
    NOT_APPROVED = "not_approved"
    CRITICAL_INJECTION = "critical_injection"
    OVER_BUDGET = "over_budget"
    DUPLICATE = "duplicate"
    MISSING_PROVENANCE = "missing_provenance"
    INVALID_HASH = "invalid_hash"
    POLICY_CONFLICT = "policy_conflict"


class ContextSourceMetadata(ContextContract):
    projectId: str | None = Field(default=None, min_length=1, max_length=120)
    approved: bool = False
    sensitivity: Literal["public", "internal", "sensitive", "restricted"] | None = None
    inclusionPriority: int = Field(default=0, ge=-1000, le=1000)
    truncationAllowed: bool = True
    exactPreservationRequired: bool = False
    additional: dict[str, SafeMetadataValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_additional_metadata(self) -> ContextSourceMetadata:
        self.additional = normalize_safe_metadata(
            self.additional, field_name="context_source.metadata.additional"
        )
        return self


class ContextSource(ContextContract):
    sourceId: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
    sourceType: ContextSourceType
    trustLevel: TrustLevel
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=200_000)
    contentHash: str = Field(pattern=r"^[0-9a-f]{64}$")
    relativePath: str | None = Field(default=None, max_length=500)
    createdAt: datetime | None = None
    metadata: ContextSourceMetadata = Field(default_factory=ContextSourceMetadata)


class ContextPolicy(ContextContract):
    policyVersion: str = Field(
        default="phase-2b-context-1",
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$",
    )
    estimatedTokenBudget: int = Field(default=8192, ge=256, le=65_536)
    allowedSourceTypes: list[ContextSourceType] = Field(
        default_factory=lambda: [
            ContextSourceType.SYSTEM_POLICY,
            ContextSourceType.OPERATOR_INSTRUCTION,
            ContextSourceType.TASK_REQUEST,
            ContextSourceType.REPOSITORY_FILE,
            ContextSourceType.ARTIFACT,
            ContextSourceType.TOOL_RESULT,
            ContextSourceType.VALIDATOR_RESULT,
            ContextSourceType.PRIOR_MODEL_OUTPUT,
            ContextSourceType.EXTERNAL_DOCUMENT,
            ContextSourceType.MANUAL_NOTE,
        ],
        min_length=1,
        max_length=10,
    )
    allowedTrustLevels: list[TrustLevel] = Field(
        default_factory=lambda: [
            TrustLevel.SYSTEM_POLICY,
            TrustLevel.TRUSTED_CONFIGURATION,
            TrustLevel.TASK_REQUEST,
            TrustLevel.OPERATOR_INSTRUCTION,
            TrustLevel.TRUSTED_VALIDATOR,
            TrustLevel.TRUSTED_TOOL_RESULT,
            TrustLevel.APPROVED_ARTIFACT,
            TrustLevel.REPOSITORY_CONTENT,
            TrustLevel.EXTERNAL_CONTENT,
            TrustLevel.PRIOR_MODEL_OUTPUT,
        ],
        min_length=1,
        max_length=11,
    )
    maximumSourceCount: int = Field(default=32, ge=1, le=64)
    reservedOutputTokens: int = Field(default=1024, ge=1, le=32_768)
    crossProjectContextAllowed: bool = False
    maximumContextTokens: int | None = Field(default=None, ge=256, le=65_536)
    minimumRequiredContext: int = Field(default=0, ge=0, le=64)

    @model_validator(mode="after")
    def validate_budget(self) -> ContextPolicy:
        effective_budget = self.maximumContextTokens or self.estimatedTokenBudget
        if self.reservedOutputTokens >= effective_budget:
            raise ValueError("reservedOutputTokens must be below the effective context budget")
        if self.minimumRequiredContext > self.maximumSourceCount:
            raise ValueError("minimumRequiredContext cannot exceed maximumSourceCount")
        if len(set(self.allowedSourceTypes)) != len(self.allowedSourceTypes):
            raise ValueError("allowedSourceTypes cannot contain duplicates")
        if len(set(self.allowedTrustLevels)) != len(self.allowedTrustLevels):
            raise ValueError("allowedTrustLevels cannot contain duplicates")
        return self


class CreateContextAssemblyRequest(ContextContract):
    taskId: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
    projectId: str = Field(min_length=1, max_length=120)
    allowedResultType: str = Field(
        default="structured_output",
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$",
    )
    completionCriteria: str = Field(min_length=1, max_length=2000)
    toolAvailabilitySummary: dict[str, list[str]] = Field(default_factory=dict)
    policy: ContextPolicy = Field(default_factory=ContextPolicy)
    sources: list[ContextSource] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def validate_tool_summary(self) -> CreateContextAssemblyRequest:
        if len(self.toolAvailabilitySummary) > 32:
            raise ValueError("toolAvailabilitySummary accepts at most 32 keys")
        total_characters = 0
        for key, values in self.toolAvailabilitySummary.items():
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", key):
                raise ValueError("toolAvailabilitySummary contains an invalid key")
            if len(values) > 100:
                raise ValueError("toolAvailabilitySummary values exceed the item limit")
            if any(not value or len(value) > 160 for value in values):
                raise ValueError("toolAvailabilitySummary contains an invalid value")
            total_characters += len(key) + sum(len(value) for value in values)
        if total_characters > 20_000:
            raise ValueError("toolAvailabilitySummary exceeds the size limit")
        return self


class ModelMessage(ContextContract):
    role: Literal["system", "developer", "user"]
    content: str


class ModelRequest(ContextContract):
    schemaVersion: str = "1.0"
    requestId: str
    taskId: str
    projectId: str
    messages: list[ModelMessage]
    requestHash: str
    generation: dict[str, Any] = Field(default_factory=dict)
    contextManifestId: str


class IncludedContextSource(ContextContract):
    sourceId: str
    sourceType: ContextSourceType
    trustLevel: TrustLevel
    originalContentHash: str
    assembledContentHash: str
    originalSize: int
    includedSize: int
    estimatedTokens: int
    truncated: bool = False


class ExcludedContextSource(ContextContract):
    sourceId: str
    reason: ExclusionReason
    detail: str | None = None


class RedactionFinding(ContextContract):
    sourceId: str
    category: str
    count: int


class InjectionFinding(ContextContract):
    sourceId: str
    category: str
    severity: InjectionSeverity
    count: int


class ConflictFinding(ContextContract):
    sourceId: str
    conflictWith: str
    category: str
    description: str


class DuplicateContextSource(ContextContract):
    sourceId: str
    keptSourceId: str
    reason: Literal["duplicate", "duplicate_replaced_by_higher_trust"]


class ContextBudget(ContextContract):
    estimatedInputTokens: int
    maximumContextTokens: int
    reservedOutputTokens: int
    withinBudget: bool


class ContextManifest(ContextContract):
    schemaVersion: str = "1.0"
    manifestId: str
    taskId: str
    projectId: str
    policyVersion: str
    includedSources: list[IncludedContextSource] = Field(default_factory=list)
    excludedSources: list[ExcludedContextSource] = Field(default_factory=list)
    redactions: list[RedactionFinding] = Field(default_factory=list)
    injectionFindings: list[InjectionFinding] = Field(default_factory=list)
    conflicts: list[ConflictFinding] = Field(default_factory=list)
    duplicateSources: list[DuplicateContextSource] = Field(default_factory=list)
    truncatedSourceIds: list[str] = Field(default_factory=list)
    budget: ContextBudget
    requestHash: str


class ContextAssemblyReport(ContextContract):
    includedSourceCount: int
    excludedSourceCount: int
    includedBytes: int
    estimatedInputTokens: int
    tokenBudget: int
    reservedTokens: int
    redactionCount: int
    injectionFindingCount: int
    conflictCount: int
    duplicateSourceCount: int
    truncatedSourceCount: int
    humanReviewRequired: bool
    finalAssemblyStatus: Literal["completed", "review_required"]


class ContextAssembly(ContextContract):
    id: str
    schemaVersion: str = "1.0"
    taskId: str
    projectId: str
    status: Literal["completed", "review_required"]
    inputHash: str
    requestHash: str
    policyVersion: str
    modelRequest: ModelRequest | None
    manifest: ContextManifest
    report: ContextAssemblyReport
    createdAt: datetime


class ContextAssemblyResponse(ContextContract):
    data: ContextAssembly
    meta: dict[str, Any] = Field(default_factory=lambda: {"schemaVersion": "1.0"})


class ContextAssemblyListResponse(ContextContract):
    data: list[ContextAssembly]
    meta: dict[str, Any] = Field(default_factory=lambda: {"schemaVersion": "1.0"})


class ContextAssemblyEventPayload(ContextContract):
    assemblyId: str
    status: Literal["completed", "review_required"]
    requestHash: str
    includedSourceCount: int = Field(ge=0)
    excludedSourceCount: int = Field(ge=0)
    redactionCount: int = Field(ge=0)
    injectionFindingCount: int = Field(ge=0)
    conflictCount: int = Field(ge=0)


class ContextAssemblerStatus(ContextContract):
    state: Literal["ready", "unavailable"] = "ready"
    totalAssemblies: int = 0
    completedAssemblies: int = 0
    reviewRequiredAssemblies: int = 0
    includedSources: int = 0
    excludedSources: int = 0
    redactions: int = 0
    injectionFindings: int = 0
    lastAssemblyAt: datetime | None = None
