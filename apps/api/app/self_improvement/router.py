"""Local analysis reads and explicit authorized native backlog admission."""

import ipaddress

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from app.core.errors import DomainError
from app.models.domain import TypedApiResponse
from app.models.improvement_backlog import (
    ImprovementBacklogItem,
    ImprovementBacklogSelection,
    SelectImprovementRequest,
)
from app.models.self_improvement import Analysis, Comparison
from app.self_improvement.backlog_service import ImprovementBacklogService
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


def backlog_service(request):
    return ImprovementBacklogService(
        request.app.state.repository, request.app.state.broker, request.app.state.identity_service
    )


@router.post("/backlog/select", response_model=TypedApiResponse[ImprovementBacklogSelection])
async def select_backlog(
    body: SelectImprovementRequest,
    request: Request,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
    actor_id: str | None = Header(default=None, alias="X-Jarvis-Actor-Id"),
):
    actor = request.app.state.agent_runtime_service.authenticate_actor(actor_id)
    result = await backlog_service(request).select(actor, body, idempotency_key)
    response.status_code = 201 if result.outcome == "selected" else 200
    return TypedApiResponse(data=result)


@router.get("/backlog", response_model=TypedApiResponse[list[ImprovementBacklogItem]])
def backlog(
    request: Request,
    offset: int = Query(default=0, ge=0, le=100_000),
    limit: int = Query(default=20, ge=1, le=100),
    actor_id: str | None = Header(default=None, alias="X-Jarvis-Actor-Id"),
):
    actor = request.app.state.agent_runtime_service.authenticate_actor(actor_id)
    return TypedApiResponse(
        data=backlog_service(request).list_items(actor, offset=offset, limit=limit)
    )


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
