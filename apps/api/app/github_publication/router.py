"""Authorized internal publication APIs. Routes contain no SQL or Git effects."""

from fastapi import APIRouter, Query, Request

from app.core.errors import DomainError
from app.github_publication.journal import PublicationJournal
from app.github_publication.observation_service import observe
from app.github_publication.orchestrator import advance_publication
from app.models.domain import TypedApiResponse
from app.models.publication_correction import PublicationCorrectionTriage
from app.models.publication_lifecycle import (
    AdvancePublication,
    ApprovePublication,
    PreviewPublication,
    PublicationApproval,
    PublicationPlan,
    PublicationRequest,
    PublicationSnapshot,
    PublicationStatus,
)
from app.models.publication_source_link import PublicationSourceLink
from app.remote_control.router import Actor as Operator
from app.self_build.router import Actor

router = APIRouter(prefix="/api/self-build/workspaces", tags=["GitHub publication"])
operator_router = APIRouter(
    prefix="/api/remote/self-build", tags=["authenticated GitHub publication"]
)


@router.get(
    "/{workspace_id}/publication-status", response_model=TypedApiResponse[PublicationStatus]
)
def publication_status(
    workspace_id: str,
    request: Request,
    principal: Actor,
    native_command_id: str = Query(min_length=1, max_length=120),
    operation_id: str | None = Query(default=None, min_length=1, max_length=120),
):
    from app.github_publication.status import status

    native = getattr(request.app.state, "self_build_native_commit_service", None)
    if native is None:
        service(request)
        raise DomainError(
            "GITHUB_NATIVE_SERVICE_UNAVAILABLE", "Native commit service is unavailable.", 503
        )
    return TypedApiResponse(
        data=status(
            native,
            getattr(request.app.state, "github_publication_service", None),
            principal,
            workspace_id,
            native_command_id,
            operation_id,
        )
    )


def service(request):
    value = getattr(request.app.state, "github_publication_service", None)
    if value is None:
        raise DomainError(
            "GITHUB_PUBLICATION_UNCONFIGURED",
            "Publication requires operator-configured repository, native service and validation authority.",
            503,
        )
    return value


@router.get(
    "/{workspace_id}/publication/{operation_id}/source-link",
    response_model=TypedApiResponse[PublicationSourceLink],
)
def source_link(
    workspace_id: str,
    operation_id: str,
    request: Request,
    principal: Actor,
    expected_head_sha: str = Query(pattern=r"^[a-f0-9]{40}$"),
    observation_digest: str = Query(pattern=r"^[a-f0-9]{64}$"),
):
    from app.github_publication.source_link import read_source_link

    return TypedApiResponse(
        data=read_source_link(
            service(request),
            principal,
            workspace_id,
            operation_id,
            expected_head_sha,
            observation_digest,
        )
    )


@router.get(
    "/{workspace_id}/publication/{operation_id}/correction-triage",
    response_model=TypedApiResponse[PublicationCorrectionTriage],
)
def correction_triage(
    workspace_id: str,
    operation_id: str,
    request: Request,
    principal: Actor,
    expected_head_sha: str = Query(pattern=r"^[a-f0-9]{40}$"),
    observation_digest: str = Query(pattern=r"^[a-f0-9]{64}$"),
):
    from app.github_publication.correction_triage import classify
    from app.github_publication.source_link import read_source_link

    configured = service(request)
    linked = read_source_link(
        configured,
        principal,
        workspace_id,
        operation_id,
        expected_head_sha,
        observation_digest,
    )
    return TypedApiResponse(data=classify(linked, configured.publisher_ids))


@router.post(
    "/{workspace_id}/publication/preview", response_model=TypedApiResponse[PublicationPlan]
)
def preview(workspace_id: str, body: PreviewPublication, request: Request, principal: Actor):
    return TypedApiResponse(
        data=service(request).preview(
            principal, workspace_id, body.native_command_id, body.validation_journal_id
        )
    )


@operator_router.post("/publications/approve", response_model=TypedApiResponse[PublicationApproval])
def approve(body: ApprovePublication, request: Request, principal: Operator):
    return TypedApiResponse(
        data=service(request).approve(
            principal,
            body.plan,
            expected_plan_hash=body.expected_plan_hash,
            valid_for_seconds=body.valid_for_seconds,
        )
    )


@router.post("/{workspace_id}/publication", response_model=TypedApiResponse[PublicationSnapshot])
def advance(workspace_id: str, body: AdvancePublication, request: Request, principal: Actor):
    if body.plan.workspace_id != workspace_id:
        raise DomainError(
            "GITHUB_PUBLICATION_WORKSPACE_MISMATCH",
            "Publication workspace differs from the approved plan.",
            409,
        )
    return TypedApiResponse(
        data=advance_publication(service(request), principal, body.execution, body.plan)
    )


@router.get(
    "/{workspace_id}/publication/{operation_id}",
    response_model=TypedApiResponse[PublicationSnapshot],
)
def read(workspace_id: str, operation_id: str, request: Request, principal: Actor):
    # Historical receipt only; fresh readiness is returned by observation.
    return TypedApiResponse(
        data=PublicationJournal(service(request)).read(principal, workspace_id, operation_id)
    )


@router.post(
    "/{workspace_id}/publication/{operation_id}/observe",
    response_model=TypedApiResponse[PublicationSnapshot],
)
def observation(
    workspace_id: str,
    operation_id: str,
    body: PublicationRequest,
    request: Request,
    principal: Actor,
):
    return TypedApiResponse(
        data=observe(service(request), principal, workspace_id, body, operation_id)
    )
