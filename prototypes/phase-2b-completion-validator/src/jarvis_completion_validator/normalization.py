from typing import Dict, Any, List
from copy import deepcopy

def normalize_task_input(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalizes input dictionary without altering core facts.
    - Trims whitespace from strings.
    - Ensures optional arrays are initialized to empty lists.
    - Normalizes timestamps to a standard format if needed.
    """
    normalized = deepcopy(data)

    # Fill optional empty arrays in worker_result
    if "worker_result" in normalized:
        wr = normalized["worker_result"]
        for arr_field in ["artifact_ids", "limitations", "errors", "warnings", "approval_claims"]:
            if arr_field not in wr or wr[arr_field] is None:
                wr[arr_field] = []
        if "criterion_claims" not in wr or wr["criterion_claims"] is None:
            wr["criterion_claims"] = []
        else:
            for cc in wr["criterion_claims"]:
                if "evidence_ids" not in cc or cc["evidence_ids"] is None:
                    cc["evidence_ids"] = []

    # Sort evidence references to canonicalize
    if "worker_result" in normalized and "criterion_claims" in normalized["worker_result"]:
        for cc in normalized["worker_result"]["criterion_claims"]:
            cc["evidence_ids"] = sorted(cc["evidence_ids"])

    # Ensure lists exist at root
    for list_field in ["artifacts", "evidence", "approvals", "trusted_checks"]:
        if list_field not in normalized or normalized[list_field] is None:
            normalized[list_field] = []

    return normalized
