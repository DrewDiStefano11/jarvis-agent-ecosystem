"""Typed, thin HTTP control plane for the runtime domain (trusted local callers)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Request

from app.agent_runtime.service import AgentRuntimeService
from app.models.agent_runtime import (
    AbandonAgentRunCommand,
    AbandonAttemptCommand,
    AgentRunAttempt,
    AgentRunCheckpoint,
    AgentRunQuery,
    AgentRunQueryResult,
    AgentRunSnapshot,
    AgentRunState,
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

router = APIRouter(prefix="/api/agent-runtime", tags=["agent-runtime"])
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


@router.get("/runs", response_model=AgentRunQueryResult)
def list_runs(
    request: Request,
    state: AgentRunState | None = None,
    agent_id: str | None = None,
    task_id: str | None = None,
    parent_run_id: str | None = None,
    terminal: bool | None = None,
    offset: int = 0,
    limit: int = 50,
):
    return service(request).repository.query_runs(
        AgentRunQuery(
            state=state,
            agent_id=agent_id,
            task_id=task_id,
            parent_run_id=parent_run_id,
            terminal=terminal,
            offset=offset,
            limit=limit,
        )
    )


@router.get("/runs/{run_id}", response_model=AgentRunSnapshot)
def get_run(run_id: str, request: Request):
    x = service(request).repository.load_run(run_id)
    from app.agent_runtime.errors import RunNotFoundError

    if x is None:
        raise RunNotFoundError(run_id=run_id)
    return x


@router.get("/runs/{run_id}/events", response_model=list[RuntimeEventEnvelope])
def events(run_id: str, request: Request):
    return service(request).repository.list_events(run_id)


@router.get("/runs/{run_id}/attempts", response_model=list[AgentRunAttempt])
def attempts(run_id: str, request: Request):
    return service(request).repository.load_attempt_history(run_id)


@router.get("/runs/{run_id}/checkpoints", response_model=list[AgentRunCheckpoint])
def checkpoints(run_id: str, request: Request):
    return service(request).repository.list_checkpoints(run_id)


@router.get("/runs/{run_id}/lineage", response_model=LineageResolution)
def lineage(run_id: str, request: Request):
    return service(request).resolve_lineage(run_id)


@router.post("/commands", response_model=RuntimeCommandResult)
def command(body: Annotated[Command, Body(discriminator="command_type")], request: Request):
    return service(request).handle(body)
