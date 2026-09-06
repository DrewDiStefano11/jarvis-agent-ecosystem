"""Catalog content is data; none of these fields confer runtime authority."""

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.models.identity import IdentityModel

CatalogKind = Literal["agent", "skill", "discovery"]


class RawDefinition(IdentityModel):
    kind: CatalogKind
    path: str = Field(min_length=1, max_length=500)
    text: str = Field(min_length=1, max_length=1_000_000)


class SourceSnapshot(IdentityModel):
    provider: str = Field(pattern=r"^[a-z][a-z0-9-]{1,60}$")
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    license: str = Field(min_length=1, max_length=100)
    license_text: str = Field(min_length=1, max_length=100_000)
    definitions: list[RawDefinition] = Field(max_length=10_000)


class NormalizedDefinition(IdentityModel):
    kind: CatalogKind
    stable_key: str
    display_name: str
    description: str
    role: str
    agent_class: Literal["specialist"] = "specialist"
    capabilities: list[str]
    unmapped_tags: list[str]
    specialties: list[str]
    skill_references: list[str]
    requested_tool_classes: list[str]
    preferred_model_classes: list[str]
    references: list[str]
    applicable_agent_classes: list[str]
    normalized_instructions: str
    warnings: list[str]
    duplicate_key: str


class ImportRequest(IdentityModel):
    source: Literal["wshobson-agents", "voltagent-skills"]
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    dry_run: bool = True


class ImportReport(IdentityModel):
    discovered: int = 0
    valid: int = 0
    invalid: int = 0
    duplicates: int = 0
    new: int = 0
    changed: int = 0
    unchanged: int = 0
    agents: int = 0
    skills: int = 0
    discoveries: int = 0
    warnings: list[str] = Field(default_factory=list)
    dry_run: bool


class ReviewRequest(IdentityModel):
    revision_id: str = Field(min_length=1, max_length=80)
    approved: bool
    reason: str = Field(min_length=1, max_length=500)


class ActivateRequest(IdentityModel):
    revision_id: str = Field(min_length=1, max_length=80)


class CatalogSummary(IdentityModel):
    id: str
    kind: CatalogKind
    stable_key: str
    display_name: str
    description: str
    role: str
    capabilities: list[str]
    source_repository: str
    source_commit: str
    source_path: str
    source_license: str
    revision_id: str
    review_status: str
    trust_status: Literal["external_untrusted"] = "external_untrusted"
    enabled: bool
    duplicate_of: str | None
    identity_id: str | None
    active_revision_id: str | None
    update_available: bool
    operational_status: str | None
    lifecycle_state: str | None
    runtime_enabled: bool | None
    warnings: list[str]


class CatalogDetail(CatalogSummary):
    normalized: NormalizedDefinition
    original_definition: str
    source_hash: str
    parser_version: str
    license_text: str
    imported_at: datetime
    revisions: list[str]


class CatalogPage(IdentityModel):
    items: list[CatalogSummary]
    offset: int
    limit: int
    total: int


class CatalogSourceView(IdentityModel):
    id: str
    provider: str
    repository: str
    commit: str
    license: str
    imported_at: datetime
    imported_count: int


class CapabilityView(IdentityModel):
    key: str
    parent: str | None
