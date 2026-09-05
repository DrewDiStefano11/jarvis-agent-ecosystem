from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer

from app.models.agent_runtime import (
    normalize_safe_metadata,
    validate_identifier,
    validate_safe_text,
)
from app.models.autonomous_worker import AutonomousWorkerStatus
from app.models.constraints import MAX_CORRELATION_ID_LENGTH
from app.models.context import ContextAssemblerStatus

AgentStatus = Literal[
    "idle",
    "assigned",
    "planning",
    "thinking",
    "researching",
    "executing_tool",
    "waiting_for_model",
    "waiting_for_agent",
    "waiting_for_approval",
    "reviewing",
    "paused",
    "failed",
    "retrying",
    "delivering",
    "completed",
    "offline",
]
TaskStatus = Literal[
    "queued",
    "planning",
    "assigned",
    "in_progress",
    "waiting",
    "waiting_for_approval",
    "under_review",
    "revision_requested",
    "paused",
    "failed",
    "retrying",
    "completed",
    "cancelled",
]


class ContractModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class RequestContractModel(ContractModel):
    """Strict base for data accepted from an HTTP caller."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Performance(ContractModel):
    completionRate: float
    accuracyScore: float
    averageCompletionTime: int
    failedTaskCount: int
    userCorrectionCount: int
    reviewerScore: float
    reliabilityScore: float


class Office(ContractModel):
    zone: str
    deskId: str
    spriteIdentifier: str
    displayPosition: dict[str, int]
    currentAnimationState: str
    currentDestination: str | None = None
    isInMeeting: bool = False


class Agent(ContractModel):
    id: str
    schemaVersion: str = "1.0"
    name: str
    role: str
    description: str
    goals: list[str] = Field(default_factory=list)
    departmentId: str
    managerId: str | None = None
    status: AgentStatus = "idle"
    previousStatus: AgentStatus | None = None
    currentTaskId: str | None = None
    queuedTaskIds: list[str] = Field(default_factory=list)
    progress: int = Field(default=0, ge=0, le=100)
    statusMessage: str = "Available"
    capabilities: list[str]
    allowedTools: list[str]
    deniedTools: list[str]
    approvalPolicy: dict[str, Any]
    memoryAccess: dict[str, Any]
    performance: Performance
    resourceProfile: dict[str, Any]
    office: Office
    createdAt: datetime
    updatedAt: datetime
    version: str = "1.0.0"
    deploymentStatus: str = "simulated"
    isTemporary: bool = False


class Department(ContractModel):
    id: str
    name: str
    description: str
    managerAgentId: str | None
    agentIds: list[str]


class TaskDependency(ContractModel):
    taskId: str
    type: Literal["blocks", "requires", "informs"]


class Task(ContractModel):
    id: str
    schemaVersion: str = "1.0"
    title: str
    description: str
    request: str
    parentTaskId: str | None = None
    correctionOfTaskId: str | None = None
    childTaskIds: list[str] = Field(default_factory=list)
    projectId: str | None = None
    createdBy: str
    assignedManagerId: str | None = None
    assignedAgentIds: list[str] = Field(default_factory=list)
    priority: Literal["low", "medium", "high", "urgent"] = "medium"
    status: TaskStatus = "queued"
    progress: int = Field(default=0, ge=0, le=100)
    statusMessage: str = "Queued"
    dependencies: list[TaskDependency] = Field(default_factory=list)
    blockedBy: list[str] = Field(default_factory=list)
    approvalIds: list[str] = Field(default_factory=list)
    artifactIds: list[str] = Field(default_factory=list)
    result: str | None = None
    error: dict[str, Any] | None = None
    retryCount: int = 0
    maxRetries: int = 2
    createdAt: datetime
    startedAt: datetime | None = None
    updatedAt: datetime
    completedAt: datetime | None = None

    @model_serializer(mode="wrap")
    def preserve_legacy_serialization(self, handler):
        payload = handler(self)
        if self.correctionOfTaskId is None:
            payload.pop("correctionOfTaskId", None)
        return payload


class Approval(ContractModel):
    id: str
    schemaVersion: str = "1.0"
    taskId: str
    requestedByAgentId: str
    actionType: str
    title: str
    description: str
    reason: str
    riskLevel: Literal["green", "yellow", "orange", "red", "black"]
    affectedResources: list[str]
    exactActionPreview: str
    expectedOutcome: str
    reversalMethod: str
    expiresAt: datetime
    status: Literal["pending", "approved", "rejected", "expired", "cancelled"] = "pending"
    reviewedBy: str | None = None
    reviewedAt: datetime | None = None
    decisionNote: str | None = None
    createdAt: datetime


class Artifact(ContractModel):
    id: str
    taskId: str
    name: str
    type: str
    summary: str
    simulatedPath: str
    createdAt: datetime


class AuditEvent(ContractModel):
    id: str
    timestamp: datetime
    eventType: str
    actorAgentId: str | None = None
    taskId: str | None = None
    previousState: str | None = None
    newState: str | None = None
    summary: str
    correlationId: str = Field(min_length=1, max_length=MAX_CORRELATION_ID_LENGTH)
    sequenceNumber: int
    payload: dict[str, Any] = Field(default_factory=dict)
    artifactIds: list[str] = Field(default_factory=list)
    approvalId: str | None = None


class Notification(ContractModel):
    id: str
    title: str
    message: str
    level: Literal["info", "success", "warning", "error"]
    isRead: bool = False
    taskId: str | None = None
    createdAt: datetime


class ResourceStatus(ContractModel):
    name: str
    value: str
    label: str = "Simulated"


class SimulatorControl(ContractModel):
    state: Literal["idle", "running", "paused", "completed", "failed", "recovery_required"] = "idle"
    currentStep: int = 0
    totalSteps: int = 25
    accelerated: bool = False


class SystemStatus(ContractModel):
    status: str = "healthy"
    environment: str = "development"
    apiSchemaVersion: str = "1.0"
    seedDataVersion: str = "1.0"
    emergencyStop: bool = False
    simulator: SimulatorControl
    resources: list[ResourceStatus]
    lastSynchronizedAt: datetime
    storageBackend: str = "sqlite"
    databaseHealthy: bool = True
    databaseRevision: str = "20260905_08"
    schemaCurrent: bool = True
    eventSessionId: str
    outboxPendingCount: int = 0
    outboxExhaustedCount: int = 0
    recoveryRequired: bool = False
    activeWorkflowRunId: str | None = None
    lastCheckpointId: str | None = None
    lastStartupAt: datetime | None = None
    lastCleanShutdown: datetime | None = None
    contextAssembler: ContextAssemblerStatus = Field(default_factory=ContextAssemblerStatus)
    activeWorkerCount: int = 0
    activeLeaseCount: int = 0
    expiredLeaseCount: int = 0
    staleWorkerCount: int = 0
    autonomousWorker: AutonomousWorkerStatus = Field(
        default_factory=lambda: AutonomousWorkerStatus(
            enabled=False,
            modelExecutionMode="disabled",
            providerReady=False,
        )
    )


class Worker(ContractModel):
    id: str
    name: str
    instanceId: str
    status: Literal["active", "draining", "stopped"]
    startedAt: datetime
    lastHeartbeatAt: datetime
    stoppedAt: datetime | None = None
    leaseSeconds: int
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def metadata_is_safe(cls, value: dict[str, Any]) -> dict[str, Any]:
        return normalize_safe_metadata(value, field_name="worker.metadata")


class TaskLease(ContractModel):
    taskId: str
    workerId: str
    leaseToken: str
    acquiredAt: datetime
    expiresAt: datetime
    renewedAt: datetime
    attemptNumber: int
    version: int
    recoveryCheckpointId: str | None = None


class EventEnvelope(ContractModel):
    eventId: str
    schemaVersion: str = "1.0"
    eventType: str
    timestamp: datetime
    sequenceNumber: int
    eventSessionId: str | None = None
    correlationId: str = Field(min_length=1, max_length=MAX_CORRELATION_ID_LENGTH)
    taskId: str | None = None
    agentId: str | None = None
    source: str = "simulator"
    payload: dict[str, Any]


class AgentStatusPayload(ContractModel):
    previousStatus: AgentStatus
    newStatus: AgentStatus
    progress: int
    statusMessage: str


class TaskStatusPayload(ContractModel):
    previousStatus: TaskStatus
    newStatus: TaskStatus
    progress: int
    statusMessage: str


class TaskDelegationPayload(ContractModel):
    managerAgentId: str
    delegatedAgentIds: list[str]
    childTaskIds: list[str]


class ApprovalEventPayload(ContractModel):
    approvalId: str
    status: Literal["pending", "approved", "rejected", "expired", "cancelled"]
    riskLevel: Literal["green", "yellow", "orange", "red", "black"]


class SystemEventPayload(ContractModel):
    state: str
    emergencyStop: bool = False


class TemporaryAgentEventPayload(ContractModel):
    temporaryAgentId: str
    deploymentStatus: str = "temporary-simulated"


class ErrorEventPayload(ContractModel):
    code: str
    message: str
    retryable: bool = False


class AgentStatusEvent(EventEnvelope):
    payload: AgentStatusPayload


class TaskStatusEvent(EventEnvelope):
    payload: TaskStatusPayload


class TaskDelegationEvent(EventEnvelope):
    payload: TaskDelegationPayload


class ApprovalEvent(EventEnvelope):
    payload: ApprovalEventPayload


class SystemEvent(EventEnvelope):
    payload: SystemEventPayload


class TemporaryAgentEvent(EventEnvelope):
    payload: TemporaryAgentEventPayload


class ErrorEvent(EventEnvelope):
    payload: ErrorEventPayload


class CreateTaskRequest(RequestContractModel):
    title: str = Field(min_length=3, max_length=160)
    description: str = Field(min_length=3, max_length=2000)
    priority: Literal["low", "medium", "high", "urgent"] = "medium"
    correctionOfTaskId: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$"
    )
    projectId: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")

    @model_serializer(mode="wrap")
    def preserve_legacy_serialization(self, handler):
        payload = handler(self)
        for field in ("correctionOfTaskId", "projectId"):
            if getattr(self, field) is None:
                payload.pop(field, None)
        return payload


class RegisterWorkerRequest(RequestContractModel):
    name: str = Field(min_length=1, max_length=160)
    instanceId: str = Field(min_length=1, max_length=120)
    leaseSeconds: int | None = Field(default=None, ge=1, le=3600)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def metadata_is_safe(cls, value: dict[str, Any]) -> dict[str, Any]:
        return normalize_safe_metadata(value, field_name="worker.metadata")


class AcquireTaskLeaseRequest(RequestContractModel):
    leaseSeconds: int | None = Field(default=None, ge=1, le=3600)
    taskId: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")


class LeaseCommandRequest(RequestContractModel):
    workerId: str = Field(min_length=1, max_length=80)
    leaseToken: str = Field(min_length=1, max_length=80)

    @field_validator("workerId", "leaseToken")
    @classmethod
    def capability_identifiers_are_safe(cls, value: str, info) -> str:
        return validate_identifier(value, field_name=str(info.field_name), max_length=80)


class RenewTaskLeaseRequest(LeaseCommandRequest):
    leaseSeconds: int | None = Field(default=None, ge=1, le=3600)
    checkpointId: str | None = Field(default=None, min_length=1, max_length=120)

    @field_validator("checkpointId")
    @classmethod
    def checkpoint_identifier_is_safe(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_identifier(value, field_name="checkpointId", max_length=120)


class CompleteTaskLeaseRequest(LeaseCommandRequest):
    result: str = Field(max_length=20000)

    @field_validator("result")
    @classmethod
    def result_is_safe(cls, value: str) -> str:
        validate_safe_text(
            value.replace("\r", " ").replace("\n", " ").replace("\t", " "),
            field_name="lease.result",
            max_length=20_000,
        )
        return value


class FailTaskLeaseRequest(LeaseCommandRequest):
    error: dict[str, Any]
    retryable: bool = False

    @field_validator("error")
    @classmethod
    def error_is_safe(cls, value: dict[str, Any]) -> dict[str, Any]:
        return normalize_safe_metadata(value, field_name="lease.error")


class CreateTemporaryAgentRequest(RequestContractModel):
    name: str = Field(min_length=2, max_length=60)
    role: str = Field(min_length=2, max_length=100)
    departmentId: str = "research"


class DecisionRequest(RequestContractModel):
    decisionNote: str | None = Field(default=None, max_length=500)
    reviewedBy: str = Field(default="local-user", min_length=1, max_length=120)

    @field_validator("reviewedBy")
    @classmethod
    def reviewer_is_safe(cls, value: str) -> str:
        return validate_identifier(value, field_name="reviewedBy", max_length=120)


class EditApprovalRequest(RequestContractModel):
    title: str | None = Field(default=None, min_length=3, max_length=160)
    description: str | None = Field(default=None, min_length=3, max_length=1000)


class FailureRequest(RequestContractModel):
    scenario: Literal[
        "scout_research_failure",
        "archive_unavailable",
        "sentinel_rejection",
        "expired_approval",
        "invalid_transition",
        "websocket_disconnect",
    ] = "scout_research_failure"


class ApiResponse(ContractModel):
    data: Any
    meta: dict[str, Any] = Field(default_factory=lambda: {"schemaVersion": "1.0"})


DataT = TypeVar("DataT")


class TypedApiResponse(ContractModel, Generic[DataT]):
    """The standard successful-response envelope with a typed ``data`` payload.

    This is the same wire contract as :class:`ApiResponse`; the generic
    parameter only lets routes declare the inner model so the generated OpenAPI
    documents the real payload schema instead of an untyped object.
    """

    data: DataT
    meta: dict[str, Any] = Field(default_factory=lambda: {"schemaVersion": "1.0"})


class ErrorDetail(ContractModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(ContractModel):
    error: ErrorDetail
