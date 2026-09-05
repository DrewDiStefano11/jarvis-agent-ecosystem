"""Operator-only setup within the existing trusted-loopback API boundary."""

from fastapi import APIRouter, Query, Request

from app.autonomous_worker.provisioning import check_task_actor_configured, configure_task_actor
from app.models.autonomous_worker import LocalPlanningSetupRequest, LocalPlanningSetupResult
from app.models.domain import TypedApiResponse

router = APIRouter(prefix="/api/local-planning", tags=["local planning setup"])


@router.get("/setup", response_model=TypedApiResponse[LocalPlanningSetupResult])
def check_local_planner(
    request: Request, taskId: str = Query(...)
) -> TypedApiResponse[LocalPlanningSetupResult]:
    configured = request.app.state.settings.autonomous_worker_actor_id.strip()
    key = (
        request.app.state.identity_service.get_agent(configured).stable_key
        if configured
        else "local-planning-worker"
    )
    actor_id, is_ready = check_task_actor_configured(request.app, taskId, key)
    return TypedApiResponse(
        data=LocalPlanningSetupResult(
            taskId=taskId, actorId=actor_id or "", workerActorConfigured=is_ready
        )
    )


@router.post("/setup", response_model=TypedApiResponse[LocalPlanningSetupResult])
def prepare_local_planner(
    body: LocalPlanningSetupRequest, request: Request
) -> TypedApiResponse[LocalPlanningSetupResult]:
    # Reuse the explicitly configured worker identity when one exists. Never
    # create a different identity that the configured worker cannot execute as.
    configured = request.app.state.settings.autonomous_worker_actor_id.strip()
    key = (
        request.app.state.identity_service.get_agent(configured).stable_key
        if configured
        else "local-planning-worker"
    )
    actor_id = configure_task_actor(request.app, body.taskId, key)
    return TypedApiResponse(
        data=LocalPlanningSetupResult(
            taskId=body.taskId, actorId=actor_id, workerActorConfigured=bool(configured)
        )
    )
