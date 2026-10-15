"""Advisory repair evidence; never execution or source mutation authority."""

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from app.models.development_validation import Commit, Contract, Digest, FailureKind, Gate
from app.models.validation_execution import DiagnosticCode, Identifier


class ReproductionReference(Contract):
    repository_identity: str
    head_sha: Commit
    base_sha: Commit
    code_state_hash: Digest
    execution_plan_hash: Digest
    validation_plan_hash: Digest
    suite_digest: Digest
    command_policy_fingerprint: Digest
    task_id: Identifier
    runtime_run_id: Identifier
    attempt_id: Identifier
    command_run_id: Identifier
    receipt_digest: Digest
    stdout_digest: Digest
    stderr_digest: Digest


class RepairFinding(Contract):
    gate: Gate
    disposition: Literal[
        "passed", "failed", "missing", "stale", "interrupted", "cancelled", "unmeasured"
    ]
    failure_kind: FailureKind | None = None
    diagnostic_codes: tuple[DiagnosticCode, ...] = Field(default=(), max_length=16)
    reproduction: ReproductionReference | None = None
    recommendation: Literal[
        "none",
        "gather_evidence",
        "revalidate",
        "diagnose_failure",
        "request_bounded_retry",
        "stop_retrying",
    ]
    supporting_receipt_digests: tuple[Digest, ...] = Field(default=(), max_length=512)
    retries_remaining: int = Field(strict=True, ge=0, le=2)


class RepairEvidence(Contract):
    schema_version: Literal["1.0"] = "1.0"
    execution_plan_hash: Digest
    code_state_hash: Digest
    observed_at: datetime
    source_changed: bool
    findings: tuple[RepairFinding, ...] = Field(min_length=1, max_length=9)
    execution_authorized: Literal[False] = False
    source_mutation_authorized: Literal[False] = False
    report_digest: Digest

    @field_validator("observed_at")
    @classmethod
    def aware_observation(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("repair evidence requires a timezone-aware trusted observation")
        return value
