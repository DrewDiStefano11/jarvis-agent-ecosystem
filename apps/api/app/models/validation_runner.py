"""Fixed invocation descriptions, not command-execution permissions."""

from typing import Literal

from pydantic import Field, field_validator

from app.models.development_validation import Contract, Digest, Gate
from app.models.validation_execution import CommandDefinition


class InvocationDescription(Contract):
    program: Literal["python", "pnpm", "native_repository_integrity"]
    working_directory: Literal[".", "apps/api", "apps/web"]
    arguments: tuple[str, ...] = Field(max_length=140)
    environment: tuple[tuple[Literal["JARVIS_SMOKE_BROWSER"], Literal["true"]], ...] = Field(
        default=(), max_length=1
    )

    @field_validator("arguments")
    @classmethod
    def bounded_arguments(cls, values):
        if any(
            not 1 <= len(v) <= 240 or any(ord(c) < 32 or ord(c) == 127 for c in v) for v in values
        ):
            raise ValueError("invalid fixed invocation argument")
        return values


class GateInvocation(Contract):
    gate: Gate
    execution_plan_hash: Digest
    command: CommandDefinition
    # The gate budget covers the entire sequence, including setup and cleanup.
    invocations: tuple[InvocationDescription, ...] = Field(min_length=1, max_length=2)
    invocation_digest: Digest


class RunnerAvailability(Contract):
    enabled: Literal[False] = False
    isolation_implemented: Literal[False] = False
    reason: Literal["verified_execution_isolation_unavailable"] = (
        "verified_execution_isolation_unavailable"
    )
    gates: tuple[Gate, ...] = Field(min_length=9, max_length=9)
