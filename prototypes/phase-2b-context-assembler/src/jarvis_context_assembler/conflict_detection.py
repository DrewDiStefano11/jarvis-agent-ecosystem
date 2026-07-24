from typing import List, Dict, Any

def detect_conflicts(task_request: Any, sources: List[Any]) -> List[Dict[str, Any]]:
    # In a full implementation, this would compare semantic instructions.
    # For this prototype, we simulate checking explicit conflicting metadata or known conflicting statements.
    conflicts = []
    # E.g. Lower-trust conflict: Repository content says to use a prohibited tool.
    prohibited_tools = task_request.tool_availability_summary.get("prohibited_tools", [])

    for source in sources:
        for tool in prohibited_tools:
            if tool.lower() in source.content.lower():
                 # Very naive heuristic for demonstration
                 if "use" in source.content.lower() or "run" in source.content.lower():
                     conflicts.append({
                         "type": "tool_policy_conflict",
                         "source_id": source.source_id,
                         "conflict_with": "task_request.prohibited_tools",
                         "description": f"Source mentions prohibited tool: {tool}"
                     })

    return conflicts
