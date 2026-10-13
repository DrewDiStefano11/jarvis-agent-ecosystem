"""Authenticated validation measurements; no public importer or execution authority."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from app.models.development_validation import (
    CodeState,
    Contract,
    Digest,
    FailureKind,
    Gate,
    ValidationPlan,
)

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")]


class CommandDefinition(Contract):
    gate: Gate
    adapter: Identifier
    suite_digest: Digest
    policy_fingerprint: Digest
    timeout_seconds: int = Field(strict=True, ge=1, le=3600)
    output_limit_bytes: int = Field(strict=True, ge=1, le=1048576)
    memory_limit_bytes: int = Field(strict=True, ge=1, le=8589934592)


class ExecutionPlan(Contract):
    schema_version: Literal["1.0"] = "1.0"
    code_state: CodeState
    validation: ValidationPlan
    commands: tuple[CommandDefinition, ...] = Field(min_length=1, max_length=9)
    execution_plan_hash: Digest


class ResourceMeasurements(Contract):
    # None means unavailable, never a fabricated zero measurement.
    wall_time_ms: int = Field(strict=True, ge=0, le=86400000)
    cpu_time_ms: int | None = Field(default=None, strict=True, ge=0, le=360000000)
    peak_memory_bytes: int | None = Field(default=None, strict=True, ge=0, le=2**63 - 1)


class ExecutionMeasurement(Contract):
    schema_version: Literal["1.0"] = "1.0"
    execution_plan_hash: Digest
    validation_plan_hash: Digest
    repository_identity: str = Field(
        pattern=r"^github\.com/[a-z0-9][a-z0-9_-]{0,99}/[a-z0-9][a-z0-9._-]{0,99}$"
    )
    code_state_hash: Digest
    command: CommandDefinition
    task_id: Identifier
    runtime_run_id: Identifier
    attempt_id: Identifier
    command_run_id: Identifier
    sequence: int = Field(strict=True, ge=1, le=2**63 - 1)
    started_at: datetime
    finished_at: datetime
    outcome: Literal["passed", "failed", "interrupted", "cancelled", "unmeasured"]
    exit_code: int | None = Field(default=None, strict=True, ge=-2147483648, le=4294967295)
    failure_kind: FailureKind | None = None
    resources: ResourceMeasurements
    stdout_digest: Digest
    stderr_digest: Digest
    output_bytes: int = Field(strict=True, ge=0, le=1048576)
    output_truncated: bool = Field(strict=True)
    # Allowlisted diagnostic codes only: raw logs, paths and model prose never persist.
    diagnostic_codes: tuple[
        Literal[
            "assertion_failed",
            "lint_failed",
            "typecheck_failed",
            "build_failed",
            "migration_failed",
            "dependency_unavailable",
            "timeout",
            "cancelled",
            "interrupted",
            "output_truncated",
            "unknown",
        ],
        ...,
    ] = Field(default=(), max_length=16)

    @field_validator("started_at", "finished_at")
    @classmethod
    def aware_time(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("measurement time requires a timezone")
        return value

    @model_validator(mode="after")
    def coherent_result(self):
        if self.finished_at < self.started_at:
            raise ValueError("measurement time is reversed")
        elapsed = (self.finished_at - self.started_at).total_seconds() * 1000
        if abs(elapsed - self.resources.wall_time_ms) > 1:
            raise ValueError("wall measurement differs from timestamps")
        if self.output_bytes > self.command.output_limit_bytes:
            raise ValueError("retained output exceeds policy")
        if self.outcome == "passed" and (
            self.exit_code != 0
            or self.failure_kind is not None
            or elapsed > self.command.timeout_seconds * 1000
            or (
                self.resources.peak_memory_bytes is not None
                and self.resources.peak_memory_bytes > self.command.memory_limit_bytes
            )
        ):
            raise ValueError("pass requires a successful bounded measured exit")
        if self.outcome == "failed":
            exceeded_policy = elapsed > self.command.timeout_seconds * 1000 or (
                self.resources.peak_memory_bytes is not None
                and self.resources.peak_memory_bytes > self.command.memory_limit_bytes
            )
            if self.failure_kind is None or (self.exit_code == 0 and not exceeded_policy):
                raise ValueError("failed measurement requires explicit classification")
        elif self.failure_kind is not None:
            raise ValueError("only failure has a failure classification")
        if (
            self.outcome in {"interrupted", "cancelled", "unmeasured"}
            and self.exit_code is not None
        ):
            raise ValueError("uncertain results cannot assert an exit")
        return self


class SignedReceipt(Contract):
    collector_id: Identifier
    key_id: Identifier
    measurement: ExecutionMeasurement
    authentication_tag: Digest
