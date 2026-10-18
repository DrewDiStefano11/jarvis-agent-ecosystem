"""Native media effects over existing audit/outbox/checkpoint infrastructure."""

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from app.models.development_validation import Contract, Digest
from app.models.validation_cleanup import StorageRegistration
from app.models.validation_execution import Identifier


class NativeDeliveryIntent(Contract):
    effect_id: Identifier
    validation_journal_id: Identifier
    registration: StorageRegistration
    execution_plan_hash: Digest
    capsule_digest: Digest
    operation: Literal["attach_media", "detach_media"]
    image_digest: Digest
    image_byte_count: int = Field(strict=True, ge=1, le=140000000)


class NativeDeliveryEntry(Contract):
    effect_id: Identifier
    sequence: int = Field(strict=True, ge=1, le=4)
    previous_digest: Digest
    kind: Literal["intent", "claimed", "observed", "uncertain", "reconciled"]
    timestamp: datetime
    intent: NativeDeliveryIntent | None = None
    observation_digest: Digest | None = None
    entry_digest: Digest

    @model_validator(mode="after")
    def shape(self):
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("DELIVERY_TIME_INVALID")
        if (self.kind == "intent") != (self.intent is not None):
            raise ValueError("DELIVERY_INTENT_INVALID")
        if (self.kind in {"observed", "reconciled"}) != (self.observation_digest is not None):
            raise ValueError("DELIVERY_OBSERVATION_INVALID")
        return self
