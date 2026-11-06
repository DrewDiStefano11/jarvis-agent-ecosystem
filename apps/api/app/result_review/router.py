"""Local operator result review; transport contains no SQL."""

from fastapi import APIRouter, Depends, Request, Response

from app.autonomous_worker.provisioning import _find_actor
from app.core.errors import DomainError
from app.models.domain import TypedApiResponse
from app.models.result_review import (
    ResultReviewer,
    ResultReviewerSetup,
    ResultReviewItem,
    ResultReviewReceipt,
    ReviewedArtifact,
    ReviewResultCommand,
)
from app.provider_configuration.router import require_local_operator_origin
from app.result_review.provisioning import configure_result_reviewer


def private_response(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(
    prefix="/api/results", tags=["result review"], dependencies=[Depends(private_response)]
)


def local_operator(request):
    try:
        require_local_operator_origin(request)
    except DomainError as error:
        raise DomainError(
            "RESULT_REVIEW_LOCAL_OPERATOR_REQUIRED",
            "Use the local operator workspace to review saved results.",
            403,
        ) from error


def actor(request):
    local_operator(request)
    identifier = request.headers.get("X-Jarvis-Actor-Id")
    if not identifier:
        raise DomainError("RESULT_REVIEW_PERMISSION_DENIED", "A result reviewer is required.", 403)
    return identifier


@router.post("/operator/setup", response_model=TypedApiResponse[ResultReviewer])
def setup(body: ResultReviewerSetup, request: Request):
    local_operator(request)
    return TypedApiResponse(
        data=ResultReviewer(actorId=configure_result_reviewer(request.app.state.identity_service))
    )


@router.get("/operator", response_model=TypedApiResponse[ResultReviewer | None])
def discover(request: Request):
    local_operator(request)
    reviewer = _find_actor(request.app.state.identity_service, "local-result-reviewer")
    return TypedApiResponse(data=None if reviewer is None else ResultReviewer(actorId=reviewer.id))


@router.get("/reviews", response_model=TypedApiResponse[list[ResultReviewItem]])
def pending(request: Request):
    return TypedApiResponse(data=request.app.state.result_review_service.pending(actor(request)))


@router.post(
    "/executions/{execution_id}/review", response_model=TypedApiResponse[ResultReviewReceipt]
)
def decide(execution_id: str, body: ReviewResultCommand, request: Request):
    if execution_id != body.execution_id:
        raise DomainError("RESULT_REVIEW_CONFLICT", "Review execution does not match the URL.", 409)
    return TypedApiResponse(
        data=request.app.state.result_review_service.decide(actor(request), body)
    )


@router.get("/artifacts/{artifact_id}/content")
def content(artifact_id: str, request: Request):
    content = request.app.state.result_review_service.artifact_content(actor(request), artifact_id)
    return Response(
        content=content,
        media_type="application/json",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="{artifact_id}.json"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/artifacts", response_model=TypedApiResponse[list[ReviewedArtifact]])
def artifacts(request: Request):
    return TypedApiResponse(data=request.app.state.result_review_service.artifacts(actor(request)))
