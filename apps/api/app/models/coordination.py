"""Durable coordinator projections. Runtime remains the execution authority."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, PrivateAttr, field_validator, model_validator

from app.models.decomposition import DecompositionRecord, StrictModel, ready_keys


class CoordinatedSubtask(StrictModel):
    subtaskId: str
    key: str
    assignedAgentId: str
    runtimeRunId: str
    status: Literal[
        "pending", "claimed", "running", "retrying", "succeeded", "blocked", "failed"
    ] = "pending"
    runtimeAttemptId: str | None = None
    attemptCount: int = Field(default=0, ge=0, le=3)
    resultSummary: str | None = Field(default=None, max_length=8_000)
    resultDigest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    evidence: list[str] = Field(default_factory=list, max_length=16)
    checkpointId: str | None = Field(default=None, max_length=120)
    failureCategory: str | None = Field(default=None, max_length=80)
    failureDetail: str | None = Field(default=None, max_length=500)
    retryEligibleAt: datetime | None = None
    dispatchLeaseFingerprint: str | None = None
    provider: str | None = None
    model: str | None = None

    @model_validator(mode="after")
    def successful_node_has_evidence(self):
        if self.status == "succeeded" and (
            not self.resultSummary
            or not self.resultDigest
            or not self.evidence
            or not self.checkpointId
        ):
            raise ValueError("Successful coordinated subtask requires durable evidence")
        return self


class CoordinationFailure(StrictModel):
    subtaskId: str
    runtimeAttemptId: str
    attemptNumber: int = Field(ge=1, le=3)
    category: str = Field(min_length=1, max_length=80)
    detail: str = Field(min_length=1, max_length=500)
    retryable: bool
    recordedAt: datetime


class CoordinationSynthesis(StrictModel):
    status: Literal["pending", "running", "succeeded", "failed"] = "pending"
    attemptCount: int = Field(default=0, ge=0, le=2)
    runtimeRunId: str
    runtimeAttemptId: str | None = None
    inputSubtaskIds: list[str] = Field(default_factory=list, max_length=12)
    inputsDigest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    summary: str | None = Field(default=None, max_length=16_000)
    resultDigest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    checkpointId: str | None = Field(default=None, max_length=120)
    failureDetail: str | None = Field(default=None, max_length=500)
    retryEligibleAt: datetime | None = None
    dispatchLeaseFingerprint: str | None = None
    provider: str | None = None
    model: str | None = None

    @model_validator(mode="after")
    def successful_synthesis_has_evidence(self):
        if self.status == "succeeded" and (
            not self.inputSubtaskIds
            or not self.inputsDigest
            or not self.summary
            or not self.resultDigest
            or not self.checkpointId
        ):
            raise ValueError("Successful synthesis requires durable inputs and evidence")
        return self


class SpecialistResult(StrictModel):
    _inference_identity: tuple[str, str] = PrivateAttr(default=("fixture", "scripted"))
    subtaskId: str = Field(min_length=1, max_length=120)
    schemaVersion: Literal["1.0"] = "1.0"
    summary: str = Field(min_length=1, max_length=8_000)
    evidence: list[Annotated[str, Field(min_length=1, max_length=2_000)]] = Field(
        min_length=1, max_length=16
    )
    completionCriteriaSatisfied: list[Annotated[str, Field(min_length=1, max_length=1_200)]] = (
        Field(min_length=1, max_length=8)
    )

    @model_validator(mode="after")
    def bounded_checkpoint_payload(self):
        size = len(self.summary) + sum(map(len, self.evidence))
        if size > 12_000:
            raise ValueError("Specialist result exceeds durable checkpoint bound")
        return self


class SynthesisResult(StrictModel):
    _inference_identity: tuple[str, str] = PrivateAttr(default=("fixture", "scripted"))
    schemaVersion: Literal["1.0"] = "1.0"
    summary: str = Field(min_length=1, max_length=16_000)
    contributingSubtaskIds: list[str] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def unique_contributors(self):
        if len(set(self.contributingSubtaskIds)) != len(self.contributingSubtaskIds):
            raise ValueError("Duplicate synthesis contributor")
        return self


class CoordinationRecord(StrictModel):
    schemaVersion: Literal["1"] = "1"
    coordinatorVersion: Literal["coordinator-1"] = "coordinator-1"
    id: str
    taskId: str
    decompositionId: str
    decompositionHash: str
    runtimeRunId: str
    status: Literal["active", "blocked", "synthesizing", "completing", "completed", "failed"] = (
        "active"
    )
    maximumAttemptsPerSubtask: Literal[3] = 3
    maximumSynthesisAttempts: Literal[2] = 2
    modelDispatchCount: int = Field(default=0, ge=0, le=38)
    nodes: list[CoordinatedSubtask] = Field(min_length=1, max_length=12)
    failures: list[CoordinationFailure] = Field(default_factory=list, max_length=36)
    synthesis: CoordinationSynthesis
    finalResultReference: str | None = Field(default=None, max_length=160)
    createdAt: datetime
    updatedAt: datetime
    completedAt: datetime | None = None
    blockedReason: str | None = Field(default=None, max_length=120)

    @field_validator("nodes")
    @classmethod
    def unique_nodes(cls, value):
        if len({node.subtaskId for node in value}) != len(value):
            raise ValueError("Duplicate coordinated subtask")
        return value


def execution_ready_keys(
    graph: DecompositionRecord,
    *,
    completed: frozenset[str] = frozenset(),
    owned: frozenset[str] = frozenset(),
    eligible_agents: frozenset[str],
    execution_permitted: bool,
) -> list[str]:
    """All independent nodes stay eligible; claims impose the execution bound."""
    if not execution_permitted or graph.status != "ready":
        return []
    eligible = {n.key for n in graph.subtasks if n.assignedAgentId in eligible_agents}
    return [
        key for key in ready_keys(graph.subtasks, completed) if key in eligible and key not in owned
    ]
