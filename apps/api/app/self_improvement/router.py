"""Read-only operator surface. Analysis writes are CLI-only."""

import ipaddress

from fastapi import APIRouter, Depends, Query, Request

from app.core.errors import DomainError
from app.models.domain import TypedApiResponse
from app.models.self_improvement import Analysis, Comparison
from app.self_improvement.service import ImprovementService


def local_operator(request: Request):
    host = request.client.host if request.client else ""
    try:
        allowed = host == "testclient" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        allowed = False
    if not allowed:
        raise DomainError("LOCAL_OPERATOR_REQUIRED", "Local operator only", 403)


router = APIRouter(
    prefix="/api/self-improvement", tags=["improvement lab"], dependencies=[Depends(local_operator)]
)


def service(request):
    return ImprovementService(request.app.state.repository.session_factory)


@router.get("/baselines", response_model=TypedApiResponse[list[Analysis]])
def baselines(request: Request, limit: int = Query(default=20, ge=1, le=100)):
    return TypedApiResponse(data=service(request).repository.list_analyses(limit))


@router.get("/baselines/{baseline_id}", response_model=TypedApiResponse[Analysis])
def baseline(baseline_id: str, request: Request):
    try:
        return TypedApiResponse(data=service(request).repository.analysis(baseline_id))
    except ValueError as error:
        raise DomainError("SELF_IMPROVEMENT_BASELINE_NOT_FOUND", "Unknown baseline", 404) from error


@router.get(
    "/baselines/{baseline_id}/comparisons", response_model=TypedApiResponse[list[Comparison]]
)
def comparisons(baseline_id: str, request: Request):
    return TypedApiResponse(data=service(request).repository.comparisons(baseline_id))
