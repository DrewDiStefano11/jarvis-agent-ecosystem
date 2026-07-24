from typing import Tuple, Optional
from .enums import TruncationStrategy
from .token_budget import estimate_tokens

def truncate_content(content: str, available_tokens: int, strategy: str = TruncationStrategy.TAIL) -> Tuple[str, bool]:
    if estimate_tokens(content) <= available_tokens:
        return content, False

    if strategy == TruncationStrategy.REJECT:
        return content, True

    # Heuristic: roughly 3.5 chars per token
    available_chars = int(available_tokens * 3.5)

    # We must also account for truncation markers
    marker = "\n...[TRUNCATED]...\n"
    marker_len = len(marker)

    if available_chars <= marker_len:
         # Cannot even fit marker, reject
         return content, True

    safe_chars = available_chars - marker_len

    if strategy == TruncationStrategy.TAIL:
        return content[:safe_chars] + marker, False
    elif strategy == TruncationStrategy.HEAD:
        return marker + content[-safe_chars:], False
    elif strategy == TruncationStrategy.HEAD_AND_TAIL:
        half = safe_chars // 2
        return content[:half] + marker + content[-half:], False

    # Fallback for others
    return content[:safe_chars] + marker, False
