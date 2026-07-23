import json
from pathlib import Path
import os
from copy import deepcopy

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"

def load_example(name: str) -> dict:
    with open(EXAMPLES_DIR / f"{name}.json", 'r', encoding='utf-8') as f:
        return json.load(f)

def merge_task_and_result(task_name: str, result_name: str, artifacts_name: str = None) -> dict:
    data = load_example(task_name)
    result = load_example(result_name)
    data["worker_result"] = result.get("worker_result", result)
    if "evidence" in result:
        data["evidence"] = result["evidence"]
    if "approvals" in result:
        data["approvals"] = result["approvals"]
    if "trusted_checks" in result:
        data["trusted_checks"] = result["trusted_checks"]

    if artifacts_name:
        arts = load_example(artifacts_name)
        data["artifacts"] = arts.get("artifacts", arts)

    return deepcopy(data)
