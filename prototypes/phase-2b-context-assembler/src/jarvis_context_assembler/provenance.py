from typing import Optional
from .contracts import ContextSource
from .hashing import hash_content
from .enums import ExclusionReason

def verify_provenance(source: ContextSource) -> Optional[ExclusionReason]:
    if not source.source_id or not source.source_type or not source.trust_level:
        return ExclusionReason.MISSING_PROVENANCE

    actual_hash = hash_content(source.content)
    if actual_hash != source.content_hash:
        return ExclusionReason.INVALID_HASH

    return None
