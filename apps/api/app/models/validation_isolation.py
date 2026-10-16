"""Provisioning and transfer contracts; declarations never attest isolation."""

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.core.errors import DomainError
from app.development_validation.planner import digest, ignored_output
from app.models.development_validation import Contract, Digest
from app.models.source_change import source_path
from app.models.validation_execution import Identifier
from app.models.validation_journal import JournalOwner


class IsolationLimits(Contract):
    memory_bytes: int = Field(strict=True, ge=2147483648, le=8589934592)
    cpu_count: int = Field(strict=True, ge=1, le=4)
    disk_bytes: int = Field(strict=True, ge=1073741824, le=68719476736)
    process_count: int = Field(strict=True, ge=1, le=256)
    wall_seconds: int = Field(strict=True, ge=1, le=3600)
    output_bytes: int = Field(strict=True, ge=1, le=1048576)
    concurrency: int = Field(default=1, strict=True, ge=1, le=4)


class IsolationProvisioning(Contract):
    backend: Literal["windows_sandbox", "hyper_v_vm"]
    image_digest: Digest
    dependency_digest: Digest
    collector_identity: Identifier
    limits: IsolationLimits
    additional_spending_cents: Literal[0] = 0
    existing_image_license_confirmed: Literal[True]
    external_network: Literal[False] = False
    host_network: Literal[False] = False
    host_credentials: Literal[False] = False
    clipboard: Literal[False] = False
    devices: Literal[False] = False
    writable_host_mounts: Literal[False] = False
    enhanced_session: Literal[False] = False
    # These describe the requested configuration, not verified host observations.
    execution_authorized: Literal[False] = False


class CapsuleFile(Contract):
    path: str
    mode: Literal["100644", "100755"]
    content_digest: Digest
    byte_count: int = Field(strict=True, ge=0, le=8388608)

    @field_validator("path")
    @classmethod
    def safe_path(cls, value):
        try:
            source_path(value)
        except DomainError:
            raise ValueError("CAPSULE_PATH_DENIED") from None
        if ignored_output(value) or any(
            part.casefold().startswith(".jarvis-") for part in value.split("/")
        ):
            raise ValueError("CAPSULE_PRIVATE_OR_GENERATED_PATH")
        return value


class SourceCapsule(Contract):
    schema_version: Literal["1.0"] = "1.0"
    owner: JournalOwner
    journal_digest: Digest
    execution_plan_hash: Digest
    code_state_hash: Digest
    # Independently measured complete native inventory, not a caller's subset.
    files: tuple[CapsuleFile, ...] = Field(min_length=1, max_length=4096)
    inventory_digest: Digest
    capsule_digest: Digest
    execution_authorized: Literal[False] = False

    @model_validator(mode="after")
    def complete_inventory(self):
        names = [item.path for item in self.files]
        folded = {name.casefold() for name in names}
        if names != sorted(names) or len(folded) != len(names):
            raise ValueError("CAPSULE_PATH_COLLISION")
        if any(
            "/".join(name.casefold().split("/")[:index]) in folded
            for name in names
            for index in range(1, len(name.split("/")))
        ):
            raise ValueError("CAPSULE_FILE_DIRECTORY_COLLISION")
        if sum(item.byte_count for item in self.files) > 134217728:
            raise ValueError("CAPSULE_TOTAL_LIMIT")
        if digest([item.model_dump(mode="json") for item in self.files]) != self.inventory_digest:
            raise ValueError("CAPSULE_INVENTORY_MISMATCH")
        if digest(self.model_dump(mode="json", exclude={"capsule_digest"})) != self.capsule_digest:
            raise ValueError("CAPSULE_DIGEST_MISMATCH")
        return self


class IsolationLifecycle(Contract):
    schema_version: Literal["1.0"] = "1.0"
    isolation_id: Identifier
    owner: JournalOwner
    journal_digest: Digest
    capsule_digest: Digest
    provisioning_digest: Digest
    sequence: int = Field(strict=True, ge=1, le=64)
    phase: Literal[
        "requested",
        "provisioned",
        "transferred",
        "started",
        "stopped",
        "uncertain",
        "cleanup_requested",
        "destroyed",
    ]
    execution_authorized: Literal[False] = False


class HostCapacity(Contract):
    host_identity: Identifier
    deployment_role: Literal["development", "production", "unclassified"]
    observed_at: datetime
    physical_memory_bytes: int = Field(strict=True, ge=1)
    available_memory_bytes: int = Field(strict=True, ge=0)
    logical_cpu_count: int = Field(strict=True, ge=1, le=1024)
    available_cpu_count: int = Field(strict=True, ge=0, le=1024)
    available_storage_bytes: int = Field(strict=True, ge=0)
    hypervisor_present: bool = Field(strict=True)
    firmware_virtualization: bool | None = Field(default=None, strict=True)
    slat: bool | None = Field(default=None, strict=True)
    hyper_v_feature: Literal["enabled", "disabled", "unknown"]
    sandbox_feature: Literal["enabled", "disabled", "unknown"]
    required_services_available: bool = Field(strict=True)

    @model_validator(mode="after")
    def coherent_capacity(self):
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("HOST_CAPACITY_TIME_INVALID")
        if (
            self.available_memory_bytes > self.physical_memory_bytes
            or self.available_cpu_count > self.logical_cpu_count
        ):
            raise ValueError("HOST_CAPACITY_INVALID")
        return self


class HostAdmission(Contract):
    host_identity: Identifier
    deployment_role: Literal["development", "production", "unclassified"]
    provisioning_digest: Digest
    blockers: tuple[str, ...] = Field(max_length=16)
    execution_authorized: Literal[False] = False
