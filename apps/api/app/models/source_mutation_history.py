"""Protected historical source mutation evidence; never execution authority."""

from typing import Literal

from pydantic import Field

from app.models.development_validation import Contract
from app.models.source_mutation import MutationRecord


class MutationHistory(Contract):
    record: MutationRecord
    native_checkpoint_present: bool = Field(strict=True)
    journal_checkpoint_acknowledged: bool = Field(strict=True)
    current_source_measured: Literal[False] = False
    execution_authorized: Literal[False] = False
