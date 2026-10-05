"""Frozen completion policy and bounded independent verdicts; never authority."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")]
Outcome = Literal["passed", "needs_correction", "failed", "unverifiable"]


class VerificationContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CompletionCriterion(VerificationContract):
    id: Identifier
    description: str = Field(min_length=1, max_length=300)
    mode: Literal["field_nonempty", "field_contains", "artifact", "test_evidence", "semantic"]
    field: Literal["summary", "analysis", "recommendations", "risks", "assumptions"] | None = None
    expected: str | None = Field(default=None, min_length=1, max_length=300)
    artifactId: Identifier | None = None
    expectedPath: str | None = Field(default=None, min_length=1, max_length=240)
    expectedHash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def coherent_policy(self):
        if self.mode == "artifact":
            if self.artifactId is None or self.expectedPath is None or self.expectedHash is None:
                raise ValueError("artifact criteria require a frozen ID, path and hash")
        elif any(
            value is not None for value in (self.artifactId, self.expectedPath, self.expectedHash)
        ):
            raise ValueError("artifact references require artifact mode")
        if self.mode in {"artifact", "test_evidence"}:
            if self.field is not None or self.expected is not None:
                raise ValueError("evidence criteria cannot specify field predicates")
            return self
        if self.mode == "semantic":
            if self.field is not None or self.expected is not None:
                raise ValueError("semantic criteria cannot specify field predicates")
        elif self.field is None:
            raise ValueError("deterministic criteria require a field")
        if (self.mode == "field_contains") != (self.expected is not None):
            raise ValueError("only field_contains requires an expected value")
        if self.mode == "field_contains" and self.field not in {"summary", "analysis"}:
            raise ValueError("field_contains requires a text field")
        return self


class CriterionCheck(VerificationContract):
    criterionId: Identifier
    outcome: Outcome
    evidenceIds: list[Identifier] = Field(default_factory=list, max_length=8)
    reason: str = Field(min_length=1, max_length=300)


class ReviewerVerdict(VerificationContract):
    checks: list[CriterionCheck] = Field(min_length=1, max_length=8)


class VerificationResult(VerificationContract):
    schemaVersion: Literal["1.0"] = "1.0"
    policyVersion: Literal["independent-verifier-1"] = "independent-verifier-1"
    verificationId: Identifier
    taskId: Identifier
    runtimeRunId: Identifier
    runtimeAttemptId: Identifier
    executionId: Identifier
    resultHash: str = Field(pattern=r"^[a-f0-9]{64}$")
    criteriaHash: str = Field(pattern=r"^[a-f0-9]{64}$")
    outcome: Outcome
    checks: list[CriterionCheck] = Field(min_length=1, max_length=8)
    reviewerRole: Literal["independent_critic"] = "independent_critic"
    provider: str | None = Field(default=None, max_length=120)
    model: str | None = Field(default=None, max_length=200)
    requestCount: int = Field(ge=0, le=2)
    createdAt: datetime
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
