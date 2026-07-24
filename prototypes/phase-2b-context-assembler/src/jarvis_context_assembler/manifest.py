from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

@dataclass
class ContextManifest:
    schema_version: str = "1.0"
    manifest_id: str = ""
    task_id: str = ""
    project_id: str = ""
    policy_version: str = ""
    included_sources: List[Dict[str, Any]] = field(default_factory=list)
    excluded_sources: List[Dict[str, Any]] = field(default_factory=list)
    redactions: List[Dict[str, Any]] = field(default_factory=list)
    injection_findings: List[Dict[str, Any]] = field(default_factory=list)
    conflicts: List[Dict[str, Any]] = field(default_factory=list)
    duplicate_sources: List[Dict[str, Any]] = field(default_factory=list)
    truncated_sources: List[Dict[str, Any]] = field(default_factory=list)
    budget: Dict[str, Any] = field(default_factory=dict)
    request_hash: str = ""
