from typing import List, Tuple
from jarvis_completion_validator.contracts import (
    CompletionCriterion, Evidence, TaskReviewInput, ReviewerFinding
)
from jarvis_completion_validator.enums import (
    EvidenceTrustLevel, FindingSeverity, FindingCategory
)
import uuid

def evaluate_evidence_for_criterion(
    criterion: CompletionCriterion,
    input_data: TaskReviewInput
) -> Tuple[bool, List[str], List[ReviewerFinding]]:
    """
    Determines if a criterion is met based on provided evidence and its trust level.
    Returns (is_met, evidence_ids, findings)
    """
    findings = []

    # 1. Identify all evidence explicitly linked to this criterion
    linked_evidence = [e for e in input_data.evidence if criterion.criterion_id in e.related_criterion_ids]

    # 2. Check if worker claimed it with specific evidence
    worker_claim = next((cc for cc in input_data.worker_result.criterion_claims if cc.criterion_id == criterion.criterion_id), None)

    all_evidence_ids = set([e.evidence_id for e in linked_evidence])
    if worker_claim:
        all_evidence_ids.update(worker_claim.evidence_ids)

    relevant_evidence = [e for e in input_data.evidence if e.evidence_id in all_evidence_ids]

    if not relevant_evidence:
        if criterion.required:
            findings.append(ReviewerFinding(
                finding_id=f"find-{uuid.uuid4().hex[:8]}",
                severity=FindingSeverity.MINOR,
                category=FindingCategory.MISSING_EVIDENCE,
                summary=f"No evidence provided for criterion {criterion.criterion_id}",
                detailed_reason="Worker must provide evidence or trusted checks must be present.",
                automatically_remediable=True,
                related_criterion_id=criterion.criterion_id
            ))
        return False, list(all_evidence_ids), findings

    # Evaluate trust
    has_trusted = False
    has_untrusted_only = True

    for ev in relevant_evidence:
        if ev.trust_level in [
            EvidenceTrustLevel.AUTHORITATIVE,
            EvidenceTrustLevel.TRUSTED_VALIDATOR,
            EvidenceTrustLevel.TRUSTED_TOOL,
            EvidenceTrustLevel.OPERATOR
        ]:
            has_trusted = True
            has_untrusted_only = False

    if has_untrusted_only:
        findings.append(ReviewerFinding(
            finding_id=f"find-{uuid.uuid4().hex[:8]}",
            severity=FindingSeverity.MINOR,
            category=FindingCategory.UNSUPPORTED_CLAIM,
            summary=f"Only untrusted evidence provided for criterion {criterion.criterion_id}",
            detailed_reason="Worker claim or model claim is insufficient without independent verification.",
            automatically_remediable=True,
            related_criterion_id=criterion.criterion_id
        ))
        return False, list(all_evidence_ids), findings

    return True, list(all_evidence_ids), findings
