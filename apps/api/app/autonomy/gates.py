"""Authoritative execution gates: cancellation and emergency stop.

The :class:`ExecutionGate` is the harness-side operator control. It is checked
before every durable execution boundary (starting a node, beginning an
attempt, recording a checkpoint). A closed gate refuses new work with a
machine-readable reason code; previously committed durable state remains
inspectable and is never mutated by the gate itself.

Cancellation and emergency stop are distinct:

- ``request_cancellation`` models an operator cancelling the objective. The
  harness finalizes in-flight attempts through the real runtime cancellation
  command chain and reaches terminal ``cancelled``.
- ``activate_emergency_stop`` models the platform emergency stop. The harness
  refuses all further autonomous execution immediately and reaches terminal
  ``blocked`` with reason ``emergency_stop``. The stop cannot be cleared by the
  harness or by model output — only by constructing a new gate (i.e. an
  explicit operator reset outside the autonomous loop).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


class GateClosedError(RuntimeError):
    """Raised when a closed gate refuses a durable execution boundary."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {detail}")


@dataclass(frozen=True)
class GateState:
    cancelled: bool
    emergency_stopped: bool
    reason_code: str | None
    detail: str | None
    changed_at: datetime | None


class ExecutionGate:
    """In-memory authoritative operator control for one harness run."""

    def __init__(self) -> None:
        self._cancelled = False
        self._emergency_stopped = False
        self._reason_code: str | None = None
        self._detail: str | None = None
        self._changed_at: datetime | None = None

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    @property
    def emergency_stopped(self) -> bool:
        return self._emergency_stopped

    def snapshot(self) -> GateState:
        return GateState(
            cancelled=self._cancelled,
            emergency_stopped=self._emergency_stopped,
            reason_code=self._reason_code,
            detail=self._detail,
            changed_at=self._changed_at,
        )

    def request_cancellation(self, reason_code: str, detail: str) -> None:
        """Record an operator cancellation request (idempotent, first wins)."""
        if self._emergency_stopped or self._cancelled:
            return
        self._cancelled = True
        self._reason_code = reason_code
        self._detail = detail
        self._changed_at = datetime.now(UTC)

    def activate_emergency_stop(self, reason_code: str, detail: str) -> None:
        """Activate the emergency stop (idempotent, first wins, cannot clear)."""
        if self._emergency_stopped:
            return
        self._emergency_stopped = True
        self._reason_code = reason_code
        self._detail = detail
        self._changed_at = datetime.now(UTC)

    def check(self, boundary: str) -> None:
        """Refuse the named durable boundary while the gate is closed."""
        if self._emergency_stopped:
            raise GateClosedError(
                "emergency_stop",
                f"emergency stop refuses {boundary} ({self._reason_code})",
            )
        if self._cancelled:
            raise GateClosedError(
                "cancelled",
                f"cancellation refuses {boundary} ({self._reason_code})",
            )
