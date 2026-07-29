"""Typed, thin HTTP control plane for the runtime domain (trusted local callers)."""

from __future__ import annotations

from typing import Annotated, TypeVar

from fastapi import APIRouter, Body, Depends, Header, Request

from app.agent_runtime.authorization import RuntimeActorContext
from app.agent_runtime.service import AgentRuntimeService
from app.models.agent_runtime import (
    AbandonAgentRunCommand,
    AbandonAttemptCommand,
    AgentRunAttempt,
    AgentRunCheckpoint,
    AgentRunQuery,
    AgentRunQueryResult,
    AgentRunSnapshot,
    BeginAttemptCommand,
    BlockAgentRunCommand,
    ClaimAgentRunCommand,
    CompleteAgentRunCommand,
    CompleteAttemptCommand,
    ConfirmCancellationCommand,
    ConfirmCancellationStartCommand,
    ConfirmPauseCommand,
    CreateAgentRunCommand,
    FailAgentRunCommand,
    FailAttemptCommand,
    HeartbeatCommand,
    LineageResolution,
    QueueAgentRunCommand,
    RecordCheckpointCommand,
    RequestCancellationCommand,
    RequestPauseCommand,
    RequestRecoveryPlanCommand,
    ResumeAgentRunCommand,
    RuntimeCommandResult,
    RuntimeEventEnvelope,
    StartAttemptCommand,
    TimeoutAgentRunCommand,
    TimeoutAttemptCommand,
    UnblockAgentRunCommand,
)
from app.models.domain import TypedApiResponse

router = APIRouter(prefix="/api/agent-runtime", tags=["agent-runtime"])
DataT = TypeVar("DataT")
Command = (
    CreateAgentRunCommand
    | QueueAgentRunCommand
    | ClaimAgentRunCommand
    | BeginAttemptCommand
    | StartAttemptCommand
    | HeartbeatCommand
    | RequestPauseCommand
    | ConfirmPauseCommand
    | ResumeAgentRunCommand
    | BlockAgentRunCommand
    | UnblockAgentRunCommand
    | RequestCancellationCommand
    | ConfirmCancellationStartCommand
    | ConfirmCancellationCommand
    | RecordCheckpointCommand
    | CompleteAttemptCommand
    | FailAttemptCommand
    | TimeoutAttemptCommand
    | AbandonAttemptCommand
    | CompleteAgentRunCommand
    | FailAgentRunCommand
    | TimeoutAgentRunCommand
    | AbandonAgentRunCommand
    | RequestRecoveryPlanCommand
)


def service(request: Request) -> AgentRuntimeService:
    return request.app.state.agent_runtime_service


def actor(
    request: Request,
    x_jarvis_actor_id: Annotated[str | None, Header(alias="X-Jarvis-Actor-Id")] = None,
) -> RuntimeActorContext:
    return service(request).authenticate_actor(x_jarvis_actor_id)


def enveloped(data: DataT) -> TypedApiResponse[DataT]:
    """Wrap a typed route result in the standard successful-response envelope."""
    return TypedApiResponse[DataT](data=data)


@router.get("/runs", response_model=TypedApiResponse[AgentRunQueryResult])
def list_runs(
    request: Request,
    query: Annotated[AgentRunQuery, Depends()],
    runtime_actor: Annotated[RuntimeActorContext, Depends(actor)],
) -> TypedApiResponse[AgentRunQueryResult]:
    return enveloped(service(request).list_runs_authorized(query, runtime_actor))


@router.get("/runs/{run_id}", response_model=TypedApiResponse[AgentRunSnapshot])
def get_run(
    run_id: str,
    request: Request,
    runtime_actor: Annotated[RuntimeActorContext, Depends(actor)],
) -> TypedApiResponse[AgentRunSnapshot]:
    return enveloped(service(request).read_run_authorized(run_id, runtime_actor))


@router.get("/runs/{run_id}/events", response_model=TypedApiResponse[list[RuntimeEventEnvelope]])
def events(
    run_id: str,
    request: Request,
    runtime_actor: Annotated[RuntimeActorContext, Depends(actor)],
) -> TypedApiResponse[list[RuntimeEventEnvelope]]:
    return enveloped(list(service(request).events_authorized(run_id, runtime_actor)))


@router.get("/runs/{run_id}/attempts", response_model=TypedApiResponse[list[AgentRunAttempt]])
def attempts(
    run_id: str,
    request: Request,
    runtime_actor: Annotated[RuntimeActorContext, Depends(actor)],
) -> TypedApiResponse[list[AgentRunAttempt]]:
    return enveloped(list(service(request).attempts_authorized(run_id, runtime_actor)))


@router.get("/runs/{run_id}/checkpoints", response_model=TypedApiResponse[list[AgentRunCheckpoint]])
def checkpoints(
    run_id: str,
    request: Request,
    runtime_actor: Annotated[RuntimeActorContext, Depends(actor)],
) -> TypedApiResponse[list[AgentRunCheckpoint]]:
    return enveloped(list(service(request).checkpoints_authorized(run_id, runtime_actor)))


@router.get("/runs/{run_id}/lineage", response_model=TypedApiResponse[LineageResolution])
def lineage(
    run_id: str,
    request: Request,
    runtime_actor: Annotated[RuntimeActorContext, Depends(actor)],
) -> TypedApiResponse[LineageResolution]:
    return enveloped(service(request).lineage_authorized(run_id, runtime_actor))


@router.post("/commands", response_model=TypedApiResponse[RuntimeCommandResult])
def command(
    body: Annotated[Command, Body(discriminator="command_type")],
    request: Request,
    runtime_actor: Annotated[RuntimeActorContext, Depends(actor)],
) -> TypedApiResponse[RuntimeCommandResult]:
    return enveloped(service(request).handle_authorized(body, runtime_actor))
