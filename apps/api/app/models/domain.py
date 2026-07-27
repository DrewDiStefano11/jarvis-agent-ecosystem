from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

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
    correlationId: str
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
    databaseRevision: str = "a87a487dd714"
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
    correlationId: str
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


class CreateTaskRequest(ContractModel):
    title: str = Field(min_length=3, max_length=160)
    description: str = Field(min_length=3, max_length=2000)
    priority: Literal["low", "medium", "high", "urgent"] = "medium"


class RegisterWorkerRequest(ContractModel):
    name: str = Field(min_length=1, max_length=160)
    instanceId: str = Field(min_length=1, max_length=120)
    leaseSeconds: int | None = Field(default=None, ge=1, le=3600)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AcquireTaskLeaseRequest(ContractModel):
    leaseSeconds: int | None = Field(default=None, ge=1, le=3600)


class LeaseCommandRequest(ContractModel):
    workerId: str
    leaseToken: str


class RenewTaskLeaseRequest(LeaseCommandRequest):
    leaseSeconds: int | None = Field(default=None, ge=1, le=3600)
    checkpointId: str | None = None


class CompleteTaskLeaseRequest(LeaseCommandRequest):
    result: str = Field(max_length=20000)


class FailTaskLeaseRequest(LeaseCommandRequest):
    error: dict[str, Any]
    retryable: bool = False


class CreateTemporaryAgentRequest(ContractModel):
    name: str = Field(min_length=2, max_length=60)
    role: str = Field(min_length=2, max_length=100)
    departmentId: str = "research"


class DecisionRequest(ContractModel):
    decisionNote: str | None = Field(default=None, max_length=500)
    reviewedBy: str = "local-user"


class EditApprovalRequest(ContractModel):
    title: str | None = Field(default=None, min_length=3, max_length=160)
    description: str | None = Field(default=None, min_length=3, max_length=1000)


class FailureRequest(ContractModel):
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


class ErrorDetail(ContractModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(ContractModel):
    error: ErrorDetail
