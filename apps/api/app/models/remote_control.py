"""Remote controls request lifecycle actions; they cannot forge worker completion."""

from pydantic import BaseModel, ConfigDict, Field

from app.models.agent_runtime import (
    RequestCancellationCommand,
    RequestPauseCommand,
    ResumeAgentRunCommand,
)
from app.models.domain import AuditEvent, Task
from app.models.identity import AgentIdentity

RemoteRuntimeCommand = RequestPauseCommand | ResumeAgentRunCommand | RequestCancellationCommand


class RemoteGoalPage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[Task] = Field(max_length=100)
    nextOffset: int | None = Field(default=None, ge=0)


class RemoteAuditPage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[AuditEvent] = Field(max_length=100)
    nextOffset: int | None = Field(default=None, ge=0)


class RemoteAgentPage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[AgentIdentity] = Field(max_length=100)
    nextOffset: int | None = Field(default=None, ge=0)


class RemoteSystemStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    emergencyStop: bool
    simulatorStatus: str = Field(max_length=40)
    recoveryStatus: str = Field(max_length=40)
    eventSessionId: str = Field(max_length=80)
    lastSequenceNumber: int = Field(ge=0)
