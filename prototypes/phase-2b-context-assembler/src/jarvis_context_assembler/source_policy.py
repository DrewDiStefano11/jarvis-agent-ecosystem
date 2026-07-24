from typing import Optional
from .contracts import ContextSource, ContextPolicy
from .enums import ExclusionReason

def validate_source_against_policy(source: ContextSource, policy: ContextPolicy, task_project_id: str) -> Optional[ExclusionReason]:
    if source.source_type not in policy.allowed_source_types:
        return ExclusionReason.SOURCE_TYPE_DENIED

    if source.trust_level not in policy.allowed_trust_levels:
        return ExclusionReason.TRUST_LEVEL_DENIED

    source_project = source.metadata.project_id
    if source_project and source_project != task_project_id:
        if not policy.cross_project_context_allowed:
            return ExclusionReason.WRONG_PROJECT

    if hasattr(source.metadata, 'approved') and source.metadata.approved is False:
         return ExclusionReason.NOT_APPROVED

    return None
