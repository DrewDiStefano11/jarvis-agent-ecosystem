from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

from app.core.errors import DomainError
from app.models.domain import Agent, Approval, Artifact, AuditEvent, Department, Notification, Task
from app.services.seed import build_seed


class InMemoryRepository:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        seed = build_seed()
        self.departments = {x["id"]: Department.model_validate(x) for x in seed["departments"]}
        self.agents = {x["id"]: Agent.model_validate(x) for x in seed["agents"]}
        self.tasks = {x["id"]: Task.model_validate(x) for x in seed["tasks"]}
        self.approvals = {x["id"]: Approval.model_validate(x) for x in seed["approvals"]}
        self.artifacts = {x["id"]: Artifact.model_validate(x) for x in seed["artifacts"]}
        self.notifications = {
            x["id"]: Notification.model_validate(x) for x in seed["notifications"]
        }
        self.audit = [AuditEvent.model_validate(x) for x in seed["audit"]]
        self.emergency_stop = False

    @staticmethod
    def require(store: dict[str, object], item_id: str, kind: str) -> object:
        if item_id not in store:
            raise DomainError(f"{kind.upper()}_NOT_FOUND", f"Unknown {kind} ID: {item_id}", 404)
        return store[item_id]

    def add_audit(
        self,
        event_type: str,
        summary: str,
        sequence: int,
        task_id: str | None = None,
        agent_id: str | None = None,
        previous: str | None = None,
        new: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> AuditEvent:
        item = AuditEvent(
            id=f"audit-{uuid4().hex[:10]}",
            timestamp=datetime.now(UTC),
            eventType=event_type,
            actorAgentId=agent_id,
            taskId=task_id,
            previousState=previous,
            newState=new,
            summary=summary,
            correlationId="phase-1-demo",
            sequenceNumber=sequence,
            payload=payload or {},
        )
        self.audit.append(item)
        return item

    def snapshot(self) -> dict[str, object]:
        return deepcopy(
            {
                "departments": list(self.departments.values()),
                "agents": list(self.agents.values()),
                "tasks": list(self.tasks.values()),
                "approvals": list(self.approvals.values()),
                "artifacts": list(self.artifacts.values()),
                "notifications": list(self.notifications.values()),
                "auditEvents": self.audit,
                "emergencyStop": self.emergency_stop,
            }
        )
