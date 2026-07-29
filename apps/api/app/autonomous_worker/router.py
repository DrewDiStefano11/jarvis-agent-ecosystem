from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request

from app.agent_runtime.authorization import RuntimeActorContext
from app.autonomous_worker.service import AutonomousWorkerService
from app.models.autonomous_worker import ModelExecutionResult
from app.models.domain import TypedApiResponse

router = APIRouter(prefix="/api/model-executions", tags=["model-executions"])


def service(request: Request) -> AutonomousWorkerService:
    return request.app.state.autonomous_worker_service


def actor(
    request: Request,
    x_jarvis_actor_id: Annotated[str | None, Header(alias="X-Jarvis-Actor-Id")] = None,
) -> RuntimeActorContext:
    return request.app.state.agent_runtime_service.authenticate_actor(x_jarvis_actor_id)


@router.get(
    "/{execution_id}",
    response_model=TypedApiResponse[ModelExecutionResult],
)
def get_execution(
    execution_id: str,
    worker_service: Annotated[AutonomousWorkerService, Depends(service)],
    runtime_actor: Annotated[RuntimeActorContext, Depends(actor)],
) -> TypedApiResponse[ModelExecutionResult]:
    return TypedApiResponse(data=worker_service.read_result_authorized(execution_id, runtime_actor))


@router.get(
    "",
    response_model=TypedApiResponse[list[ModelExecutionResult]],
)
def list_executions(
    task_id: Annotated[str, Query(alias="taskId", min_length=1, max_length=120)],
    worker_service: Annotated[AutonomousWorkerService, Depends(service)],
    runtime_actor: Annotated[RuntimeActorContext, Depends(actor)],
) -> TypedApiResponse[list[ModelExecutionResult]]:
    return TypedApiResponse(
        data=worker_service.list_task_results_authorized(task_id, runtime_actor)
    )
