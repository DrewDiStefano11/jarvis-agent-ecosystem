from typing import List
from .contracts import ContextSource
from .deduplication import get_trust_rank

def sort_sources(sources: List[ContextSource]) -> List[ContextSource]:
    """
    Sort based on:
    1. Trust level
    2. Required source status (exact_preservation)
    3. Inclusion priority
    4. Stable source ID tie-breaker
    """
    def sort_key(s: ContextSource):
        trust_rank = get_trust_rank(s.trust_level)
        exact = 0 if s.metadata.exact_preservation_required else 1
        priority = -(s.metadata.inclusion_priority or 0)
        return (trust_rank, exact, priority, s.source_id)

    return sorted(sources, key=sort_key)
