"""Local operator office placement contracts; never agent execution authority."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class OfficeContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OfficePoint(OfficeContract):
    x: float = Field(ge=0, le=8192, allow_inf_nan=False)
    y: float = Field(ge=0, le=5460, allow_inf_nan=False)


class OfficeStation(OfficeContract):
    id: str
    label: str
    roomId: str
    roomName: str
    point: OfficePoint


class OfficeRoute(OfficeContract):
    id: str
    originId: str
    destinationId: str
    points: list[OfficePoint] = Field(min_length=2, max_length=160)
    doorIds: list[str]
    length: float = Field(gt=0, allow_inf_nan=False)


class OfficeCatalog(OfficeContract):
    version: str
    sourceCommit: str
    geometryHash: str
    reviewScope: str
    stations: list[OfficeStation]
    routes: list[OfficeRoute]
    spriteIds: list[str]


class OfficeMotion(OfficeContract):
    originId: str
    destinationId: str
    points: list[OfficePoint] = Field(min_length=2, max_length=320)
    doorIds: list[str]
    startedAt: datetime
    durationMs: int = Field(gt=0)
    stoppedAt: datetime | None = None


class OfficePlacement(OfficeContract):
    identityId: str
    displayName: str
    lifecycleState: str
    enabled: bool
    stationId: str
    spriteId: str
    position: OfficePoint
    motion: OfficeMotion | None
    movementState: Literal["idle", "moving", "stopped"]
    activity: Literal["idle", "queued", "working", "waiting", "failed", "completed"]
    version: int
    updatedAt: datetime


class OfficeSnapshot(OfficeContract):
    serverTime: datetime
    catalog: OfficeCatalog
    placements: list[OfficePlacement]
    placementVersions: dict[str, int]
    emergencyStop: bool


class OfficeCommand(OfficeContract):
    commandId: str = Field(min_length=1, max_length=120, pattern=r"^[a-zA-Z0-9_.:-]+$")
    action: Literal["assign", "move", "stop", "release"]
    expectedVersion: int = Field(ge=0)
    stationId: str | None = Field(default=None, max_length=100)
    spriteId: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def require_fields(self):
        if self.action in {"assign", "move"} and not self.stationId:
            raise ValueError("stationId is required for assignment and movement")
        if self.action == "assign" and not self.spriteId:
            raise ValueError("spriteId is required for assignment")
        if self.action != "assign" and self.spriteId is not None:
            raise ValueError("Only assignment selects a sprite")
        if self.action in {"stop", "release"} and self.stationId is not None:
            raise ValueError("Stop and release do not select a station")
        return self


class OfficeCommandResult(OfficeContract):
    commandId: str
    identityId: str
    version: int
    action: str
