"""Versioned planned work only: assignment is never execution authorization."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.catalog.taxonomy import CAPABILITIES

Text = Annotated[str, Field(min_length=3, max_length=1200)]
Key = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProposedSubtask(StrictModel):
    key: Key
    title: Annotated[str, Field(min_length=3, max_length=160)]
    description: Text
    requiredCapabilities: list[str] = Field(min_length=1, max_length=12)
    dependsOn: list[Key] = Field(default_factory=list, max_length=11)
    preferredAgentId: str | None = Field(default=None, max_length=80)
    deliverable: Text
    outputType: Literal[
        "structured_research",
        "analysis",
        "recommendation",
        "code_patch",
        "document",
        "dataset",
        "browser_findings",
        "decision_input",
    ]
    completionCriteria: list[Text] = Field(min_length=1, max_length=8)

    @field_validator("requiredCapabilities")
    @classmethod
    def canonical_capabilities(cls, value):
        if set(value) - CAPABILITIES:
            raise ValueError("Unknown normalized capability")
        return sorted(set(value))

    @field_validator("dependsOn")
    @classmethod
    def unique_dependencies(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("Duplicate dependencies")
        return sorted(value)


class DecompositionProposal(StrictModel):
    schemaVersion: Literal["1"] = "1"
    objectiveSummary: Text
    subtasks: list[ProposedSubtask] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def validate_graph(self):
        topological_keys(self.subtasks)
        return self


def topological_keys(nodes: list[ProposedSubtask]) -> list[str]:
    """Stable Kahn ordering, with at most six dependency levels."""
    by_key = {node.key: node for node in nodes}
    if len(by_key) != len(nodes):
        raise ValueError("Duplicate subtask keys")
    for node in nodes:
        if node.key in node.dependsOn or set(node.dependsOn) - by_key.keys():
            raise ValueError("Self or unknown dependency")
    result, depths = [], {}
    while len(result) < len(nodes):
        eligible = sorted(
            k for k, n in by_key.items() if k not in depths and set(n.dependsOn) <= depths.keys()
        )
        if not eligible:
            raise ValueError("Dependency cycle")
        key = eligible[0]
        depths[key] = 1 + max((depths[d] for d in by_key[key].dependsOn), default=0)
        if depths[key] > 6:
            raise ValueError("Dependency depth exceeds six levels")
        result.append(key)
    return result


class PlannedSubtask(ProposedSubtask):
    id: str
    parentTaskId: str
    assignedAgentId: str
    assignedAgentName: str
    assignmentRationale: str
    order: int
    status: Literal["ready", "blocked", "pending"]


class DecompositionIssue(StrictModel):
    code: str
    message: str
    affectedSubtasks: list[str] = Field(default_factory=list)
    requiredCapabilities: list[str] = Field(default_factory=list)


class DecompositionRecord(StrictModel):
    schemaVersion: Literal["1"] = "1"
    promptVersion: Literal["decomposer-1"] = "decomposer-1"
    id: str
    taskId: str
    version: int
    status: Literal[
        "ready",
        "needs_team_reselection",
        "failed",
        "unsupported",
        "superseded",
        "needs_redecomposition",
    ]
    teamSelectionId: str | None
    inputFingerprint: str
    contextAssemblyId: str | None
    objectiveSummary: str = ""
    provider: str | None = None
    model: str | None = None
    requestCount: int = 0
    subtasks: list[PlannedSubtask] = Field(default_factory=list, max_length=12)
    issues: list[DecompositionIssue] = Field(default_factory=list)
    operatorProtected: bool = False
    createdAt: datetime
    supersededBy: str | None = None


def ready_keys(nodes: list[PlannedSubtask], satisfied: frozenset[str] = frozenset()) -> list[str]:
    """Dependencies consume the named upstream deliverables, never all prior history."""
    return [
        key
        for key in topological_keys(nodes)
        if key not in satisfied
        and set(next(n for n in nodes if n.key == key).dependsOn) <= satisfied
    ]


class DecompositionRequest(StrictModel):
    contextAssemblyId: str | None = Field(default=None, max_length=80)
