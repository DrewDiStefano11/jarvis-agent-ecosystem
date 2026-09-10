"""Durable coordination projections. Runtime remains the execution authority."""

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.models.decomposition import DecompositionRecord, StrictModel, ready_keys


class CoordinatedSubtask(StrictModel):
    subtaskId: str
    key: str
    assignedAgentId: str
    runtimeRunId: str
    status: Literal["pending", "claimed"] = "pending"
    # This is a preparation reference, not an independently authorized attempt.
    runtimeAttemptId: str | None = None


class CoordinationRecord(StrictModel):
    schemaVersion: Literal["1"] = "1"
    coordinatorVersion: Literal["coordinator-1"] = "coordinator-1"
    id: str
    taskId: str
    decompositionId: str
    decompositionHash: str
    runtimeRunId: str
    status: Literal["active"] = "active"
    nodes: list[CoordinatedSubtask] = Field(min_length=1, max_length=12)
    createdAt: datetime
    updatedAt: datetime


def execution_ready_keys(
    graph: DecompositionRecord,
    *,
    completed: frozenset[str] = frozenset(),
    owned: frozenset[str] = frozenset(),
    eligible_agents: frozenset[str],
    execution_permitted: bool,
) -> list[str]:
    """All independent nodes stay eligible; claims impose the execution bound."""
    if not execution_permitted or graph.status != "ready":
        return []
    eligible = {n.key for n in graph.subtasks if n.assignedAgentId in eligible_agents}
    return [
        key for key in ready_keys(graph.subtasks, completed) if key in eligible and key not in owned
    ]
