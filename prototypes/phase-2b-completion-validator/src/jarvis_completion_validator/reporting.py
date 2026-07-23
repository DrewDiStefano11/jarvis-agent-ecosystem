from datetime import datetime, timezone
import json
from dataclasses import asdict

from jarvis_completion_validator import __version__
from jarvis_completion_validator.reviewer import Reviewer
from jarvis_completion_validator.scoring import calculate_score
from jarvis_completion_validator.recommendations import determine_recommendation
from jarvis_completion_validator.redaction import redact_dict
from jarvis_completion_validator.contracts import CompletionReport

def generate_report(raw_input: dict, profile: str = None) -> CompletionReport:
    # 1. Review
    reviewer = Reviewer(raw_input, profile)
    results = reviewer.review()

    task_data = results["task_data"]
    evaluated_criteria = results["evaluated_criteria"]
    findings = results["findings"]
    contradictions_report = results["contradictions_report"]
    unsupported_claims_report = results["unsupported_claims_report"]
    warnings = results["warnings"]

    # 2. Score
    score = calculate_score(task_data, evaluated_criteria, findings)

    # 3. Recommend
    actual_result, recommended_actions = determine_recommendation(task_data, evaluated_criteria, findings)

    # 4. Redact sensitive info from dict serializations
    artifacts_dicts = redact_dict([asdict(a) for a in task_data.artifacts])
    approvals_dicts = redact_dict([asdict(a) for a in task_data.approvals])
    checks_dicts = redact_dict([asdict(c) for c in task_data.trusted_checks])
    worker_claim_dict = redact_dict({
        "status": task_data.worker_result.status_claim,
        "confidence": task_data.worker_result.worker_confidence
    })

    # 5. Build Report
    report = CompletionReport(
        schema_version="1.0",
        validator_version=__version__,
        timestamp=datetime.now(timezone.utc),
        task_id=task_data.task.task_id,
        worker_claim=worker_claim_dict,
        actual_result=actual_result,
        score=score,
        criteria=evaluated_criteria,
        artifacts=artifacts_dicts,
        approvals=approvals_dicts,
        trusted_checks=checks_dicts,
        findings=findings,
        contradictions=contradictions_report,
        unsupported_claims=unsupported_claims_report,
        warnings=warnings,
        recommended_next_actions=recommended_actions
    )

    return report

class ReportEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        if hasattr(obj, "__dataclass_fields__"):
            return asdict(obj)
        if isinstance(obj, set):
            return list(obj)
        if hasattr(obj, "value"): # Enums
            return obj.value
        return super().default(obj)
