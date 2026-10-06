"""Explicit frozen authority for one bounded planning-result correction."""

from typing import Literal

from pydantic import Field

from app.models.verification import VerificationContract


class PlanningCorrectionPolicy(VerificationContract):
    policy_version: Literal["planning-correction-1"] = "planning-correction-1"
    maximum_corrections: Literal[1] = 1
    # Two planning cycles, each with at most two worker and two critic dispatches.
    # Coordinator children keep their own native aggregate budget.
    maximum_model_dispatches: Literal[8] = 8
    maximum_elapsed_seconds: int = Field(default=600, ge=1, le=3600)
