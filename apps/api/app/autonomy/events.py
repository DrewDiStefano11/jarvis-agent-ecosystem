"""Structured observability for autonomy acceptance runs (Workstream C).

The :class:`EventRecorder` is the single timeline feed for a harness run. Every
entry is bounded, JSON-serializable, and scrubbed of secrets before it is
stored, so timelines are safe to persist as evidence and to render for humans.

Recorded dimensions include: stage, event name, node id, attempt number,
dependency readiness, timestamps, retry/failure reasons, synthesis readiness,
task outcome, durable checkpoint ids, recovery occurrences, and
tool-authorization-relevant state transitions — without ever recording secrets,
credentials, private model reasoning, sensitive headers, unrestricted paths, or
raw provider exception internals.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.autonomy.ports import StageKind
from app.context.security import redact_sensitive_data
from app.model_providers.security import redact_secrets

MAX_DETAIL_KEYS = 16
MAX_DETAIL_VALUE_CHARS = 2000
MAX_EVENT_NAME_LENGTH = 64


class TimelineEvent(BaseModel):
    """One immutable, redacted observability entry."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    seq: int = Field(ge=1)
    timestamp: datetime
    stage: StageKind
    event: str = Field(min_length=1, max_length=MAX_EVENT_NAME_LENGTH)
    node_id: str | None = Field(default=None, max_length=80)
    attempt_number: int | None = Field(default=None, ge=1)
    detail: dict[str, Any] = Field(default_factory=dict)


def scrub_text(value: str) -> str:
    """Redact secret-bearing and sensitive substrings from free text."""
    redacted, _ = redact_sensitive_data(redact_secrets(value))
    return redacted


def scrub_detail(detail: Mapping[str, Any]) -> dict[str, Any]:
    """Bound and scrub an event detail mapping.

    Rules (documented, deterministic):

    - At most ``MAX_DETAIL_KEYS`` keys are kept (alphabetical, first N).
    - Strings are secret-scrubbed and truncated to ``MAX_DETAIL_VALUE_CHARS``.
    - Only JSON scalar/sequence/mapping values survive; anything else is
      replaced by its type name so provider exception internals can never leak.
    - Mapping keys containing secret terms are dropped entirely.
    """
    scrubbed: dict[str, Any] = {}
    for key in sorted(detail.keys(), key=str)[:MAX_DETAIL_KEYS]:
        name = str(key)
        lowered = name.lower().replace("-", "_")
        if any(
            term in lowered
            for term in ("api_key", "authorization", "token", "password", "secret", "credential")
        ):
            continue
        scrubbed[name] = _scrub_value(detail[key], depth=0)
    return scrubbed


def _scrub_value(value: Any, *, depth: int) -> Any:
    if depth > 4:
        return "<truncated>"
    if isinstance(value, str):
        return scrub_text(value)[:MAX_DETAIL_VALUE_CHARS]
    if isinstance(value, bool | int | float | type(None)):
        return value
    if isinstance(value, Mapping):
        return {str(key)[:80]: _scrub_value(item, depth=depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub_value(item, depth=depth + 1) for item in value][:32]
    return f"<{type(value).__name__}>"


class EventRecorder:
    """Bounded, sequential, redacted timeline recorder."""

    def __init__(self, *, max_events: int, clock: Any = None) -> None:
        self._max_events = max(1, max_events)
        self._clock = clock
        self._events: list[TimelineEvent] = []
        self.dropped = 0

    @property
    def events(self) -> tuple[TimelineEvent, ...]:
        return tuple(self._events)

    def _now(self) -> datetime:
        if self._clock is not None:
            return self._clock.now()
        return datetime.now(UTC)

    def record(
        self,
        stage: StageKind,
        event: str,
        *,
        node_id: str | None = None,
        attempt_number: int | None = None,
        detail: Mapping[str, Any] | None = None,
    ) -> TimelineEvent | None:
        """Record one timeline entry; drops (counted) beyond the bound."""
        if len(self._events) >= self._max_events:
            self.dropped += 1
            return None
        entry = TimelineEvent(
            seq=len(self._events) + 1,
            timestamp=self._now(),
            stage=stage,
            event=event[:MAX_EVENT_NAME_LENGTH],
            node_id=node_id,
            attempt_number=attempt_number,
            detail=scrub_detail(detail or {}),
        )
        self._events.append(entry)
        return entry

    def of_event(self, *names: str) -> tuple[TimelineEvent, ...]:
        wanted = set(names)
        return tuple(entry for entry in self._events if entry.event in wanted)

    def last(self) -> TimelineEvent | None:
        return self._events[-1] if self._events else None
