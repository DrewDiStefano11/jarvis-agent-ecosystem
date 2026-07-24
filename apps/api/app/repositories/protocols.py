from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from app.models.context import ContextAssemblerStatus, ContextAssembly

if TYPE_CHECKING:
    from app.repositories.sqlalchemy import IdempotencyResult


class Repository(Protocol):
    departments: dict[str, object]
    agents: dict[str, object]
    tasks: dict[str, object]
    approvals: dict[str, object]
    artifacts: dict[str, object]
    context_assemblies: dict[str, ContextAssembly]
    notifications: dict[str, object]
    audit: list[object]
    emergency_stop: bool

    def persist(self) -> None: ...
    def snapshot(self) -> dict[str, object]: ...
    def context_assembler_status(self) -> ContextAssemblerStatus: ...
    def complete_idempotency(self, result: IdempotencyResult) -> None: ...
