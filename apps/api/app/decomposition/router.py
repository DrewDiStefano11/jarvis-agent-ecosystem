from fastapi import APIRouter, Request

from app.decomposition.service import DecompositionService
from app.models.decomposition import DecompositionRecord, DecompositionRequest
from app.models.domain import TypedApiResponse

router = APIRouter(prefix="/api/tasks/{task_id}/decomposition", tags=["planned work"])


def service(request: Request):
    return DecompositionService(
        request.app.state.repository,
        request.app.state.identity_service,
        request.app.state.model_router,
    )


@router.get("", response_model=TypedApiResponse[DecompositionRecord | None])
def current(task_id: str, request: Request):
    return TypedApiResponse(data=service(request).current(task_id))


@router.get("/history", response_model=TypedApiResponse[list[DecompositionRecord]])
def history(task_id: str, request: Request):
    instance = service(request)
    instance.tasks.get_task_durable(task_id)
    return TypedApiResponse(data=instance.repository.history(task_id))


@router.post("", response_model=TypedApiResponse[DecompositionRecord])
@router.post("/rebuild", response_model=TypedApiResponse[DecompositionRecord])
async def prepare(task_id: str, body: DecompositionRequest, request: Request):
    result = await service(request).prepare(task_id, body.contextAssemblyId)
    await request.app.state.broker.dispatch_pending()
    return TypedApiResponse(data=result)
