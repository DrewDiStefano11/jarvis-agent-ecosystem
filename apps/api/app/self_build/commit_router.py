"""Typed native preparation endpoints. No SQL or Git effects in handlers."""

from fastapi import APIRouter, Request

from app.models.domain import TypedApiResponse
from app.models.git_commit_authorization import (
    ApproveCommitInspection,
    ApproveNativeCommitPreparation,
    CommitAuthorizationPlan,
    CommitInspectionApproval,
    CommitInspectionPlan,
    CommitPreparationApproval,
    CommitSelection,
    PreparedCommitRecord,
    PrepareNativeCommitRequest,
    PreviewCommitPreparation,
)
from app.remote_control.router import Actor as Operator
from app.self_build.router import Actor

router = APIRouter(prefix="/api/self-build/workspaces", tags=["commit preparation"])
operator_router = APIRouter(
    prefix="/api/remote/self-build", tags=["authenticated commit preparation"]
)


@router.post(
    "/{workspace_id}/commit-preparation/inspection/preview",
    response_model=TypedApiResponse[CommitInspectionPlan],
)
def inspection_preview(
    workspace_id: str, body: CommitSelection, request: Request, principal: Actor
):
    return TypedApiResponse(
        data=request.app.state.self_build_commit_service.inspection_plan(
            principal, workspace_id, body
        )
    )


@operator_router.post(
    "/commit-inspections/approve", response_model=TypedApiResponse[CommitInspectionApproval]
)
def inspection_approve(body: ApproveCommitInspection, request: Request, principal: Operator):
    return TypedApiResponse(
        data=request.app.state.self_build_commit_service.approve_inspection(principal, body)
    )


@router.post(
    "/{workspace_id}/commit-preparation/preview",
    response_model=TypedApiResponse[CommitAuthorizationPlan],
)
def preparation_preview(
    workspace_id: str, body: PreviewCommitPreparation, request: Request, principal: Actor
):
    return TypedApiResponse(
        data=request.app.state.self_build_commit_service.preview(principal, workspace_id, body)
    )


@operator_router.post(
    "/commit-preparations/approve", response_model=TypedApiResponse[CommitPreparationApproval]
)
def preparation_approve(
    body: ApproveNativeCommitPreparation, request: Request, principal: Operator
):
    return TypedApiResponse(
        data=request.app.state.self_build_commit_service.approve(principal, body)
    )


@router.post(
    "/{workspace_id}/commit-preparation", response_model=TypedApiResponse[PreparedCommitRecord]
)
def prepare(
    workspace_id: str, body: PrepareNativeCommitRequest, request: Request, principal: Actor
):
    return TypedApiResponse(
        data=request.app.state.self_build_commit_service.prepare(principal, workspace_id, body)
    )


@router.get(
    "/{workspace_id}/commit-preparation/{command_id}",
    response_model=TypedApiResponse[PreparedCommitRecord],
)
def read(workspace_id: str, command_id: str, request: Request, principal: Actor):
    return TypedApiResponse(
        data=request.app.state.self_build_commit_service.read(principal, workspace_id, command_id)
    )
