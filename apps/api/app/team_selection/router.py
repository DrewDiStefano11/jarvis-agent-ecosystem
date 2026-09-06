from fastapi import APIRouter, Request

from app.models.domain import TypedApiResponse
from app.models.team_selection import TeamSelectionRecord
from app.team_selection.service import TeamSelectionService

router = APIRouter(prefix="/api/tasks/{taskId}/team-selection", tags=["team selection"])


@router.get("", response_model=TypedApiResponse[TeamSelectionRecord | None])
def get_team_selection(
    taskId: str, request: Request
) -> TypedApiResponse[TeamSelectionRecord | None]:
    repository = request.app.state.repository()
    task = repository.get_task_durable(taskId)
    return TypedApiResponse(data=task.teamSelection)


@router.post("", response_model=TypedApiResponse[TeamSelectionRecord])
async def trigger_team_selection(
    taskId: str, request: Request
) -> TypedApiResponse[TeamSelectionRecord]:
    repository = request.app.state.repository()
    task = repository.get_task_durable(taskId)

    if task.teamSelection and task.teamSelection.status == "completed":
        return TypedApiResponse(data=task.teamSelection)

    team_selector = TeamSelectionService(
        repository=repository,
        identity_service=request.app.state.identity_service,
        model_router=request.app.state.model_router,
        event_broker=request.app.state.broker,
    )
    task = await team_selector.assign_team(task)
    assert task.teamSelection is not None
    return TypedApiResponse(data=task.teamSelection)


@router.post("/reselect", response_model=TypedApiResponse[TeamSelectionRecord])
async def trigger_team_reselection(
    taskId: str, request: Request
) -> TypedApiResponse[TeamSelectionRecord]:
    repository = request.app.state.repository()
    task = repository.get_task_durable(taskId)

    # Force reselection by clearing the current one
    task.teamSelection = None

    team_selector = TeamSelectionService(
        repository=repository,
        identity_service=request.app.state.identity_service,
        model_router=request.app.state.model_router,
        event_broker=request.app.state.broker,
    )
    task = await team_selector.assign_team(task)
    assert task.teamSelection is not None
    return TypedApiResponse(data=task.teamSelection)
