"""Authenticated review inputs; never accept source observations or model responses."""

from app.models.development_agent import ContextSelection
from app.models.development_dispatch import DevelopmentLimits
from app.models.development_validation import Contract, Digest
from app.models.self_build import Identifier
from app.models.source_mutation import ApplySourceMutation
from app.models.workspace_creation import CreateWorkspaceRequest


class PreviewDevelopmentAdmission(Contract):
    authority: CreateWorkspaceRequest
    selection: ContextSelection
    limits: DevelopmentLimits


class ApproveDevelopmentAdmission(PreviewDevelopmentAdmission):
    execution_actor_id: Identifier
    expected_plan_hash: Digest


class ReadDevelopmentAdmission(Contract):
    authority: CreateWorkspaceRequest


class RunDevelopmentIteration(ReadDevelopmentAdmission):
    selection: ContextSelection
    command_id: Identifier


class CaptureDevelopmentBaseline(ReadDevelopmentAdmission):
    mutation: ApplySourceMutation
