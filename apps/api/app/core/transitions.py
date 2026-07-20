from app.models.domain import AgentStatus

ACTIVE_STATES: set[AgentStatus] = {
    "assigned",
    "planning",
    "thinking",
    "researching",
    "executing_tool",
    "waiting_for_model",
    "waiting_for_agent",
    "waiting_for_approval",
    "reviewing",
    "retrying",
    "delivering",
}

TRANSITIONS: dict[AgentStatus, set[AgentStatus]] = {
    "idle": {"assigned", "offline", "paused"},
    "assigned": {"planning", "failed", "paused"},
    "planning": {
        "thinking",
        "researching",
        "waiting_for_agent",
        "reviewing",
        "delivering",
        "failed",
        "paused",
    },
    "thinking": {"researching", "reviewing", "delivering", "failed", "paused"},
    "researching": {
        "executing_tool",
        "waiting_for_agent",
        "reviewing",
        "delivering",
        "failed",
        "paused",
    },
    "executing_tool": {"researching", "waiting_for_agent", "failed", "paused"},
    "waiting_for_model": {"thinking", "failed", "paused"},
    "waiting_for_agent": {"researching", "reviewing", "delivering", "failed", "paused"},
    "waiting_for_approval": {"executing_tool", "failed", "paused"},
    "reviewing": {"researching", "delivering", "completed", "failed", "paused"},
    "paused": set(AgentStatus.__args__),
    "failed": {"retrying", "paused"},
    "retrying": {"researching", "planning", "failed", "paused"},
    "delivering": {"completed", "idle", "failed", "paused"},
    "completed": {"idle", "assigned", "paused"},
    "offline": {"idle"},
}


class InvalidTransitionError(ValueError):
    pass


def validate_transition(
    previous: AgentStatus, new: AgentStatus, resume_to: AgentStatus | None = None
) -> None:
    if new == previous:
        return
    if previous == "paused" and resume_to == new:
        return
    if new not in TRANSITIONS[previous]:
        raise InvalidTransitionError(f"Invalid agent state transition: {previous} -> {new}")
