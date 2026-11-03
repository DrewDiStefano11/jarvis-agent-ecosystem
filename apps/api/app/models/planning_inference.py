"""Native planning-dispatch event contract; model output supplies no authority."""

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PlanningInferenceIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    taskId: str = Field(min_length=1, max_length=200)
    stage: Literal["team_selection", "decomposition"]
    inputFingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    requestIndex: int = Field(ge=1, le=2, strict=True)
    planningScope: str = Field(pattern=r"^[a-f0-9]{64}$")
    provider: str = Field(min_length=1, max_length=120)
    model: str = Field(min_length=1, max_length=200)

    @classmethod
    def for_request(cls, task_id, stage, fingerprint, index, provider, model):
        scope = hashlib.sha256(
            json.dumps([task_id, stage, fingerprint, index], separators=(",", ":")).encode()
        ).hexdigest()
        return cls(
            taskId=task_id, stage=stage, inputFingerprint=fingerprint,
            requestIndex=index, planningScope=scope, provider=provider, model=model,
        )
