"""Save an explicit draft alongside one native task, without context/model dispatch."""

from uuid import uuid4

from app.models.domain import CreateTaskRequest
from app.models.goals import CreateGoalRequest, Goal, GoalMessage
from app.services.task_creation import prepare_task_creation


def prepare_goal(body: CreateGoalRequest):
    title = " ".join(body.request.split())[:160]
    task = prepare_task_creation(CreateTaskRequest(title=title, description=body.request))
    task.statusMessage = "Saved draft — execution has not been authorized or queued"
    goal = Goal(
        id=f"goal-{uuid4().hex}",
        conversationId=f"conversation-{uuid4().hex}",
        taskId=task.id,
        title=title,
        createdAt=task.createdAt,
        messages=[
            GoalMessage(id=f"message-{uuid4().hex}", content=body.request, createdAt=task.createdAt)
        ],
    )
    return goal, task


async def save_goal(broker, goal, task, idempotency, *, domain_effects=None, actor_id=None):
    await broker.emit(
        "task.created",
        {"task": task.model_dump(mode="json"), "goalId": goal.id},
        task.id,
        source="operator",
        correlation_id=goal.id,
        audit={
            "summary": "Captured goal under bounded planning consent"
            if goal.planning
            else "Saved goal draft; no execution admitted",
            **({"actorIdentityId": actor_id} if actor_id else {}),
            **(
                {
                    "payload": {
                        "policyId": goal.planning.policyId,
                        "policyHash": goal.planning.policyHash,
                    }
                }
                if goal.planning
                else {}
            ),
        },
        created_task=task,
        created_goal=goal,
        idempotency=idempotency,
        **({"domain_effects": domain_effects} if domain_effects is not None else {}),
    )
