"""Shared contract constraints for identifiers crossing runtime, event, and audit layers.

These values are authoritative for both the Pydantic contracts and the durable
schema. Correlation IDs are preserved exactly everywhere; they are never
truncated, hashed, or normalized, and oversized values are rejected during
contract validation instead.
"""

from __future__ import annotations

MAX_CORRELATION_ID_LENGTH = 120
