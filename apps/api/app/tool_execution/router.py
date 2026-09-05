from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.agent_runtime.authorization import RuntimeActorContext
from app.autonomous_worker.router import actor
from app.models.domain import TypedApiResponse
from app.models.tool_execution import (
    AuthorizeToolExecutionRequest,
    ToolArtifactContent,
    ToolExecutionResult,
    WorkspaceInfo,
)

router = APIRouter(prefix="/api", tags=["workspace-tools"])


@router.get("/tool-workspaces", response_model=TypedApiResponse[list[WorkspaceInfo]])
def workspaces(request: Request):
    return TypedApiResponse(data=request.app.state.tool_execution_service.workspaces())


@router.post("/tool-executions/authorize", response_model=TypedApiResponse[ToolExecutionResult])
def authorize(
    body: AuthorizeToolExecutionRequest,
    request: Request,
    runtime_actor: Annotated[RuntimeActorContext, Depends(actor)],
):
    return TypedApiResponse(
        data=request.app.state.tool_execution_service.authorize(body, runtime_actor)
    )


@router.get("/tool-executions", response_model=TypedApiResponse[list[ToolExecutionResult]])
def list_executions(
    request: Request,
    runtime_actor: Annotated[RuntimeActorContext, Depends(actor)],
    task_id: Annotated[str, Query(alias="taskId", min_length=1, max_length=120)],
):
    return TypedApiResponse(
        data=request.app.state.tool_execution_service.list_task(task_id, runtime_actor)
    )


@router.get("/tool-executions/{execution_id}", response_model=TypedApiResponse[ToolExecutionResult])
def get_execution(
    execution_id: str,
    request: Request,
    runtime_actor: Annotated[RuntimeActorContext, Depends(actor)],
):
    return TypedApiResponse(
        data=request.app.state.tool_execution_service.read(execution_id, runtime_actor)
    )


@router.get("/tool-artifacts/{artifact_id}", response_model=TypedApiResponse[ToolArtifactContent])
def get_artifact(
    artifact_id: str,
    request: Request,
    runtime_actor: Annotated[RuntimeActorContext, Depends(actor)],
):
    return TypedApiResponse(
        data=request.app.state.tool_execution_service.artifact(artifact_id, runtime_actor)
    )
