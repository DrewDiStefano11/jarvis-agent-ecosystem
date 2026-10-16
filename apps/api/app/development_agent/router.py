"""Typed development review endpoints. Handlers contain no SQL or execution grants."""

from fastapi import APIRouter, Request

from app.models.development_baseline import BaselineSummary
from app.models.development_dispatch import (
    AdmissionPlan,
    DevelopmentAdmission,
    DevelopmentIteration,
)
from app.models.development_review import (
    ApproveDevelopmentAdmission,
    CaptureDevelopmentBaseline,
    PreviewDevelopmentAdmission,
    ReadDevelopmentAdmission,
    RunDevelopmentIteration,
)
from app.models.domain import TypedApiResponse
from app.models.source_edit_planning import EditingPlanRecord
from app.remote_control.router import Actor as Operator
from app.self_build.router import Actor

router = APIRouter(prefix="/api/development/workspaces", tags=["development review"])
operator_router = APIRouter(
    prefix="/api/remote/development/workspaces", tags=["authenticated development review"]
)


@router.post("/{workspace_id}/admission/preview", response_model=TypedApiResponse[AdmissionPlan])
def preview(
    workspace_id: str, body: PreviewDevelopmentAdmission, request: Request, principal: Actor
):
    return TypedApiResponse(
        data=request.app.state.development_review_service.preview(principal, workspace_id, body)
    )


@operator_router.post(
    "/{workspace_id}/admission/approve", response_model=TypedApiResponse[DevelopmentAdmission]
)
def approve(
    workspace_id: str, body: ApproveDevelopmentAdmission, request: Request, principal: Operator
):
    return TypedApiResponse(
        data=request.app.state.development_review_service.approve(principal, workspace_id, body)
    )


@router.post(
    "/{workspace_id}/admission/read", response_model=TypedApiResponse[DevelopmentAdmission]
)
def read(workspace_id: str, body: ReadDevelopmentAdmission, request: Request, principal: Actor):
    return TypedApiResponse(
        data=request.app.state.development_review_service.read(principal, workspace_id, body)
    )


@router.post("/{workspace_id}/iterations", response_model=TypedApiResponse[DevelopmentIteration])
async def iterate(
    workspace_id: str, body: RunDevelopmentIteration, request: Request, principal: Actor
):
    return TypedApiResponse(
        data=await request.app.state.development_executor.execute(
            principal,
            workspace_id,
            body.authority,
            body.selection,
            body.command_id,
        )
    )


@router.post(
    "/{workspace_id}/iterations/{dispatch_id}/source-review",
    response_model=TypedApiResponse[EditingPlanRecord],
)
def source_review(
    workspace_id: str,
    dispatch_id: str,
    body: ReadDevelopmentAdmission,
    request: Request,
    principal: Actor,
):
    return TypedApiResponse(
        data=request.app.state.development_review_service.editing(
            principal, workspace_id, dispatch_id, body
        )
    )


@router.post(
    "/{workspace_id}/iterations/{dispatch_id}/baseline",
    response_model=TypedApiResponse[BaselineSummary],
)
def baseline(
    workspace_id: str,
    dispatch_id: str,
    body: CaptureDevelopmentBaseline,
    request: Request,
    principal: Actor,
):
    service = request.app.state.development_baseline_service
    return TypedApiResponse(
        data=service.summary(
            service.capture(
                principal,
                workspace_id,
                body.authority,
                dispatch_id,
                body.mutation,
            )
        )
    )
