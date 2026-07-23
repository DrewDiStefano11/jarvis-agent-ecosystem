from typing import Dict, Any, List
from .enums import NetworkPolicy
from .contracts import TaskEnvelope, ExecutionPlan, ExecutionStep, FinalOutput, RetryPolicy
from .limits import ConfigurationLimits

def load_task_envelope_from_dict(data: Dict[str, Any]) -> TaskEnvelope:
    """Basic structural mapping for TaskEnvelope"""
    return TaskEnvelope(
        schema_version=data.get("schema_version", "1.0"),
        task_id=data.get("task_id", ""),
        title=data.get("title", ""),
        request=data.get("request", ""),
        project_id=data.get("project_id"),
        workspace_roots=data.get("workspace_roots", []),
        allowed_tools=data.get("allowed_tools", []),
        denied_tools=data.get("denied_tools", []),
        maximum_steps=data.get("maximum_steps", 10),
        maximum_model_retries=data.get("maximum_model_retries", 0),
        approval_policy_version=data.get("approval_policy_version"),
        network_policy=NetworkPolicy(data.get("network_policy", "none"))
    )

def validate_plan_schema(plan_dict: Dict[str, Any], limits: ConfigurationLimits) -> tuple[bool, List[str], ExecutionPlan]:
    errors = []

    # Required fields
    required = ["schema_version", "plan_id", "task_id", "objective", "completion_criteria", "steps", "final_output"]
    for req in required:
        if req not in plan_dict:
            errors.append(f"Missing required field: {req}")

    if errors:
        return False, errors, None

    # Lengths and limits
    if len(plan_dict.get("completion_criteria", [])) == 0:
        errors.append("completion_criteria must be non-empty")

    if len(plan_dict.get("steps", [])) > limits.max_steps:
        errors.append(f"Steps count exceeds maximum of {limits.max_steps}")

    steps_list = []
    for step_data in plan_dict.get("steps", []):
        retry = None
        if "retry_policy" in step_data:
            rp = step_data["retry_policy"]
            retry = RetryPolicy(
                maximum_attempts=rp.get("maximum_attempts", 0),
                retryable_errors=rp.get("retryable_errors", [])
            )
            if retry.maximum_attempts > limits.max_retry_attempts:
                errors.append(f"Retry attempts {retry.maximum_attempts} exceeds limit {limits.max_retry_attempts}")
            if retry.maximum_attempts < 0:
                 errors.append("Negative retry attempts are not allowed")

        steps_list.append(ExecutionStep(
            step_id=step_data.get("step_id", ""),
            title=step_data.get("title", ""),
            description=step_data.get("description", ""),
            step_type=step_data.get("step_type", "tool"),
            depends_on=step_data.get("depends_on", []),
            expected_output=step_data.get("expected_output", ""),
            tool_name=step_data.get("tool_name"),
            parameters=step_data.get("parameters", {}),
            approval_hint=step_data.get("approval_hint", "none"),
            retry_policy=retry,
            checkpoint_after=step_data.get("checkpoint_after", False)
        ))

    fo_data = plan_dict.get("final_output", {})
    if "type" not in fo_data or "format" not in fo_data:
        errors.append("final_output must have type and format")

    if errors:
        return False, errors, None

    plan = ExecutionPlan(
        schema_version=plan_dict["schema_version"],
        plan_id=plan_dict["plan_id"],
        task_id=plan_dict["task_id"],
        objective=plan_dict["objective"],
        completion_criteria=plan_dict["completion_criteria"],
        assumptions=plan_dict.get("assumptions", []),
        steps=steps_list,
        final_output=FinalOutput(type=fo_data["type"], format=fo_data["format"])
    )

    return True, errors, plan
