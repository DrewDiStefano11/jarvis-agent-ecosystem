"""Authenticated operator HTTP surface; SQL and lifecycle policy stay in services."""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header, Query, Request

from app.agent_runtime.authorization import RuntimeActorContext
from app.core.errors import DomainError
from app.models.agent_runtime import (
    AgentRunQuery,
    AgentRunQueryResult,
    AgentRunSnapshot,
    RuntimeCommandResult,
)
from app.models.autonomous_worker import ModelExecutionResult
from app.models.decomposition import DecompositionRecord
from app.models.domain import CreateTaskRequest, Task, TypedApiResponse
from app.models.remote_control import (
    RemoteAgentPage,
    RemoteAuditPage,
    RemoteGoalPage,
    RemoteRuntimeCommand,
    RemoteSystemStatus,
)
from app.models.self_build import (
    ApproveWorkspaceAbandonRequest,
    ApproveWorkspaceRequest,
    WorkspaceAbandonApproval,
    WorkspaceApproval,
)
from app.remote_control.service import RemoteControlService

router = APIRouter(prefix="/api/remote", tags=["authenticated remote operation"])


def service(request: Request) -> RemoteControlService:
    return request.app.state.remote_control_service


def actor(request: Request) -> RuntimeActorContext:
    credentials = request.headers.getlist("authorization")
    claimed = request.headers.getlist("x-jarvis-actor-id")
    if len(credentials) != 1 or len(claimed) > 1:
        raise DomainError(
            "REMOTE_AUTHENTICATION_REQUIRED", "One bearer credential is required.", 401
        )
    return service(request).access.authenticate(
        credentials[0],
        secure_transport=request.scope.get("scheme") == "https",
        claimed_actor_id=claimed[0] if claimed else None,
    )


Actor = Annotated[RuntimeActorContext, Depends(actor)]


@router.post("/goals", response_model=TypedApiResponse[Task], status_code=201)
async def submit_goal(
    body: CreateTaskRequest,
    request: Request,
    principal: Actor,
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key", min_length=1, max_length=200, pattern=r"^[^\x00-\x1f\x7f]+$"
        ),
    ],
):
    return TypedApiResponse(
        data=await service(request).submit_goal(principal, body, idempotency_key)
    )


@router.get("/goals", response_model=TypedApiResponse[RemoteGoalPage])
def list_goals(
    request: Request,
    principal: Actor,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return TypedApiResponse(data=service(request).list_goals(principal, offset=offset, limit=limit))


@router.get("/goals/{task_id}", response_model=TypedApiResponse[Task])
def inspect_goal(task_id: str, request: Request, principal: Actor):
    return TypedApiResponse(data=service(request).inspect_goal(principal, task_id))


@router.post("/goals/{task_id}/cancel", response_model=TypedApiResponse[Task])
async def cancel_goal(task_id: str, request: Request, principal: Actor):
    return TypedApiResponse(data=await service(request).cancel_goal(principal, task_id))


@router.post("/runtime/commands", response_model=TypedApiResponse[RuntimeCommandResult])
def runtime_command(
    body: Annotated[RemoteRuntimeCommand, Body(discriminator="command_type")],
    request: Request,
    principal: Actor,
):
    return TypedApiResponse(data=service(request).runtime_command(principal, body))


@router.get("/runtime/runs", response_model=TypedApiResponse[AgentRunQueryResult])
def list_runs(request: Request, principal: Actor, query: Annotated[AgentRunQuery, Depends()]):
    instance = service(request)
    instance.access.authorize(principal, "read")
    return TypedApiResponse(data=instance.runtime.list_runs_authorized(query, principal))


@router.get("/runtime/runs/{run_id}", response_model=TypedApiResponse[AgentRunSnapshot])
def inspect_run(run_id: str, request: Request, principal: Actor):
    instance = service(request)
    instance.access.authorize(principal, "read")
    return TypedApiResponse(data=instance.runtime.read_run_authorized(run_id, principal))


@router.get("/goals/{task_id}/graph", response_model=TypedApiResponse[DecompositionRecord | None])
def inspect_graph(task_id: str, request: Request, principal: Actor):
    return TypedApiResponse(data=service(request).inspect_graph(principal, task_id))


@router.get("/goals/{task_id}/audit", response_model=TypedApiResponse[RemoteAuditPage])
def goal_audit(
    task_id: str,
    request: Request,
    principal: Actor,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return TypedApiResponse(
        data=service(request).goal_audit(principal, task_id, offset=offset, limit=limit)
    )


@router.get(
    "/runtime/runs/{run_id}/result", response_model=TypedApiResponse[ModelExecutionResult | None]
)
def inspect_result(run_id: str, request: Request, principal: Actor):
    return TypedApiResponse(data=service(request).inspect_result(principal, run_id))


@router.get("/status", response_model=TypedApiResponse[RemoteSystemStatus])
def status(request: Request, principal: Actor):
    return TypedApiResponse(data=service(request).status(principal))


@router.post("/system/emergency-stop", response_model=TypedApiResponse[RemoteSystemStatus])
async def emergency_stop(request: Request, principal: Actor):
    return TypedApiResponse(
        data=await service(request).system_control(
            principal, request.app.state.simulator, stop=True
        )
    )


@router.post("/system/resume", response_model=TypedApiResponse[RemoteSystemStatus])
async def system_resume(request: Request, principal: Actor):
    return TypedApiResponse(
        data=await service(request).system_control(
            principal, request.app.state.simulator, stop=False
        )
    )


@router.get("/agents", response_model=TypedApiResponse[RemoteAgentPage])
def active_agents(
    request: Request,
    principal: Actor,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return TypedApiResponse(
        data=service(request).active_agents(principal, offset=offset, limit=limit)
    )


@router.post("/self-build/workspaces/approve", response_model=TypedApiResponse[WorkspaceApproval])
def approve_workspace(body: ApproveWorkspaceRequest, request: Request, principal: Actor):
    return TypedApiResponse(
        data=request.app.state.self_build_workspace_service.approve(principal, body)
    )


@router.post(
    "/self-build/workspaces/abandon/approve",
    response_model=TypedApiResponse[WorkspaceAbandonApproval],
)
def approve_workspace_abandon(
    body: ApproveWorkspaceAbandonRequest, request: Request, principal: Actor
):
    return TypedApiResponse(
        data=request.app.state.self_build_workspace_service.approve_abandon(principal, body)
    )
