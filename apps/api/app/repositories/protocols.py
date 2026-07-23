from __future__ import annotations

from typing import Protocol


class Repository(Protocol):
    departments: dict[str, object]
    agents: dict[str, object]
    tasks: dict[str, object]
    approvals: dict[str, object]
    artifacts: dict[str, object]
    notifications: dict[str, object]
    audit: list[object]
    emergency_stop: bool

    def persist(self) -> None: ...
    def snapshot(self) -> dict[str, object]: ...
