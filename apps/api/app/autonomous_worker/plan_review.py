"""Deterministic local review policy for persisted Phase 2C planning results.

The reviewer is a pure structural function over the already durable
``PlanningReviewResult``.  It never calls a model, never interprets generated
prose as authorization, and never grants capability: it maps a persisted plan to
one machine-readable orchestration outcome that the durable workflow can act on.

Generated text is evidence, not authority.  Only ``PlanReviewOutcome`` governs
orchestration; ``analysis``/``summary`` remain stored explanation.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.models.autonomous_worker import PlanningReviewResult

PLAN_REVIEW_POLICY_VERSION = "1.0"
MAX_REVIEW_FINDINGS = 8


class PlanReviewOutcome(StrEnum):
    """Machine-usable review outcomes recognized by the durable orchestration."""

    ACCEPTED = "accepted"
    REVISION_REQUESTED = "revision_requested"
    ESCALATED = "escalated"


class PlanReviewFinding(StrEnum):
    """Bounded structural findings; never free-form model prose."""

    MISSING_RECOMMENDATIONS = "plan_missing_recommendations"
    OPEN_INFORMATION_GAPS = "plan_open_information_gaps"
    HUMAN_REVIEW_REQUESTED = "plan_human_review_requested"


ACCEPTED_REASON_CODE = "plan_review_accepted"
REVISION_REASON_CODE = "plan_missing_recommendations"
ESCALATION_REASON_CODE = "model_result_review_required"


@dataclass(frozen=True)
class PlanReviewDecision:
    """One deterministic review decision for one exact runtime attempt."""

    outcome: PlanReviewOutcome
    reason_code: str
    findings: tuple[str, ...] = ()

    def as_metadata(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "reasonCode": self.reason_code,
            "findings": list(self.findings),
            "policyVersion": PLAN_REVIEW_POLICY_VERSION,
        }


class PlanReviewRecordError(ValueError):
    """A persisted review record is not a valid deterministic decision."""


def evaluate_plan(result: PlanningReviewResult) -> PlanReviewDecision:
    """Return the deterministic review decision for one persisted plan."""

    findings: list[str] = []
    if not result.recommendations:
        findings.append(PlanReviewFinding.MISSING_RECOMMENDATIONS.value)
    if result.missingInformation:
        findings.append(PlanReviewFinding.OPEN_INFORMATION_GAPS.value)
    if result.requiresHumanReview:
        findings.append(PlanReviewFinding.HUMAN_REVIEW_REQUESTED.value)
        return PlanReviewDecision(
            outcome=PlanReviewOutcome.ESCALATED,
            reason_code=ESCALATION_REASON_CODE,
            findings=tuple(findings[:MAX_REVIEW_FINDINGS]),
        )
    if not result.recommendations:
        # A plan without a single actionable recommendation is structurally
        # unusable, so a bounded revision attempt is requested instead of
        # accepting an empty deliverable.
        return PlanReviewDecision(
            outcome=PlanReviewOutcome.REVISION_REQUESTED,
            reason_code=REVISION_REASON_CODE,
            findings=tuple(findings[:MAX_REVIEW_FINDINGS]),
        )
    return PlanReviewDecision(
        outcome=PlanReviewOutcome.ACCEPTED,
        reason_code=ACCEPTED_REASON_CODE,
        findings=tuple(findings[:MAX_REVIEW_FINDINGS]),
    )


def decision_from_metadata(metadata: Mapping[str, Any]) -> PlanReviewDecision:
    """Rebuild a decision from a durable checkpoint record.

    Raises ``PlanReviewRecordError`` when the persisted record cannot be trusted
    so recovery fails closed instead of guessing an outcome.
    """

    outcome = metadata.get("outcome")
    reason_code = metadata.get("reasonCode")
    findings = metadata.get("findings", [])
    policy_version = metadata.get("policyVersion")
    if policy_version != PLAN_REVIEW_POLICY_VERSION:
        raise PlanReviewRecordError("unsupported review policy version")
    if not isinstance(outcome, str) or outcome not in set(PlanReviewOutcome):
        raise PlanReviewRecordError("unknown review outcome")
    if not isinstance(reason_code, str) or not reason_code:
        raise PlanReviewRecordError("missing review reason code")
    if not isinstance(findings, list) or not all(isinstance(item, str) for item in findings):
        raise PlanReviewRecordError("invalid review findings")
    if len(findings) > MAX_REVIEW_FINDINGS:
        raise PlanReviewRecordError("too many review findings")
    return PlanReviewDecision(
        outcome=PlanReviewOutcome(outcome),
        reason_code=reason_code,
        findings=tuple(findings),
    )


def revision_directive(findings: tuple[str, ...]) -> str:
    """Return the bounded, deterministic revision instruction for a retry.

    Only machine-generated finding codes are echoed back; no model prose from a
    previous attempt is reused.
    """

    codes = ", ".join(findings[:MAX_REVIEW_FINDINGS])
    return (
        "The previous plan for this task was rejected by the deterministic "
        f"structural review policy {PLAN_REVIEW_POLICY_VERSION}. "
        f"Unmet requirements: {codes}. "
        "Return one corrected JSON plan that resolves every listed requirement. "
        "Do not include tools, commands, approvals, or extra fields."
    )
