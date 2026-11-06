"""Native checks use the persisted task binding, never model or run metadata."""

from datetime import UTC, datetime
from hashlib import sha256

from sqlalchemy import select

from app.context.assembler import deterministic_hash
from app.core.errors import DomainError
from app.db.models import ContextAssemblyRow, IdentityAgentRow, TaskRow
from app.goal_planning.repository import GoalPlanningAdmissionRow, GoalPlanningPolicyRow
from app.models.agent_runtime import canonical_json
from app.models.goal_planning import direct_goal_budget


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def task_input_hash(task):
    return deterministic_hash(
        {key: getattr(task, key) for key in ("id", "projectId", "request", "title", "description")}
    )


def require_live_policy(session, policy_id, digest, *, settings=None):
    policy = session.get(GoalPlanningPolicyRow, policy_id, with_for_update=True)
    now = datetime.now(UTC)
    if (
        policy is None
        or not policy.enabled
        or policy.policy_hash != digest
        or utc(policy.expires_at) <= now
    ):
        raise DomainError(
            "GOAL_PLANNING_CONSENT_UNAVAILABLE",
            "Captured planning consent is missing, changed, expired or revoked.",
            409,
        )
    if (
        settings is not None
        and policy.worker_actor_id != settings.autonomous_worker_actor_id.strip()
    ):
        raise DomainError(
            "GOAL_PLANNING_CONSENT_UNAVAILABLE", "The captured worker configuration changed.", 409
        )
    scope = policy.scope_json
    if (
        sha256(canonical_json(scope).encode()).hexdigest() != policy.policy_hash
        or scope.get("schemaVersion") != "1.0"
        or scope.get("profile") != "direct-v1"
        or scope["id"] != policy.id
        or scope["revision"] != policy.revision
        or scope["operatorId"] != policy.operator_id
        or scope["workerActorId"] != policy.worker_actor_id
        or scope["maximumGoals"] != policy.maximum_goals
        or utc(datetime.fromisoformat(scope["expiresAt"])) != utc(policy.expires_at)
        or scope["budget"] != direct_goal_budget().model_dump(mode="json")
        or scope.get("operatorReviewRequired") is not True
    ):
        raise DomainError(
            "GOAL_PLANNING_SCOPE_CORRUPT",
            "Captured planning policy failed integrity verification.",
            409,
        )
    actor = session.scalar(
        select(IdentityAgentRow)
        .where(IdentityAgentRow.id == policy.worker_actor_id)
        .with_for_update()
    )
    if actor is None or not actor.is_enabled or actor.lifecycle_state != "active":
        raise DomainError(
            "GOAL_PLANNING_CONSENT_UNAVAILABLE", "The configured planning worker is inactive.", 409
        )
    return policy


def require_goal_consent(
    session, task_id, *, run_id=None, actor_id=None, specification=None, settings=None
):
    admission = session.scalar(
        select(GoalPlanningAdmissionRow).where(GoalPlanningAdmissionRow.task_id == task_id)
    )
    if admission is None:
        return None
    policy = require_live_policy(
        session, admission.policy_id, admission.policy_hash, settings=settings
    )
    if (
        (run_id is not None and run_id != admission.run_id)
        or (actor_id is not None and actor_id != admission.worker_actor_id)
        or admission.worker_actor_id != policy.worker_actor_id
    ):
        raise DomainError(
            "GOAL_PLANNING_SCOPE_MISMATCH",
            "This task is bound to one exact planning run and worker.",
            409,
        )
    task_row = session.get(TaskRow, task_id)
    from app.models.domain import Task

    if (
        task_row is None
        or task_input_hash(Task.model_validate(task_row.payload)) != admission.task_input_hash
    ):
        raise DomainError("GOAL_PLANNING_SCOPE_MISMATCH", "The captured goal input changed.", 409)
    context = session.get(ContextAssemblyRow, admission.context_assembly_id)
    if (
        context is None
        or context.input_hash != admission.context_input_hash
        or context.task_id != task_id
        or context.payload.get("inputHash") != admission.context_input_hash
        or context.payload.get("taskInputFingerprint") != admission.task_input_hash
        or context.payload.get("executionBudget") != policy.scope_json["budget"]
    ):
        raise DomainError(
            "GOAL_PLANNING_SCOPE_CORRUPT", "The captured planning context changed.", 409
        )
    if specification is not None:
        request = specification.autonomous_execution
        if (
            request is None
            or request.execution_type.value != "planning_review"
            or request.context_assembly_id != admission.context_assembly_id
            or request.maximum_provider_requests != 1
            or request.maximum_repair_calls != 0
            or request.maximum_output_tokens != 1536
            or request.response_format != "planning_review_json_v1"
            or specification.maximum_permitted_attempts != 1
            or request.maximum_execution_seconds > 300
            or request.provider_preference is not None
            or request.model_name is not None
            or request.verification_criteria
            or request.correction_policy is not None
            or request.coordinator_verification is not None
        ):
            raise DomainError(
                "GOAL_PLANNING_SCOPE_MISMATCH",
                "The runtime exceeds the captured direct planning scope.",
                409,
            )
    return admission


def requires_goal_review(session, task_id):
    admission = session.scalar(
        select(GoalPlanningAdmissionRow).where(GoalPlanningAdmissionRow.task_id == task_id)
    )
    if admission is None:
        return False
    policy = session.get(GoalPlanningPolicyRow, admission.policy_id)
    if (
        policy is None
        or policy.policy_hash != admission.policy_hash
        or sha256(canonical_json(policy.scope_json).encode()).hexdigest() != admission.policy_hash
        or policy.scope_json.get("operatorReviewRequired") is not True
    ):
        raise DomainError(
            "GOAL_PLANNING_SCOPE_CORRUPT",
            "The saved operator-review requirement failed integrity verification.",
            409,
        )
    return True


def require_unbound_goal_work(repository, task_id):
    sessions = getattr(repository, "session_factory", None)
    if sessions is None:
        return
    with sessions() as session:
        if session.scalar(
            select(GoalPlanningAdmissionRow.goal_id).where(
                GoalPlanningAdmissionRow.task_id == task_id
            )
        ):
            raise DomainError(
                "GOAL_PLANNING_SCOPE_MISMATCH",
                "This task already has an exact captured context and direct planning run. Use its linked goal.",
                409,
            )
