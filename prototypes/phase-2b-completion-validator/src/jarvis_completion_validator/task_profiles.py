from typing import List
from jarvis_completion_validator.contracts import (
    TaskReviewInput, ReviewerFinding
)
from jarvis_completion_validator.enums import (
    FindingSeverity, FindingCategory
)
import uuid
import json

def validate_task_profile(input_data: TaskReviewInput, profile_name: str) -> List[ReviewerFinding]:
    findings = []

    # We will use profile_name if provided, else fallback to task_type in the envelope
    effective_profile = profile_name or input_data.task.task_type

    if effective_profile == "documentation":
        findings.extend(_validate_documentation(input_data))
    elif effective_profile == "research-summary":
        findings.extend(_validate_research_summary(input_data))
    elif effective_profile == "code-analysis":
        findings.extend(_validate_code_analysis(input_data))
    elif effective_profile == "patch-proposal":
        findings.extend(_validate_patch_proposal(input_data))
    elif effective_profile == "data-classification":
        findings.extend(_validate_data_classification(input_data))

    return findings

def _validate_documentation(input_data: TaskReviewInput) -> List[ReviewerFinding]:
    findings = []
    # Validate required headings, topics, limitations
    # For now, simulate by checking if 'limitations' is required and present
    # Check if a markdown artifact exists
    has_md = any(a.format == "markdown" for a in input_data.artifacts)
    if not has_md:
        findings.append(ReviewerFinding(
            finding_id=f"find-{uuid.uuid4().hex[:8]}",
            severity=FindingSeverity.MINOR,
            category=FindingCategory.INVALID_ARTIFACT,
            summary="Documentation task expects a markdown format artifact",
            detailed_reason="Standard documentation format is missing.",
            automatically_remediable=True,
            recommended_next_action="request_revision"
        ))
    return findings

def _validate_research_summary(input_data: TaskReviewInput) -> List[ReviewerFinding]:
    findings = []
    # Require source references. We look at evidence.
    has_source = any(e.evidence_type == "source_reference" for e in input_data.evidence)
    if not has_source:
         findings.append(ReviewerFinding(
            finding_id=f"find-{uuid.uuid4().hex[:8]}",
            severity=FindingSeverity.MAJOR,
            category=FindingCategory.MISSING_EVIDENCE,
            summary="Research summary missing source references",
            detailed_reason="Research tasks require source_reference evidence.",
            automatically_remediable=True,
            recommended_next_action="request_revision"
        ))
    return findings

def _validate_code_analysis(input_data: TaskReviewInput) -> List[ReviewerFinding]:
    findings = []
    # No claim that code was modified
    if "modified code" in input_data.worker_result.summary.lower() or "changed" in input_data.worker_result.summary.lower():
         findings.append(ReviewerFinding(
            finding_id=f"find-{uuid.uuid4().hex[:8]}",
            severity=FindingSeverity.CRITICAL,
            category=FindingCategory.POLICY_VIOLATION,
            summary="Code analysis task claims to have modified code",
            detailed_reason="Code analysis must be read-only.",
            automatically_remediable=False,
            recommended_next_action="reject"
        ))
    return findings

def _validate_patch_proposal(input_data: TaskReviewInput) -> List[ReviewerFinding]:
    findings = []
    # Patch artifact exists, text only, not applied
    has_patch = any(a.artifact_type == "patch" and a.format == "text" for a in input_data.artifacts)
    if not has_patch:
         findings.append(ReviewerFinding(
            finding_id=f"find-{uuid.uuid4().hex[:8]}",
            severity=FindingSeverity.MAJOR,
            category=FindingCategory.MISSING_ARTIFACT,
            summary="Patch proposal missing text patch artifact",
            detailed_reason="A patch-proposal task must produce a text patch.",
            automatically_remediable=True,
            recommended_next_action="request_revision"
        ))
    if "applied" in input_data.worker_result.summary.lower():
         findings.append(ReviewerFinding(
            finding_id=f"find-{uuid.uuid4().hex[:8]}",
            severity=FindingSeverity.CRITICAL,
            category=FindingCategory.POLICY_VIOLATION,
            summary="Patch proposal claims to have applied the patch",
            detailed_reason="Proposals must not be applied automatically.",
            automatically_remediable=False,
            recommended_next_action="reject"
        ))
    return findings

def _validate_data_classification(input_data: TaskReviewInput) -> List[ReviewerFinding]:
    findings = []
    # Verify count reconciliation, no unsupported completion claim
    # Example mock check
    if "classified all" in input_data.worker_result.summary.lower() and input_data.worker_result.errors:
        findings.append(ReviewerFinding(
            finding_id=f"find-{uuid.uuid4().hex[:8]}",
            severity=FindingSeverity.MAJOR,
            category=FindingCategory.CONTRADICTION,
            summary="Data classification claims all done but reports errors",
            detailed_reason="Reconciliation mismatch.",
            automatically_remediable=True,
            recommended_next_action="request_revision"
        ))
    return findings
