"""Typed, thin HTTP control plane for the runtime domain (trusted local callers)."""

from __future__ import annotations

from typing import Annotated, TypeVar

from fastapi import APIRouter, Body, Depends, Request

from app.agent_runtime.errors import RunNotFoundError
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


def enveloped(data: DataT) -> TypedApiResponse[DataT]:
    """Wrap a typed route result in the standard successful-response envelope."""
    return TypedApiResponse[DataT](data=data)


@router.get("/runs", response_model=TypedApiResponse[AgentRunQueryResult])
def list_runs(
    request: Request,
    query: Annotated[AgentRunQuery, Depends()],
) -> TypedApiResponse[AgentRunQueryResult]:
    return enveloped(service(request).repository.query_runs(query))


@router.get("/runs/{run_id}", response_model=TypedApiResponse[AgentRunSnapshot])
def get_run(run_id: str, request: Request) -> TypedApiResponse[AgentRunSnapshot]:
    snapshot = service(request).repository.load_run(run_id)
    if snapshot is None:
        raise RunNotFoundError(run_id=run_id)
    return enveloped(snapshot)


@router.get("/runs/{run_id}/events", response_model=TypedApiResponse[list[RuntimeEventEnvelope]])
def events(run_id: str, request: Request) -> TypedApiResponse[list[RuntimeEventEnvelope]]:
    return enveloped(list(service(request).repository.list_events(run_id)))


@router.get("/runs/{run_id}/attempts", response_model=TypedApiResponse[list[AgentRunAttempt]])
def attempts(run_id: str, request: Request) -> TypedApiResponse[list[AgentRunAttempt]]:
    return enveloped(list(service(request).repository.load_attempt_history(run_id)))


@router.get("/runs/{run_id}/checkpoints", response_model=TypedApiResponse[list[AgentRunCheckpoint]])
def checkpoints(run_id: str, request: Request) -> TypedApiResponse[list[AgentRunCheckpoint]]:
    return enveloped(list(service(request).repository.list_checkpoints(run_id)))


@router.get("/runs/{run_id}/lineage", response_model=TypedApiResponse[LineageResolution])
def lineage(run_id: str, request: Request) -> TypedApiResponse[LineageResolution]:
    return enveloped(service(request).resolve_lineage(run_id))


@router.post("/commands", response_model=TypedApiResponse[RuntimeCommandResult])
def command(
    body: Annotated[Command, Body(discriminator="command_type")], request: Request
) -> TypedApiResponse[RuntimeCommandResult]:
    return enveloped(service(request).handle(body))
