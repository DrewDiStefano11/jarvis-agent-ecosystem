from fastapi import APIRouter, Request

from app.models.domain import TypedApiResponse
from app.models.office import OfficeCommand, OfficeCommandResult, OfficeSnapshot

router = APIRouter(prefix="/api/office", tags=["local operator office"])


@router.get("", response_model=TypedApiResponse[OfficeSnapshot])
def office(request: Request):
    return TypedApiResponse(data=request.app.state.office_service.snapshot())


@router.post(
    "/identities/{identity_id}/commands", response_model=TypedApiResponse[OfficeCommandResult]
)
async def command(identity_id: str, body: OfficeCommand, request: Request):
    result = request.app.state.office_service.command(identity_id, body)
    request.app.state.repository.refresh_event_cursor()
    await request.app.state.broker.dispatch_pending()
    return TypedApiResponse(data=result)
