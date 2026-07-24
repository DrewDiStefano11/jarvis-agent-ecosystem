from typing import List, Dict, Tuple
from .contracts import ContextSource
from .hashing import hash_content
from .enums import TrustLevel

# Simple mapping for ordering trust levels
TRUST_ORDER = {
    TrustLevel.SYSTEM_POLICY: 1,
    TrustLevel.TRUSTED_CONFIGURATION: 2,
    TrustLevel.OPERATOR_INSTRUCTION: 3,
    TrustLevel.TASK_REQUEST: 4,
    TrustLevel.TRUSTED_VALIDATOR: 5,
    TrustLevel.TRUSTED_TOOL_RESULT: 6,
    TrustLevel.APPROVED_ARTIFACT: 7,
    TrustLevel.REPOSITORY_CONTENT: 8,
    TrustLevel.EXTERNAL_CONTENT: 9,
    TrustLevel.PRIOR_MODEL_OUTPUT: 10,
    TrustLevel.UNKNOWN: 11
}

def get_trust_rank(trust_level: str) -> int:
    try:
        return TRUST_ORDER[TrustLevel(trust_level)]
    except ValueError:
        return 99

def deduplicate_sources(sources: List[ContextSource]) -> Tuple[List[ContextSource], List[Dict]]:
    seen_hashes = {}
    deduplicated = []
    removed = []

    for i, source in enumerate(sources):
        # We also want to stabilize deduplication by preserving the "highest-trust source"
        # and "exact-preservation" sources.
        chash = source.content_hash or hash_content(source.content)
        if chash in seen_hashes:
            existing_idx = seen_hashes[chash]
            existing_source = deduplicated[existing_idx]

            new_rank = get_trust_rank(source.trust_level)
            existing_rank = get_trust_rank(existing_source.trust_level)

            new_exact = source.metadata.exact_preservation_required
            existing_exact = existing_source.metadata.exact_preservation_required

            # Prefer the new one if it's higher trust, or exact-preservation when the existing isn't
            if new_rank < existing_rank or (new_exact and not existing_exact):
                deduplicated[existing_idx] = source
                removed.append({
                    "source_id": existing_source.source_id,
                    "reason": "duplicate_replaced_by_higher_trust",
                    "replaced_by": source.source_id
                })
            else:
                removed.append({
                    "source_id": source.source_id,
                    "reason": "duplicate",
                    "kept_source": existing_source.source_id
                })
        else:
            seen_hashes[chash] = len(deduplicated)
            deduplicated.append(source)

    return deduplicated, removed
