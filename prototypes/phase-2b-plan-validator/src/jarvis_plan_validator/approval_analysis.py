from .enums import ExecutionEligibility, ToolRiskLevel
from .contracts import TaskEnvelope, ExecutionPlan, ValidationReportStep

def calculate_plan_approval(
    task_envelope: TaskEnvelope,
    plan: ExecutionPlan,
    step_reports: list[ValidationReportStep]
) -> ExecutionEligibility:
    """
    Determines overall execution eligibility based on step risk levels.
    """
    has_approval_required = False
    has_prohibited = False
    has_unsupported = False

    for step_report in step_reports:
        if not step_report.valid:
            return ExecutionEligibility.UNKNOWN

        risk = step_report.risk_level
        if risk == ToolRiskLevel.BLACK or risk == ToolRiskLevel.RED:
            has_prohibited = True
        elif risk == ToolRiskLevel.ORANGE:
            has_approval_required = True

        if step_report.errors and "unsupported" in " ".join(step_report.errors).lower():
            has_unsupported = True

    if has_unsupported:
        return ExecutionEligibility.UNSUPPORTED
    if has_prohibited:
        return ExecutionEligibility.PROHIBITED

    if has_approval_required:
        return ExecutionEligibility.APPROVAL_REQUIRED

    return ExecutionEligibility.AUTOMATIC
