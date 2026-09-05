from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.core.errors import DomainError
from app.models.domain import CreateTaskRequest, Task


def validate_correction_source(source: Task) -> None:
    if source.status not in {"under_review", "failed", "cancelled", "completed"}:
        raise DomainError(
            "TASK_CORRECTION_NOT_ALLOWED",
            "Corrections require a task under review, failed, cancelled, or completed.",
            409,
        )


def prepare_task_creation(body: CreateTaskRequest, source: Task | None = None) -> Task:
    """Prepare fresh operator work without mutating the source or its execution."""

    if body.correctionOfTaskId is not None:
        if source is None or source.id != body.correctionOfTaskId:
            raise DomainError("TASK_NOT_FOUND", "The correction source task was not found.", 404)
        validate_correction_source(source)
    now = datetime.now(UTC)
    return Task(
        id=f"task-{uuid4().hex[:10]}",
        title=body.title,
        description=body.description,
        request=body.description,
        correctionOfTaskId=body.correctionOfTaskId,
        projectId=source.projectId if source is not None else None,
        createdBy="local-user",
        assignedManagerId="jarvis",
        priority=body.priority,
        createdAt=now,
        updatedAt=now,
    )
