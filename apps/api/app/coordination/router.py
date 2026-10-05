from fastapi import APIRouter, Request

from app.models.coordination import CoordinationRecord
from app.models.domain import TypedApiResponse

router = APIRouter(prefix="/api/tasks/{task_id}/coordination", tags=["coordination"])


@router.get("", response_model=TypedApiResponse[CoordinationRecord | None])
def current(task_id: str, request: Request):
    request.app.state.repository.get_task_durable(task_id)
    return TypedApiResponse(data=request.app.state.coordinator_service.repository.current(task_id))
