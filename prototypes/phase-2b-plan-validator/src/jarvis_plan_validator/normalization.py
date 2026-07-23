from typing import Dict, Any, List, Tuple
from .contracts import ValidationReportNormalization

def normalize_plan_json(raw_json: Dict[str, Any]) -> Tuple[Dict[str, Any], List[ValidationReportNormalization]]:
    """
    Normalizes a plan dictionary. Safely handles whitespace, missing empty arrays,
    and enum case where possible.
    """
    normalizations = []
    normalized_json = dict(raw_json)

    # schema_version
    if "schema_version" in normalized_json:
        val = str(normalized_json["schema_version"]).strip()
        if val != str(raw_json.get("schema_version")):
            normalizations.append(ValidationReportNormalization("schema_version", str(raw_json.get("schema_version")), val, "whitespace_stripped"))
        normalized_json["schema_version"] = val

    # assumptions
    if "assumptions" not in normalized_json or normalized_json["assumptions"] is None:
        normalized_json["assumptions"] = []
        normalizations.append(ValidationReportNormalization("assumptions", "missing/null", "[]", "default_empty_array"))

    # steps
    if "steps" in normalized_json and isinstance(normalized_json["steps"], list):
        for i, step in enumerate(normalized_json["steps"]):
            if not isinstance(step, dict):
                continue

            # depends_on
            if "depends_on" not in step or step["depends_on"] is None:
                step["depends_on"] = []
                normalizations.append(ValidationReportNormalization(f"steps[{i}].depends_on", "missing/null", "[]", "default_empty_array"))

            # step_type enum case
            if "step_type" in step and isinstance(step["step_type"], str):
                lower_type = step["step_type"].lower().strip()
                if step["step_type"] != lower_type:
                    normalizations.append(ValidationReportNormalization(f"steps[{i}].step_type", step["step_type"], lower_type, "case_normalization"))
                    step["step_type"] = lower_type

            # approval_hint enum case
            if "approval_hint" in step and isinstance(step["approval_hint"], str):
                lower_hint = step["approval_hint"].lower().strip()
                if step["approval_hint"] != lower_hint:
                    normalizations.append(ValidationReportNormalization(f"steps[{i}].approval_hint", step["approval_hint"], lower_hint, "case_normalization"))
                    step["approval_hint"] = lower_hint

    # final_output
    if "final_output" in normalized_json and isinstance(normalized_json["final_output"], dict):
        fo = normalized_json["final_output"]
        if "type" in fo and isinstance(fo["type"], str):
            lower_type = fo["type"].lower().strip()
            if fo["type"] != lower_type:
                normalizations.append(ValidationReportNormalization("final_output.type", fo["type"], lower_type, "case_normalization"))
                fo["type"] = lower_type
        if "format" in fo and isinstance(fo["format"], str):
            lower_fmt = fo["format"].lower().strip()
            if fo["format"] != lower_fmt:
                normalizations.append(ValidationReportNormalization("final_output.format", fo["format"], lower_fmt, "case_normalization"))
                fo["format"] = lower_fmt

    return normalized_json, normalizations
