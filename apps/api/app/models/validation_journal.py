"""Append-only validation intent and monotonic step history contracts."""

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.models.development_validation import Contract, Digest, Gate
from app.models.validation_execution import ExecutionPlan, Identifier, SignedReceipt


class JournalOwner(Contract):
    workspace_id: Identifier
    task_id: Identifier
    runtime_run_id: Identifier
    attempt_id: Identifier
    worker_id: Identifier
    approval_id: Identifier
    lease_fingerprint: Digest


class JournalIntent(Contract):
    schema_version: Literal["1.0"] = "1.0"
    journal_id: Identifier
    owner: JournalOwner
    plan: ExecutionPlan


class JournalEntry(Contract):
    schema_version: Literal["1.0"] = "1.0"
    journal_id: Identifier
    sequence: int = Field(strict=True, ge=1, le=128)
    previous_digest: Digest
    kind: Literal[
        "intent", "step_intent", "step_started", "step_result", "step_uncertain", "finalized"
    ]
    timestamp: datetime
    intent: JournalIntent | None = None
    gate: Gate | None = None
    command_run_id: Identifier | None = None
    receipt: SignedReceipt | None = None
    entry_digest: Digest

    @field_validator("timestamp")
    @classmethod
    def aware_timestamp(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("journal timestamps require timezone")
        return value

    @model_validator(mode="after")
    def shape(self):
        if (self.kind == "intent") != (self.intent is not None):
            raise ValueError("only intent entries contain the immutable intent")
        step = self.kind.startswith("step_")
        if step != (self.gate is not None and self.command_run_id is not None):
            raise ValueError("step entries require gate and command references")
        if not step and (self.gate is not None or self.command_run_id is not None):
            raise ValueError("nonstep entries cannot contain command references")
        if (self.kind == "step_result") != (self.receipt is not None):
            raise ValueError("only measured result entries contain receipts")
        return self


class JournalStep(Contract):
    gate: Gate
    command_run_id: Identifier
    state: Literal["intended", "started", "measured", "uncertain"]
    receipt: SignedReceipt | None = None


class JournalSnapshot(Contract):
    intent: JournalIntent
    sequence: int
    digest: Digest
    updated_at: datetime
    state: Literal["prepared", "active", "finalized"]
    steps: tuple[JournalStep, ...] = Field(default=(), max_length=9)
    # Checkpoints acknowledge exact entry digests; no journal state invents a cursor.


class JournalRequest(Contract):
    expected_plan_hash: Digest
    approval_id: Identifier
    worker_id: Identifier
    lease_token: str = Field(min_length=1, max_length=200, repr=False)


class JournalApproval(Contract):
    approval_id: Identifier
    workspace_id: Identifier
    plan: ExecutionPlan
    approved_by: Identifier
    expires_at: datetime
