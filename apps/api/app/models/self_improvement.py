"""Bounded, immutable advisory contracts. No execution or authority fields."""

import os
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.autonomy.events import scrub_text
from app.diagnostics.redaction import collect_secret_values, redact_text

Identifier = Annotated[str, Field(min_length=1, max_length=160)]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Text = Annotated[str, Field(min_length=1, max_length=1000)]
Number = Annotated[float, Field(allow_inf_nan=False, strict=True)]
SourceKind = Literal[
    "autonomy_acceptance",
    "model_evaluation",
    "model_qualification",
    "runtime_history",
    "runtime_doctor",
]
Category = Literal[
    "reliability", "model_role", "planning", "execution", "system_runtime", "efficiency"
]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    @field_validator("*", mode="before")
    @classmethod
    def scrub_strings(cls, value):
        # Free text and identifiers receive the same existing security scrubber.
        return (
            scrub_text(redact_text(value, collect_secret_values(os.environ)))
            if isinstance(value, str)
            else value
        )


class EvaluationObservation(Contract):
    id: Digest
    source_type: SourceKind
    source_id: Identifier
    evidence_digest: Digest
    timestamp: datetime
    subject_id: Identifier
    task_id: Identifier | None = None
    checkpoint_id: Identifier | None = None
    context_id: Identifier | None = None
    stage: Identifier
    role: Identifier = "unknown"
    model: Identifier = "unknown"
    provider: Identifier = "unknown"
    inference_mode: Literal["fixture", "installed_local", "runtime", "unknown"] = "unknown"
    metric: Identifier
    actual: Number | None = None
    expected: Number | None = None
    direction: Literal["higher", "lower"] = "higher"
    failure_code: Identifier | None = None
    hard_gate: bool = False
    category: Category
    # A missing measurement is never scored as a failure.
    measured: bool = True

    @model_validator(mode="after")
    def measurement(self):
        if self.measured != (self.actual is not None):
            raise ValueError("measured must match actual availability")
        if self.timestamp.tzinfo is None:
            raise ValueError("evidence timestamps must have a timezone")
        return self


class SourceProvenance(Contract):
    source_type: SourceKind
    source_id: Identifier
    digest: Digest
    schema_version: Identifier
    repo_sha: Identifier
    suite_version: Identifier = "unknown"
    suite_digest: Digest | None = None
    policy_version: Identifier = "unknown"
    policy_digest: Digest | None = None
    case_ids: tuple[Identifier, ...] = Field(default=(), max_length=512)
    configuration_digest: Digest | None = None
    complete: bool = False

    @model_validator(mode="after")
    def completeness(self):
        if len(set(self.case_ids)) != len(self.case_ids):
            raise ValueError("duplicate evaluation cases")
        if self.complete and (
            not self.case_ids
            or self.suite_digest is None
            or self.policy_digest is None
            or self.configuration_digest is None
            or self.repo_sha == "unknown"
            or self.suite_version == "unknown"
            or self.policy_version == "unknown"
        ):
            raise ValueError(
                "complete evidence requires suite, policy, cases and configuration provenance"
            )
        return self


class Baseline(Contract):
    id: Digest
    created_at: datetime
    repo_sha: Annotated[str, Field(pattern=r"^[a-f0-9]{40}$")]
    configuration_fingerprint: Digest
    safety_fingerprint: Digest
    evaluator_version: Literal["1.0"] = "1.0"
    evaluator_digest: Digest
    sources: tuple[SourceProvenance, ...] = Field(max_length=64)
    observations: tuple[EvaluationObservation, ...] = Field(max_length=4096)
    missing_metrics: tuple[Identifier, ...] = Field(max_length=64)

    @model_validator(mode="after")
    def references(self):
        ids = [o.id for o in self.observations]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate observation IDs")
        sources = {(s.source_type, s.source_id, s.digest) for s in self.sources}
        if len({(s.source_type, s.source_id) for s in self.sources}) != len(self.sources):
            raise ValueError("duplicate source references")
        if any(
            (o.source_type, o.source_id, o.evidence_digest) not in sources
            for o in self.observations
        ):
            raise ValueError("observation provenance is not in baseline")
        return self


class Weakness(Contract):
    id: Digest
    category: Category
    stage: Identifier
    role: Identifier
    evidence_ids: tuple[Digest, ...] = Field(min_length=1, max_length=4096)
    frequency: int = Field(ge=1, le=4096)
    affected_subjects: int = Field(ge=1, le=4096)
    severity: Literal["critical", "high", "medium", "low"]
    confidence: Literal["low", "medium", "high"]
    actionable: bool
    repeated: bool
    reason: Text


class ImprovementHypothesis(Contract):
    id: Digest
    weakness_id: Digest
    suspected_subsystem: Identifier
    explanation: Text
    supporting_evidence_ids: tuple[Digest, ...] = Field(min_length=1, max_length=32)
    contradicting_evidence_ids: tuple[Digest, ...] = Field(default=(), max_length=32)
    confidence: Literal["low", "medium", "high"]
    information_needed: tuple[Text, ...] = Field(default=(), max_length=16)
    origin: Literal["model_advisory"] = "model_advisory"


class Criterion(Contract):
    observation_id: Digest
    target: Number
    direction: Literal["higher", "lower"]


class ExperimentPlan(Contract):
    baseline_id: Digest
    candidate_description: Text
    evaluation_sources: tuple[Digest, ...] = Field(min_length=1, max_length=64)
    primary: tuple[Criterion, ...] = Field(min_length=1, max_length=4096)
    regression: tuple[Criterion, ...] = Field(min_length=1, max_length=4096)
    required_checks: tuple[
        Literal["authorization", "emergency_stop", "no_remote_provider", "migrations", "tests"], ...
    ] = ("authorization", "emergency_stop", "no_remote_provider", "migrations", "tests")
    # Exact candidate identity/configuration is supplied by an operator at comparison.
    candidate_applied: Literal[False] = False


class Proposal(Contract):
    id: Digest
    baseline_id: Digest
    weakness_id: Digest
    evidence_ids: tuple[Digest, ...] = Field(min_length=1, max_length=4096)
    category: Literal[
        "prompt_adjustment",
        "schema_validation",
        "retry_policy",
        "context_selection",
        "capability_taxonomy",
        "decomposer_configuration",
        "reviewer_threshold",
        "model_parameter",
        "task_bounds",
        "runtime_configuration",
        "test_coverage",
        "missing_capability",
        "model_role_reassignment",
    ]
    target_subsystem: Identifier
    change_description: Text
    expected_effect: Text
    risk: Literal["low", "medium", "high"]
    rollback: Text
    priority: Literal["critical", "high", "medium", "low"]
    priority_reasons: tuple[Text, ...] = Field(min_length=1, max_length=8)
    status: Literal[
        "proposed",
        "needs_evidence",
        "ready_for_review",
        "approved_for_experiment",
        "rejected",
        "superseded",
        "evaluated",
    ]
    experiment: ExperimentPlan | None


class Analysis(Contract):
    baseline: Baseline
    weaknesses: tuple[Weakness, ...] = Field(max_length=256)
    hypotheses: tuple[ImprovementHypothesis, ...] = Field(default=(), max_length=64)
    proposals: tuple[Proposal, ...] = Field(max_length=256)

    @model_validator(mode="after")
    def graph(self):
        observations = {o.id: o for o in self.baseline.observations}
        weaknesses = {w.id: w for w in self.weaknesses}
        if len(weaknesses) != len(self.weaknesses):
            raise ValueError("duplicate weaknesses")
        for weakness in self.weaknesses:
            if not set(weakness.evidence_ids) <= observations.keys():
                raise ValueError("unknown weakness evidence")
        for hypothesis in self.hypotheses:
            weakness = weaknesses.get(hypothesis.weakness_id)
            if weakness is None or not set(hypothesis.supporting_evidence_ids) <= set(
                weakness.evidence_ids
            ):
                raise ValueError("unsupported hypothesis")
            if not set(hypothesis.contradicting_evidence_ids) <= observations.keys():
                raise ValueError("unknown hypothesis evidence")
        for proposal in self.proposals:
            weakness = weaknesses.get(proposal.weakness_id)
            if (
                weakness is None
                or proposal.baseline_id != self.baseline.id
                or proposal.evidence_ids != weakness.evidence_ids
            ):
                raise ValueError("proposal does not match weakness/baseline")
            if proposal.experiment:
                plan = proposal.experiment
                if plan.baseline_id != self.baseline.id:
                    raise ValueError("wrong experiment baseline")
                if plan.evaluation_sources != tuple(s.digest for s in self.baseline.sources):
                    raise ValueError("experiment must retain all evaluation sources")
                if (
                    set(plan.required_checks)
                    != {
                        "authorization",
                        "emergency_stop",
                        "no_remote_provider",
                        "migrations",
                        "tests",
                    }
                    or len(plan.required_checks) != 5
                ):
                    raise ValueError("required regression checks cannot be removed")
                if {c.observation_id for c in plan.primary} != set(weakness.evidence_ids):
                    raise ValueError("primary criteria must retain all weakness measurements")
                if {c.observation_id for c in plan.regression} != {
                    o.id for o in self.baseline.observations if o.measured
                }:
                    raise ValueError("regression criteria must retain all measured evidence")
                for criterion in plan.primary + plan.regression:
                    if criterion.observation_id not in observations:
                        raise ValueError("invented criterion evidence")
                    if criterion.direction != observations[criterion.observation_id].direction:
                        raise ValueError("criterion direction changed")
                if any(c.target != observations[c.observation_id].expected for c in plan.primary):
                    raise ValueError("primary criteria must preserve recorded expectations")
                if any(c.target != observations[c.observation_id].actual for c in plan.regression):
                    raise ValueError("regression criteria must preserve baseline values")
        return self


class Comparison(Contract):
    id: Digest
    before_id: Digest
    after_id: Digest
    proposal_id: Digest
    decision: Literal["improved", "neutral", "regressed", "inconclusive"]
    improved: tuple[Text, ...] = Field(default=(), max_length=4096)
    regressed: tuple[Text, ...] = Field(default=(), max_length=4096)
    unchanged: tuple[Text, ...] = Field(default=(), max_length=4096)
    newly_measured: tuple[Text, ...] = Field(default=(), max_length=4096)
    missing: tuple[Text, ...] = Field(default=(), max_length=4096)
    reasons: tuple[Text, ...] = Field(max_length=64)


class CandidateAttestation(Contract):
    """Operator evidence, not a model assertion or an approval to execute."""

    candidate_repo_sha: Annotated[str, Field(pattern=r"^[a-f0-9]{40}$")]
    candidate_configuration_fingerprint: Digest
    authorization: bool | None = None
    emergency_stop: bool | None = None
    no_remote_provider: bool | None = None
    migrations: bool | None = None
    tests: bool | None = None
    evidence_digest: Digest
