from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.agent_runtime.authorization import RuntimeActorContext
from app.autonomous_worker.router import actor
from app.models.domain import TypedApiResponse
from app.models.self_build import (
    AbandonWorkspaceRequest,
    ReserveWorkspaceRequest,
    WorkspaceIntent,
    WorkspacePlan,
    WorkspaceReservation,
)

router = APIRouter(prefix="/api/self-build/workspaces", tags=["self-build"])
Actor = Annotated[RuntimeActorContext, Depends(actor)]


@router.post("/preview", response_model=TypedApiResponse[WorkspacePlan])
def preview(body: WorkspaceIntent, request: Request, runtime_actor: Actor):
    return TypedApiResponse(
        data=request.app.state.self_build_workspace_service.preview(runtime_actor, body)
    )


@router.post("/reserve", response_model=TypedApiResponse[WorkspaceReservation])
def reserve(body: ReserveWorkspaceRequest, request: Request, runtime_actor: Actor):
    return TypedApiResponse(
        data=request.app.state.self_build_workspace_service.reserve(runtime_actor, body)
    )


@router.get("/{workspace_id}", response_model=TypedApiResponse[WorkspaceReservation])
def read(workspace_id: str, request: Request, runtime_actor: Actor):
    return TypedApiResponse(
        data=request.app.state.self_build_workspace_service.read(runtime_actor, workspace_id)
    )


@router.post("/{workspace_id}/abandon", response_model=TypedApiResponse[WorkspaceReservation])
def abandon(
    workspace_id: str, body: AbandonWorkspaceRequest, request: Request, runtime_actor: Actor
):
    return TypedApiResponse(
        data=request.app.state.self_build_workspace_service.abandon(
            runtime_actor, workspace_id, body
        )
    )
