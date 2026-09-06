from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TeamSelectionRationale(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    agentId: str
    rationale: str
    coveredCapabilities: list[str]


class TeamSelectionRecord(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    selectionId: str
    taskId: str
    status: Literal["completed", "blocked_missing_capability"]
    requiredCapabilities: list[str] = Field(default_factory=list)
    optionalCapabilities: list[str] = Field(default_factory=list)
    managerId: str | None = None
    selectedAgentIds: list[str] = Field(default_factory=list)
    rationaleSummaries: list[TeamSelectionRationale] = Field(default_factory=list)
    selectorVersion: str = "1.0"
    workforceFingerprint: str
    createdAt: datetime
    updatedAt: datetime
