from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class Metadata(BaseModel):
    id: str
    name: str
    version: str
    department: str
    status: str


class AgentSpec(BaseModel):
    parent: str | None
    role: str
    description: str
    goals: list[str] = Field(min_length=1)
    capabilities: list[str] = Field(min_length=1)
    allowedTools: list[str]
    deniedTools: list[str]
    memoryAccess: dict[str, Any]
    approvalPolicy: dict[str, Any]
    execution: dict[str, Any]
    review: dict[str, Any]
    deployment: dict[str, Any]
    office: dict[str, Any]


class AgentManifest(BaseModel):
    kind: str
    apiVersion: str
    metadata: Metadata
    spec: AgentSpec


def load_manifest(path: Path) -> AgentManifest:
    with path.open(encoding="utf-8") as handle:
        return AgentManifest.model_validate(yaml.safe_load(handle))
