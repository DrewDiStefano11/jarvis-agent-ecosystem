from typing import List, Dict, Any
from jarvis_completion_validator.contracts import (
    TaskReviewInput, ReviewerFinding
)
from jarvis_completion_validator.enums import (
    FindingSeverity, FindingCategory, EvidenceTrustLevel
)
import uuid

def validate_unsupported_claims(input_data: TaskReviewInput) -> List[ReviewerFinding]:
    findings = []

    # Check for approval claims without records
    worker_approval_claims = input_data.worker_result.approval_claims
    record_ids = {a.approval_id for a in input_data.approvals}
    for claim in worker_approval_claims:
        if claim not in record_ids:
            findings.append(ReviewerFinding(
                finding_id=f"find-{uuid.uuid4().hex[:8]}",
                severity=FindingSeverity.MAJOR,
                category=FindingCategory.UNSUPPORTED_CLAIM,
                summary=f"Worker claims approval {claim} but no record exists",
                detailed_reason="Approval claims require verifiable authoritative records.",
                automatically_remediable=False,
                recommended_next_action="reject"
            ))

    # Check for test passing claims without trusted tests
    # If the worker summary claims "tests passed", we expect a trusted check
    if "test" in input_data.worker_result.summary.lower() and "pass" in input_data.worker_result.summary.lower():
        trusted_test = next((tc for tc in input_data.trusted_checks if tc.check_type == "test_suite"), None)
        if not trusted_test:
            findings.append(ReviewerFinding(
                finding_id=f"find-{uuid.uuid4().hex[:8]}",
                severity=FindingSeverity.MAJOR,
                category=FindingCategory.UNSUPPORTED_CLAIM,
                summary="Worker claims tests passed without trusted evidence",
                detailed_reason="Statements about passing tests require a trusted test check record.",
                automatically_remediable=True,
                recommended_next_action="request_revision"
            ))

    # File existence claims without artifact
    if "created" in input_data.worker_result.summary.lower() and "file" in input_data.worker_result.summary.lower():
        if not input_data.artifacts:
            findings.append(ReviewerFinding(
                finding_id=f"find-{uuid.uuid4().hex[:8]}",
                severity=FindingSeverity.MAJOR,
                category=FindingCategory.UNSUPPORTED_CLAIM,
                summary="Worker claims file creation without artifact evidence",
                detailed_reason="File creation claims must be backed by an artifact manifest.",
                automatically_remediable=True,
                recommended_next_action="request_revision"
            ))

    return findings
