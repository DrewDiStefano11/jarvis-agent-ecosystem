"""Deterministic runaway-protection bounds for the autonomy acceptance harness.

Every acceptance scenario executes under :class:`HarnessBounds`. Exceeding a
bound produces a deterministic terminal ``blocked`` outcome with reason code
``bounds_exceeded`` — never an infinite loop, unbounded sleep, or silent hang.
"""

from __future__ import annotations

import time

from pydantic import BaseModel, ConfigDict, Field


class HarnessBounds(BaseModel):
    """Hard limits for one autonomy acceptance run.

    All limits are small by design: acceptance scenarios validate control flow,
    not scale. Each field documents the exact enforcement point in
    :mod:`app.autonomy.harness`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_tasks: int = Field(
        default=8, ge=1, le=64, description="Maximum work-graph nodes per objective."
    )
    max_dependency_depth: int = Field(
        default=4, ge=1, le=16, description="Maximum work-graph dependency chain depth."
    )
    max_attempts_per_node: int = Field(
        default=3, ge=1, le=10, description="Maximum specialist attempts per work node."
    )
    max_node_repairs: int = Field(
        default=1,
        ge=0,
        le=5,
        description="Maximum malformed-output repair retries per attempt.",
    )
    max_model_calls: int = Field(
        default=64, ge=1, le=1024, description="Maximum model/fixture calls per run."
    )
    max_output_chars: int = Field(
        default=20000,
        ge=100,
        le=200000,
        description="Maximum accepted specialist/synthesis output characters.",
    )
    max_timeline_events: int = Field(
        default=500, ge=10, le=5000, description="Maximum recorded timeline events."
    )
    deadline_seconds: float = Field(
        default=60.0, ge=1.0, le=600.0, description="Wall-clock deadline for one run."
    )


DEFAULT_BOUNDS = HarnessBounds()


class BoundsExceededError(RuntimeError):
    """Raised when a harness bound is exceeded.

    Attributes:
        bound: The bound field name that was exceeded (e.g. ``max_model_calls``).
        limit: The configured limit value.
        observed: The observed value that exceeded the limit.
    """

    def __init__(self, bound: str, limit: int | float, observed: int | float) -> None:
        self.bound = bound
        self.limit = limit
        self.observed = observed
        super().__init__(f"harness bound exceeded: {bound} limit={limit} observed={observed}")


class BoundTracker:
    """Mutable consumption ledger for one harness run."""

    def __init__(self, bounds: HarnessBounds, *, monotonic: callable | None = None) -> None:
        self.bounds = bounds
        self._monotonic = monotonic or time.monotonic
        self._started_at = self._monotonic()
        self.model_calls = 0
        self.repairs = 0
        self.attempts = 0

    def elapsed_seconds(self) -> float:
        return self._monotonic() - self._started_at

    def check_deadline(self) -> None:
        elapsed = self.elapsed_seconds()
        if elapsed > self.bounds.deadline_seconds:
            raise BoundsExceededError("deadline_seconds", self.bounds.deadline_seconds, elapsed)

    def record_model_call(self) -> int:
        """Record one model/fixture call; returns the 1-based call index."""
        self.check_deadline()
        self.model_calls += 1
        if self.model_calls > self.bounds.max_model_calls:
            raise BoundsExceededError(
                "max_model_calls", self.bounds.max_model_calls, self.model_calls
            )
        return self.model_calls

    def record_attempt(self) -> int:
        self.check_deadline()
        self.attempts += 1
        return self.attempts

    def record_repair(self) -> int:
        self.repairs += 1
        return self.repairs
