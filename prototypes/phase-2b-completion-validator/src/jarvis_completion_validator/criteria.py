from typing import List, Tuple
from jarvis_completion_validator.contracts import (
    CompletionCriterion, Evidence, TaskReviewInput, EvaluatedCriterion, ReviewerFinding
)
from jarvis_completion_validator.enums import (
    CriterionStatus, FindingSeverity, FindingCategory, EvidenceTrustLevel
)
from jarvis_completion_validator.evidence import evaluate_evidence_for_criterion
import uuid

def evaluate_criteria(input_data: TaskReviewInput) -> Tuple[List[EvaluatedCriterion], List[ReviewerFinding]]:
    results = []
    findings = []

    for criterion in input_data.task.completion_criteria:
        status = CriterionStatus.UNKNOWN
        evidence_ids = []
        criterion_findings = []

        # Determine if worker claimed it was satisfied
        worker_claim = next((cc for cc in input_data.worker_result.criterion_claims if cc.criterion_id == criterion.criterion_id), None)

        # Manual Review check
        if criterion.human_review_required:
            status = CriterionStatus.MANUAL_REVIEW_REQUIRED
            finding = ReviewerFinding(
                finding_id=f"find-{uuid.uuid4().hex[:8]}",
                severity=FindingSeverity.INFO,
                category=FindingCategory.MANUAL_REVIEW_REQUIRED,
                summary=f"Criterion {criterion.criterion_id} requires human review.",
                detailed_reason="Semantic correctness or specific properties cannot be determined automatically.",
                automatically_remediable=False,
                related_criterion_id=criterion.criterion_id,
                recommended_next_action="human_review"
            )
            findings.append(finding)
            criterion_findings.append(finding.finding_id)
            if worker_claim:
                evidence_ids.extend(worker_claim.evidence_ids)
        else:
            # Evaluate automatically
            is_met, ev_ids, ev_findings = evaluate_evidence_for_criterion(criterion, input_data)
            evidence_ids.extend(ev_ids)
            findings.extend(ev_findings)
            criterion_findings.extend([f.finding_id for f in ev_findings])

            if is_met:
                status = CriterionStatus.MET
            else:
                status = CriterionStatus.UNMET
                if criterion.required:
                    # Create a specific finding for the required criterion being unmet
                    severity = criterion.severity_if_unmet or FindingSeverity.MAJOR
                    unmet_finding = ReviewerFinding(
                        finding_id=f"find-{uuid.uuid4().hex[:8]}",
                        severity=severity,
                        category=FindingCategory.CRITERION_UNMET,
                        summary=f"Required criterion '{criterion.criterion_id}' not met.",
                        detailed_reason=criterion.description,
                        automatically_remediable=True,
                        related_criterion_id=criterion.criterion_id,
                        recommended_next_action="request_revision"
                    )
                    findings.append(unmet_finding)
                    criterion_findings.append(unmet_finding.finding_id)

        results.append(EvaluatedCriterion(
            criterion_id=criterion.criterion_id,
            status=status,
            verification_method=criterion.verification_method.value,
            evidence_ids=evidence_ids,
            findings=criterion_findings
        ))

    return results, findings
