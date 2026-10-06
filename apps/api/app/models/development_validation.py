"""Exact software-state validation contracts; model output is not test evidence."""

import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Commit = Annotated[str, Field(pattern=r"^[a-f0-9]{40}$")]
Gate = Literal[
    "backend_ruff",
    "backend_tests",
    "frontend_typecheck",
    "frontend_lint",
    "frontend_tests",
    "frontend_build",
    "migrations",
    "repository_integrity",
    "browser_acceptance",
]
FailureKind = Literal[
    "product",
    "test",
    "infrastructure",
    "ci_environment",
    "watchdog",
    "dependency",
    "migration",
    "lint_type_build",
    "unknown",
]


def checked_path(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 240 or value != value.strip():
        raise ValueError("invalid repository-relative path")
    if (
        "\\" in value
        or ":" in value
        or value.startswith("/")
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise ValueError("invalid repository-relative path")
    for part in value.split("/"):
        if (
            part in {"", ".", ".."}
            or part.endswith((".", " "))
            or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part)
        ):
            raise ValueError("invalid repository-relative path")
    return value


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class FileChange(Contract):
    path: str
    before_hash: Digest | None
    after_hash: Digest | None

    _path = field_validator("path", mode="before")(checked_path)

    @model_validator(mode="after")
    def actual_change(self):
        if self.before_hash == self.after_hash:
            raise ValueError("file change must have different before/after hashes")
        return self


class CodeState(Contract):
    repository_identity: str = Field(
        pattern=r"^github\.com/[a-z0-9][a-z0-9_-]{0,99}/[a-z0-9][a-z0-9._-]{0,99}$"
    )
    head_sha: Commit
    base_sha: Commit
    # Authoritative adapter must include tracked, staged and relevant untracked deltas.
    changes: tuple[FileChange, ...] = Field(max_length=512)

    @field_validator("changes")
    @classmethod
    def canonical_changes(cls, values):
        paths = [value.path for value in values]
        # Case-folding prevents cross-platform conflicting file authority.
        if len(set(path.casefold() for path in paths)) != len(paths):
            raise ValueError("duplicate file paths")
        return tuple(sorted(values, key=lambda item: item.path))


class ValidationGate(Contract):
    gate: Gate
    reason: str = Field(min_length=1, max_length=300)
    targets: tuple[str, ...] = Field(default=(), max_length=128)
    scope_fingerprint: Digest

    @field_validator("targets")
    @classmethod
    def bounded_targets(cls, values):
        for value in values:
            checked_path(value)
            if not value.startswith("apps/api/tests/test_") or not value.endswith(".py"):
                raise ValueError("only explicit backend test files are targets")
        return tuple(sorted(set(values)))


class ValidationDerivation(Contract):
    affected_backend_tests: tuple[str, ...] = Field(default=(), max_length=128)
    browser_required: bool = Field(default=False, strict=True)

    @field_validator("affected_backend_tests")
    @classmethod
    def bounded_targets(cls, values):
        return ValidationGate.bounded_targets(values)


class ValidationPlan(Contract):
    schema_version: Literal["1.0"] = "1.0"
    policy_digest: Digest
    code_state_hash: Digest
    boundary: Literal["iteration", "publication"]
    derivation: ValidationDerivation
    gates: tuple[ValidationGate, ...] = Field(min_length=1, max_length=9)
    blocked_paths: tuple[str, ...] = Field(default=(), max_length=512)
    plan_hash: Digest


class ValidationEvidence(Contract):
    # Emitted only by a future trusted command journal; there is no model/API importer.
    schema_version: Literal["1.0"] = "1.0"
    command_run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
    gate: Gate
    policy_digest: Digest
    code_state_hash: Digest
    scope_fingerprint: Digest
    targets: tuple[str, ...] = Field(default=(), max_length=128)
    outcome: Literal["passed", "failed", "cancelled", "unmeasured"]
    finished_at: datetime
    failure_kind: FailureKind | None = None

    @field_validator("finished_at")
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None:
            raise ValueError("validation time requires a timezone")
        return value

    @model_validator(mode="after")
    def failure_classification(self):
        if self.outcome == "failed" and self.failure_kind is None:
            raise ValueError("failed evidence requires explicit classification")
        if self.outcome != "failed" and self.failure_kind is not None:
            raise ValueError("only failed evidence has a failure classification")
        return self


class ValidationAssessment(Contract):
    ready: bool
    missing: tuple[Gate, ...]
    failed: tuple[Gate, ...]
    stale: tuple[Gate, ...]
    blocked_paths: tuple[str, ...]
