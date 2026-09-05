from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ToolName = Literal["workspace.list", "workspace.read", "workspace.write", "workspace.report"]


class ToolContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolStep(ToolContract):
    tool: ToolName
    path: str = Field(min_length=1, max_length=240)
    content: str | None = Field(default=None, max_length=65536)
    expectedContentHash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_content(self):
        if self.tool in {"workspace.write", "workspace.report"}:
            if self.content is None:
                raise ValueError("write/report requires explicit reviewed content")
            if len(self.content.encode("utf-8")) > 65536:
                raise ValueError("content exceeds the byte limit")
        elif self.content is not None or self.expectedContentHash is not None:
            raise ValueError("read/list steps cannot specify write content or an overwrite hash")
        return self


class ToolScope(ToolContract):
    workspaceId: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$")
    allowedTools: list[ToolName] = Field(min_length=1, max_length=4)
    readPrefixes: list[str] = Field(default_factory=list, max_length=8)
    writePrefixes: list[str] = Field(default_factory=list, max_length=8)
    maximumBytes: int = Field(default=65536, ge=1, le=65536)
    maximumSteps: int = Field(default=8, ge=1, le=8)


class WorkspaceInfo(ToolContract):
    workspaceId: str
    displayName: str
    allowedTools: list[ToolName]
    readPrefixes: list[str] = Field(default_factory=lambda: ["inputs"])
    writePrefixes: list[str] = Field(default_factory=lambda: ["reports"])
    ready: bool
    reasonCode: str | None = None


class ToolObservation(ToolContract):
    tool: ToolName
    path: str
    content: str | None = Field(default=None, max_length=65536)
    contentHash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    entries: list[str] = Field(default_factory=list, max_length=100)
    byteCount: int = Field(default=0, ge=0, le=65536)
    written: bool = False


class AuthorizeToolExecutionRequest(ToolContract):
    commandId: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
    sourceExecutionId: str = Field(min_length=1, max_length=120)
    expectedPlanHash: str = Field(pattern=r"^[a-f0-9]{64}$")
    scope: ToolScope


class ToolArtifact(ToolContract):
    artifactId: str
    executionId: str
    taskId: str
    relativePath: str
    contentHash: str
    byteCount: int
    mediaType: str = "text/plain; charset=utf-8"


class ToolArtifactContent(ToolArtifact):
    content: str


class ToolStepRecord(ToolContract):
    stepIndex: int
    tool: ToolName
    path: str
    status: Literal["pending", "started", "completed", "failed"]
    observation: ToolObservation | None = None
    artifactId: str | None = None
    failureCode: str | None = None


class ToolExecutionResult(ToolContract):
    executionId: str
    sourceExecutionId: str
    sourceTaskId: str
    taskId: str
    runtimeRunId: str
    targetAgentId: str
    workspaceId: str
    planHash: str
    scope: ToolScope
    stage: Literal["preparing", "queued", "running", "completed", "failed", "paused"]
    steps: list[ToolStepRecord]
    artifacts: list[ToolArtifact] = Field(default_factory=list)
    failureCode: str | None = None
    createdAt: datetime
    updatedAt: datetime
    completedAt: datetime | None = None
