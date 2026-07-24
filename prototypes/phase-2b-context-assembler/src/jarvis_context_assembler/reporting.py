from typing import Dict, Any
from .manifest import ContextManifest

def generate_assembly_report(manifest: ContextManifest, request_id: str) -> Dict[str, Any]:
    return {
        "task_id": manifest.task_id,
        "project_id": manifest.project_id,
        "policy_version": manifest.policy_version,
        "request_id": request_id,
        "request_hash": manifest.request_hash,
        "included_source_count": len(manifest.included_sources),
        "excluded_source_count": len(manifest.excluded_sources),
        "included_bytes": sum(s.get("size", 0) for s in manifest.included_sources),
        "estimated_tokens": manifest.budget.get("estimated_total_tokens", 0),
        "token_budget": manifest.budget.get("maximum_context_tokens", 0),
        "reserved_tokens": manifest.budget.get("reserved_output_tokens", 0),
        "redaction_count": len(manifest.redactions),
        "injection_findings": manifest.injection_findings,
        "conflicts": manifest.conflicts,
        "duplicate_sources": manifest.duplicate_sources,
        "truncated_sources": manifest.truncated_sources,
        "human_review_requirement": any(f.get("severity") == "critical" for f in manifest.injection_findings) or len(manifest.conflicts) > 0,
        "final_assembly_status": "success" if not manifest.excluded_sources else "success_with_exclusions"
    }
