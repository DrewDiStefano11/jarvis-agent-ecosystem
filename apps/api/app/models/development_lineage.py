"""Independent correction-workspace binding. Root budgets never reset."""

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from app.models.development_agent import ContextSelection
from app.models.development_dispatch import AdmissionPlan, DevelopmentAdmission, utc
from app.models.development_validation import Contract, Digest
from app.models.self_build import Identifier
from app.models.workspace_creation import CreateWorkspaceRequest


class LineagePlan(Contract):
    schema_version: Literal["1.0"] = "1.0"
    ordinal: int = Field(ge=1, le=7, strict=True)
    origin_admission_digest: Digest
    parent_admission_digest: Digest
    admission_plan: AdmissionPlan
    plan_hash: Digest


class LineageRecord(Contract):
    plan: LineagePlan
    admission: DevelopmentAdmission
    approval_audit_id: Identifier
    approved_at: datetime
    _utc = field_validator("approved_at")(utc)


class PreviewCorrectionWorkspace(Contract):
    authority: CreateWorkspaceRequest
    baseline_id: Digest
    repair_selection: ContextSelection


class ApproveCorrectionWorkspace(PreviewCorrectionWorkspace):
    execution_actor_id: Identifier
    expected_plan_hash: Digest
