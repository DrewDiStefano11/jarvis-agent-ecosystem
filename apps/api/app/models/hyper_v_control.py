"""Native VM management observations. VM counters never attest a guest test exit."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.models.development_validation import Contract, Digest
from app.models.validation_execution import Identifier


class HyperVBinding(Contract):
    host_identity: Identifier
    host_machine_guid: UUID
    vm_id: UUID
    ownership_digest: Digest
    provisioning_digest: Digest
    capsule_digest: Digest


class HyperVObservation(Contract):
    binding: HyperVBinding
    observed_at: datetime
    state: Literal["off", "running", "paused", "saved", "other"]
    memory_assigned_bytes: int = Field(strict=True, ge=0)
    memory_startup_bytes: int = Field(strict=True, ge=1)
    cpu_count: int = Field(strict=True, ge=1, le=1024)
    host_vm_uptime_seconds: int = Field(strict=True, ge=0)
    host_cpu_usage_percent: int = Field(strict=True, ge=0, le=100)
    connected_network_adapters: int = Field(strict=True, ge=0)
    checkpoint_count: int = Field(strict=True, ge=0)
    dynamic_memory: bool = Field(strict=True)
    automatic_start: Literal["nothing", "other"]
    # Outside-guest VM state/resources are measured, not a guest command outcome.
    command_outcome: Literal["unmeasured"] = "unmeasured"
    execution_authorized: Literal[False] = False

    @model_validator(mode="after")
    def aware(self):
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("HYPERV_OBSERVATION_TIME_INVALID")
        return self
