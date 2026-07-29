"""Shared contract constraints for identifiers crossing runtime, event, and audit layers.

Correlation IDs are preserved exactly everywhere; they are never truncated,
hashed, or normalized, and oversized values are rejected at validation boundaries.
"""

from __future__ import annotations

MAX_CORRELATION_ID_LENGTH = 120
