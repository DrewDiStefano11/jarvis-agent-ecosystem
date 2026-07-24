import math
from typing import Optional

def estimate_tokens(text: str) -> int:
    """Conservative fallback token estimate."""
    return math.ceil(len(text) / 3.5)

class BudgetTracker:
    def __init__(self, maximum_context_tokens: int, reserved_output_tokens: int):
        self.maximum_context_tokens = maximum_context_tokens
        self.reserved_output_tokens = reserved_output_tokens
        self.used_tokens = 0

    @property
    def available_tokens(self) -> int:
        return self.maximum_context_tokens - self.reserved_output_tokens - self.used_tokens

    def can_fit(self, text: str) -> bool:
        return estimate_tokens(text) <= self.available_tokens

    def add(self, text: str):
        self.used_tokens += estimate_tokens(text)
