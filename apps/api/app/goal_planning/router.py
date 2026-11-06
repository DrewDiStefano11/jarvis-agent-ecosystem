"""Thin local transport; all scope, SQL and native orchestration live in services."""

from typing import Annotated

from fastapi import APIRouter, Header, Request

from app.models.domain import TypedApiResponse
from app.models.goal_planning import (
    ConfigureGoalPlanning,
    GoalPlanningAdmissionView,
    GoalPlanningOperator,
    GoalPlanningOperatorSetup,
    GoalPlanningPolicyView,
    RevokeGoalPlanning,
)
from app.result_review.router import local_operator

router = APIRouter(prefix="/api/goal-planning", tags=["bounded goal planning"])


def operator(request):
    local_operator(request)
    return request.headers.get("X-Jarvis-Actor-Id")


@router.get("/operator", response_model=TypedApiResponse[GoalPlanningOperator | None])
def discover(request: Request):
    from app.autonomous_worker.provisioning import _find_actor

    local_operator(request)
    actor = _find_actor(request.app.state.identity_service, "local-goal-planning-operator")
    return TypedApiResponse(data=None if actor is None else GoalPlanningOperator(actorId=actor.id))


@router.post("/operator/setup", response_model=TypedApiResponse[GoalPlanningOperator])
def setup(body: GoalPlanningOperatorSetup, request: Request):
    local_operator(request)
    return TypedApiResponse(
        data=GoalPlanningOperator(actorId=request.app.state.goal_planning_service.setup_operator())
    )


@router.get("/policy", response_model=TypedApiResponse[GoalPlanningPolicyView])
def policy(request: Request):
    local_operator(request)
    return TypedApiResponse(data=request.app.state.goal_planning_service.policy())


@router.post("/policy", response_model=TypedApiResponse[GoalPlanningPolicyView])
def configure(
    body: ConfigureGoalPlanning,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=120)],
):
    return TypedApiResponse(
        data=request.app.state.goal_planning_service.configure(
            operator(request), body, idempotency_key
        )
    )


@router.post("/policy/{policy_id}/revoke", response_model=TypedApiResponse[GoalPlanningPolicyView])
def revoke(policy_id: str, body: RevokeGoalPlanning, request: Request):
    return TypedApiResponse(
        data=request.app.state.goal_planning_service.revoke(
            operator(request), policy_id, body.expectedPolicyHash
        )
    )


@router.get("/goals/{goal_id}", response_model=TypedApiResponse[GoalPlanningAdmissionView])
def admitted(goal_id: str, request: Request):
    return TypedApiResponse(
        data=request.app.state.goal_planning_service.admission_view(operator(request), goal_id)
    )


@router.post("/goals/{goal_id}/retry", response_model=TypedApiResponse[GoalPlanningAdmissionView])
def retry(goal_id: str, request: Request):
    actor = operator(request)
    service = request.app.state.goal_planning_service
    service.admit(actor, goal_id)
    return TypedApiResponse(data=service.admission_view(actor, goal_id))
