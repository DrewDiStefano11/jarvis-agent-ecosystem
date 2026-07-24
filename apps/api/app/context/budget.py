from __future__ import annotations

import math

TRUNCATION_MARKER = "\n...[TRUNCATED]...\n"


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 3.5)


def truncate_to_token_budget(content: str, available_tokens: int) -> str | None:
    if available_tokens <= estimate_tokens(TRUNCATION_MARKER):
        return None
    if estimate_tokens(content) <= available_tokens:
        return content

    low = 0
    high = len(content)
    while low < high:
        midpoint = (low + high + 1) // 2
        candidate = f"{content[:midpoint]}{TRUNCATION_MARKER}"
        if estimate_tokens(candidate) <= available_tokens:
            low = midpoint
        else:
            high = midpoint - 1
    if low == 0:
        return None
    return f"{content[:low]}{TRUNCATION_MARKER}"
