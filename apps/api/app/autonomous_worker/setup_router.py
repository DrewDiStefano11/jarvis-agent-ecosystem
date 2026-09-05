"""Operator-only setup within the existing trusted-loopback API boundary."""

from fastapi import APIRouter, Request

from app.autonomous_worker.provisioning import configure_task_actor
from app.models.autonomous_worker import LocalPlanningSetupRequest, LocalPlanningSetupResult
from app.models.domain import TypedApiResponse

router = APIRouter(prefix="/api/local-planning", tags=["local planning setup"])


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
