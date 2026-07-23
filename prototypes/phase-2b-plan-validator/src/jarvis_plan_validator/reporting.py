import json
from dataclasses import asdict
from typing import Dict, Any
from .contracts import ValidationReport
from .redaction import redact_sensitive_values

def generate_json_report(report: ValidationReport) -> str:
    """Returns the JSON string for the validation report."""
    raw_dict = asdict(report)
    redacted = redact_sensitive_values(raw_dict)
    return json.dumps(redacted, indent=2)

def generate_text_report(report: ValidationReport) -> str:
    """Returns a human-readable text string for the validation report."""
    lines = []
    lines.append(f"Validation Report for Plan: {report.plan_id}")
    lines.append(f"Task ID: {report.task_id}")
    lines.append(f"Status: {report.result.status.upper()}")
    lines.append(f"Execution Eligibility: {report.result.execution_eligibility.upper()}")
    lines.append(f"Recommended Action: {report.recommended_action.upper()}")

    if report.errors:
        lines.append("\nErrors:")
        for err in report.errors:
            lines.append(f"  - {err}")

    if report.warnings:
        lines.append("\nWarnings:")
        for warn in report.warnings:
            lines.append(f"  - {warn}")

    lines.append("\nSteps Summary:")
    if not report.steps:
        lines.append("  No steps processed.")
    for step in report.steps:
        status_char = "✓" if step.valid else "✗"
        risk_str = step.risk_level.value.upper() if step.risk_level else "NONE"
        lines.append(f"  {status_char} [{step.step_id}] Tool: {step.tool_name or 'N/A'} (Risk: {risk_str})")
        if step.errors:
            for err in step.errors:
                lines.append(f"      Error: {err}")

    return "\n".join(lines)
