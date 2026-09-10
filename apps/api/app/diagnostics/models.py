"""Data model for the operator-facing Jarvis runtime doctor.

The doctor is read-mostly diagnostics: every check returns a bounded,
secret-free result that can be rendered as human text, JSON, or Markdown
without post-processing trust.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

MAX_IDENTIFIER_LENGTH = 200


class CheckStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    BLOCKED = "blocked"
    DISABLED = "disabled"
    UNKNOWN = "unknown"


#: Statuses that describe a condition preventing normal operation.
BLOCKING_STATUSES = frozenset({CheckStatus.BLOCKED})
#: Statuses that describe partial loss of reliability or visibility.
DEGRADED_STATUSES = frozenset({CheckStatus.DEGRADED, CheckStatus.UNKNOWN})


def overall_status(checks: Sequence[CheckResult]) -> CheckStatus:
    """Aggregate check statuses without treating disabled features as failures."""

    for result in checks:
        if result.status in BLOCKING_STATUSES:
            return CheckStatus.BLOCKED
    for result in checks:
        if result.status in DEGRADED_STATUSES:
            return CheckStatus.DEGRADED
    return CheckStatus.HEALTHY


def _bounded_identifier(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _bounded_identifier(item) for key, item in list(value.items())[:32]}
    if isinstance(value, (list, tuple)):
        return [_bounded_identifier(item) for item in list(value)[:32]]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return (
            value
            if len(value) <= MAX_IDENTIFIER_LENGTH
            else value[: MAX_IDENTIFIER_LENGTH - 3] + "..."
        )
    return str(value)[:MAX_IDENTIFIER_LENGTH]


@dataclass(frozen=True)
class CheckResult:
    """One doctor check outcome.

    ``identifiers`` carries only allowlisted, non-secret scalar facts such as
    revisions, ports, counts, and state names. Structured report rendering
    trusts these fields; callers must never place secrets here.
    """

    name: str
    group: str
    status: CheckStatus
    reason: str
    remediation: str | None = None
    identifiers: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "group": self.group,
            "status": self.status.value,
            "reason": self.reason,
            "remediation": self.remediation,
            "identifiers": {
                key: _bounded_identifier(value) for key, value in self.identifiers.items()
            },
        }


@dataclass(frozen=True)
class DiagnosticReport:
    """The complete, secret-free diagnostic snapshot for one doctor run."""

    overall: CheckStatus
    mode: str
    generated_at: str
    repository: str
    git_sha: str | None
    git_dirty: bool | None
    app_version: str | None
    app_env: str
    api_endpoint: str | None
    web_endpoint: str | None
    database_revision: str | None
    checks: tuple[CheckResult, ...] = field(default_factory=tuple)
    ready_to_run: bool | None = None
    blocked_reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def status(self) -> str:
        return self.overall.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "overall": self.overall.value,
            "mode": self.mode,
            "generatedAt": self.generated_at,
            "repository": self.repository,
            "gitSha": self.git_sha,
            "gitDirty": self.git_dirty,
            "appVersion": self.app_version,
            "appEnv": self.app_env,
            "apiEndpoint": self.api_endpoint,
            "webEndpoint": self.web_endpoint,
            "databaseRevision": self.database_revision,
            "readyToRun": self.ready_to_run,
            "blockedReasons": list(self.blocked_reasons),
            "checks": [result.to_dict() for result in self.checks],
        }

    def check(self, name: str) -> CheckResult | None:
        for result in self.checks:
            if result.name == name:
                return result
        return None
