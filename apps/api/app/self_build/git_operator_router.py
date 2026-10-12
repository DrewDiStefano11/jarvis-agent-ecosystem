"""Authenticated operator approval of one exact bounded Git inspection plan."""

from fastapi import APIRouter, Request

from app.models.domain import TypedApiResponse
from app.models.git_inspection import (
    ApproveRepositoryInspection,
    RepositoryInspectionApproval,
    RepositoryInspectionPlan,
)
from app.remote_control.router import Actor

router = APIRouter(prefix="/api/remote/self-build", tags=["authenticated self-build inspection"])


@router.post(
    "/repository-inspections/approve", response_model=TypedApiResponse[RepositoryInspectionApproval]
)
def approve(body: ApproveRepositoryInspection, request: Request, principal: Actor):
    return TypedApiResponse(data=request.app.state.self_build_git_service.approve(principal, body))


@router.post(
    "/workspaces/{workspace_id}/repository-inspections/preview",
    response_model=TypedApiResponse[RepositoryInspectionPlan],
)
def preview(workspace_id: str, request: Request, principal: Actor):
    return TypedApiResponse(
        data=request.app.state.self_build_git_service.preview(principal, workspace_id)
    )
