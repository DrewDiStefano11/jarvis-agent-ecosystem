"""Explicit stage limits; budgets limit execution but never authorize it."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ExecutionStage = Literal[
    "team_selection",
    "decomposition",
    "planning",
    "specialist_execution",
    "independent_review",
    "synthesis",
    "validation",
    "retry",
    "repair",
    "correction",
]


class StageBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    stage: ExecutionStage
    maximumRequests: int = Field(ge=0, le=32, strict=True)
    maximumInputTokens: int = Field(ge=0, le=65536, strict=True)
    maximumOutputTokens: int = Field(ge=0, le=16384, strict=True)

    @model_validator(mode="after")
    def consistent_caps(self):
        if self.maximumRequests == 0 and (self.maximumInputTokens or self.maximumOutputTokens):
            raise ValueError("Disabled stages must have zero token caps")
        if self.maximumRequests and (self.maximumInputTokens == 0 or self.maximumOutputTokens == 0):
            raise ValueError("Enabled stages require input and output caps")
        return self


class ExecutionBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    maximumRequests: int = Field(ge=1, le=100, strict=True)
    maximumTokens: int = Field(ge=256, le=1_000_000, strict=True)
    maximumAttempts: int = Field(ge=1, le=10, strict=True)
    stages: tuple[StageBudget, ...] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def aggregate_caps(self):
        if len({stage.stage for stage in self.stages}) != len(self.stages):
            raise ValueError("Stage budgets must be unique")
        if sum(stage.maximumRequests for stage in self.stages) > self.maximumRequests:
            raise ValueError("Stage requests exceed aggregate maximum")
        reserved = sum(
            stage.maximumRequests * (stage.maximumInputTokens + stage.maximumOutputTokens)
            for stage in self.stages
        )
        if reserved > self.maximumTokens:
            raise ValueError("Stage reservations exceed aggregate token maximum")
        return self

    def direct_stage(self) -> StageBudget:
        enabled = [stage for stage in self.stages if stage.maximumRequests]
        if (
            self.maximumRequests != 1
            or self.maximumAttempts != 1
            or len(enabled) != 1
            or enabled[0].stage != "planning"
            or enabled[0].maximumRequests != 1
        ):
            raise ValueError("Only a one-request direct planning graph is supported")
        return enabled[0]
