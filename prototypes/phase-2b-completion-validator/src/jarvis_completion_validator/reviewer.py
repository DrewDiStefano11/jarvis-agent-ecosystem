from typing import List
from jarvis_completion_validator.contracts import (
    TaskReviewInput, ReviewerFinding, EvaluatedCriterion
)
from jarvis_completion_validator.normalization import normalize_task_input
from jarvis_completion_validator.schema import load_task_review_input
from jarvis_completion_validator.criteria import evaluate_criteria
from jarvis_completion_validator.artifact_validation import validate_artifacts
from jarvis_completion_validator.claim_validation import validate_unsupported_claims
from jarvis_completion_validator.contradiction_detection import detect_contradictions
from jarvis_completion_validator.policy_validation import validate_approvals_and_policy
from jarvis_completion_validator.deterministic_checks import validate_trusted_checks
from jarvis_completion_validator.task_profiles import validate_task_profile

class Reviewer:
    def __init__(self, raw_input: dict, profile: str = None):
        self.raw_input = raw_input
        self.profile = profile
        self.normalized_input = normalize_task_input(raw_input)
        self.task_data = load_task_review_input(self.normalized_input)

    def review(self) -> dict:
        all_findings: List[ReviewerFinding] = []

        # 1. Criteria evaluation
        evaluated_criteria, criteria_findings = evaluate_criteria(self.task_data)
        all_findings.extend(criteria_findings)

        # 2. Artifact validation
        artifact_findings = validate_artifacts(self.task_data)
        all_findings.extend(artifact_findings)

        # 3. Claims
        claim_findings = validate_unsupported_claims(self.task_data)
        all_findings.extend(claim_findings)

        # 4. Contradictions
        contradiction_findings, contradictions_report = detect_contradictions(self.task_data)
        all_findings.extend(contradiction_findings)

        # 5. Policies & Approvals
        policy_findings = validate_approvals_and_policy(self.task_data)
        all_findings.extend(policy_findings)

        # 6. Trusted Checks
        check_findings = validate_trusted_checks(self.task_data)
        all_findings.extend(check_findings)

        # 7. Task Profiles
        profile_findings = validate_task_profile(self.task_data, self.profile)
        all_findings.extend(profile_findings)

        # Gather all warning strings from worker result
        warnings = self.task_data.worker_result.warnings

        # Map findings to unsupported claims report
        unsupported_claims_report = []
        for f in claim_findings:
            if f.category == "unsupported_claim":
                unsupported_claims_report.append({"claim": f.summary, "finding_id": f.finding_id})

        return {
            "task_data": self.task_data,
            "evaluated_criteria": evaluated_criteria,
            "findings": all_findings,
            "contradictions_report": contradictions_report,
            "unsupported_claims_report": unsupported_claims_report,
            "warnings": warnings
        }
