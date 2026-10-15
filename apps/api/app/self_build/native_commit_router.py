"""Typed native commit APIs; execution is unavailable without a trusted B consumer."""

from fastapi import APIRouter, Request

from app.models.domain import TypedApiResponse
from app.models.git_execution import (
    ApproveNativeCommitPlan,
    ExecuteNativeCommit,
    NativeCommitApproval,
    NativeCommitPlan,
    NativeCommitRecord,
    NativeCommitRecovery,
    PreviewNativeCommit,
    RenewNativeCommitApproval,
)
from app.remote_control.router import Actor as Operator
from app.self_build.commit_journal import NativeCommitJournal
from app.self_build.router import Actor

router = APIRouter(prefix="/api/self-build/workspaces", tags=["native commit execution"])
operator_router = APIRouter(
    prefix="/api/remote/self-build", tags=["authenticated native commit execution"]
)


@router.post(
    "/{workspace_id}/native-commit/preview", response_model=TypedApiResponse[NativeCommitPlan]
)
def preview(workspace_id: str, body: PreviewNativeCommit, request: Request, principal: Actor):
    return TypedApiResponse(
        data=request.app.state.self_build_native_commit_service.preview(
            principal, workspace_id, body
        )
    )


@operator_router.post(
    "/native-commits/approve", response_model=TypedApiResponse[NativeCommitApproval]
)
def approve(body: ApproveNativeCommitPlan, request: Request, principal: Operator):
    return TypedApiResponse(
        data=request.app.state.self_build_native_commit_service.approve(principal, body)
    )


@operator_router.post(
    "/native-commits/renew", response_model=TypedApiResponse[NativeCommitApproval]
)
def renew(body: RenewNativeCommitApproval, request: Request, principal: Operator):
    return TypedApiResponse(
        data=request.app.state.self_build_native_commit_service.renew_approval(principal, body)
    )


@router.post("/{workspace_id}/native-commit", response_model=TypedApiResponse[NativeCommitRecord])
def execute(workspace_id: str, body: ExecuteNativeCommit, request: Request, principal: Actor):
    gate = getattr(request.app.state, "native_commit_validation_consumer", None)
    return TypedApiResponse(
        data=request.app.state.self_build_native_commit_service.execute(
            principal, workspace_id, body, validation_gate=gate
        )
    )


@router.get(
    "/{workspace_id}/native-commit/{command_id}",
    response_model=TypedApiResponse[NativeCommitRecord],
)
def read(workspace_id: str, command_id: str, request: Request, principal: Actor):
    return TypedApiResponse(
        data=NativeCommitJournal(request.app.state.self_build_native_commit_service).read(
            principal, workspace_id, command_id
        )
    )


@router.get(
    "/{workspace_id}/native-commit/{command_id}/recovery",
    response_model=TypedApiResponse[NativeCommitRecovery],
)
def recovery(workspace_id: str, command_id: str, request: Request, principal: Actor):
    return TypedApiResponse(
        data=request.app.state.self_build_native_commit_service.recovery(
            principal, workspace_id, command_id
        )
    )
