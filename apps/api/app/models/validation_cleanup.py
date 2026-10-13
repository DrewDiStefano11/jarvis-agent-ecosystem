"""Protected native creation registrations, never candidate deletion authority."""

from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.models.development_validation import Contract, Digest
from app.models.hyper_v_control import HyperVBinding
from app.models.validation_journal import JournalOwner
from app.tool_execution.filesystem import relative_parts


class StorageIdentity(Contract):
    volume_id: int = Field(strict=True, ge=0)
    file_id: int = Field(strict=True, ge=1)


class OwnedStorageEntry(Contract):
    path: str
    kind: Literal["file", "directory"]
    identity: StorageIdentity

    @field_validator("path")
    @classmethod
    def safe(cls, value):
        segments = relative_parts(value)
        if not segments or len(segments) > 8:
            raise ValueError("CLEANUP_PATH_INVALID")
        return value


class StorageRegistration(Contract):
    binding: HyperVBinding
    owner: JournalOwner
    journal_digest: Digest
    # Native IDs are recorded at creation in protected durable storage. A JSON
    # supplied by candidate code is not a registration, even with valid hashes.
    dedicated_root_identity: StorageIdentity
    allocation_identity: StorageIdentity
    allocation_name: str = Field(pattern=r"^jarvis-validation-[a-f0-9]{32}$")
    service_sid: str = Field(max_length=184, pattern=r"^S-1-5-(?:[0-9]+-){0,14}[0-9]+$")
    entries: tuple[OwnedStorageEntry, ...] = Field(max_length=128)
    execution_authorized: Literal[False] = False

    @model_validator(mode="after")
    def inventory(self):
        names = [item.path for item in self.entries]
        folded = {name.casefold() for name in names}
        if names != sorted(names) or len(folded) != len(names):
            raise ValueError("CLEANUP_INVENTORY_INVALID")
        by_name = {item.path: item for item in self.entries}
        for name in names:
            parts = name.split("/")
            for index in range(1, len(parts)):
                parent = by_name.get("/".join(parts[:index]))
                if parent is None or parent.kind != "directory":
                    raise ValueError("CLEANUP_PARENT_UNREGISTERED")
        return self
