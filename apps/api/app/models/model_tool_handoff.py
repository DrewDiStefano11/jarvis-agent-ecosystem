"""Review-only lineage for a future trusted execution adapter. Not an approval DTO."""

from typing import Literal

from pydantic import Field

from app.models.model_tools import MAX_TOOL_CALLS, ModelToolContract, ModelToolRequest


class ModelToolHandoff(ModelToolContract):
    """Trusted service attaches runtime lineage after validating/persisting the response.

    Identity fields are references, not capabilities. The existing runtime must load
    and validate their relationships and current RBAC/lease/emergency state. There
    is intentionally no dispatcher and no accepted execution route for this DTO.
    """

    schema_version: Literal["1.0"] = "1.0"
    task_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,120}$")
    run_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,120}$")
    attempt_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,120}$")
    checkpoint_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,120}$")
    tool_request_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,120}$")
    response_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    calls: list[ModelToolRequest] = Field(min_length=1, max_length=MAX_TOOL_CALLS)
    recovery_state: Literal["requested", "acknowledgement_uncertain", "result_recorded"]
