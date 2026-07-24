from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

@dataclass
class ContextSourceMetadata:
    project_id: Optional[str] = None
    approved: Optional[bool] = None
    sensitivity: Optional[str] = None
    inclusion_priority: Optional[int] = None
    truncation_allowed: Optional[bool] = None
    exact_preservation_required: Optional[bool] = None
    additional: Dict[str, Any] = field(default_factory=dict)

@dataclass
class ContextSource:
    source_id: str
    source_type: str
    trust_level: str
    title: str
    content: str
    content_hash: str
    relative_path: Optional[str] = None
    created_at: Optional[str] = None
    metadata: ContextSourceMetadata = field(default_factory=ContextSourceMetadata)

@dataclass
class ContextPolicy:
    policy_version: str
    estimated_token_budget: int
    allowed_source_types: List[str] = field(default_factory=list)
    allowed_trust_levels: List[str] = field(default_factory=list)
    maximum_source_count: Optional[int] = None
    reserved_output_tokens: Optional[int] = None
    cross_project_context_allowed: bool = False
    maximum_context_tokens: Optional[int] = None
    minimum_required_context: Optional[int] = None

@dataclass
class TaskRequest:
    task_id: str
    project_id: str
    original_request: str
    allowed_result_type: str
    completion_criteria: str
    tool_availability_summary: Dict[str, List[str]] = field(default_factory=dict)

@dataclass
class ModelMessage:
    role: str
    content: str

@dataclass
class ModelRequest:
    schema_version: str
    request_id: str
    task_id: str
    project_id: str
    messages: List[ModelMessage]
    request_hash: str
    generation: Dict[str, Any] = field(default_factory=dict)
    context_manifest_id: Optional[str] = None
