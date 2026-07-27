from __future__ import annotations

from dataclasses import dataclass

from app.models.agent_runtime import AgentRunState, AgentRuntimeEventType, StateCategory

PRE_EXECUTION_STATES = frozenset(
    {AgentRunState.CREATED, AgentRunState.QUEUED, AgentRunState.CLAIMED}
)
ACTIVE_STATES = frozenset({AgentRunState.STARTING, AgentRunState.RUNNING})
INTERRUPTED_STATES = frozenset(
    {AgentRunState.PAUSE_REQUESTED, AgentRunState.PAUSED, AgentRunState.BLOCKED}
)
CANCELLATION_STATES = frozenset({AgentRunState.CANCEL_REQUESTED, AgentRunState.CANCELLING})
TERMINAL_STATES = frozenset(
    {
        AgentRunState.CANCELLED,
        AgentRunState.SUCCEEDED,
        AgentRunState.FAILED,
        AgentRunState.TIMED_OUT,
        AgentRunState.ABANDONED,
    }
)


@dataclass(frozen=True)
class TransitionRule:
    event_type: AgentRuntimeEventType
    allowed_sources: frozenset[AgentRunState] | None
    allowed_targets: frozenset[AgentRunState]
    required_metadata: frozenset[str]
    requires_attempt: bool = False
    requires_active_attempt: bool = False
    checkpoint_allowed: bool = False
    terminal: bool = False
    idempotent_repeat: bool = False
    increments_version: bool = True


TRANSITION_RULES: dict[AgentRuntimeEventType, TransitionRule] = {
    AgentRuntimeEventType.RUN_CREATED: TransitionRule(
        event_type=AgentRuntimeEventType.RUN_CREATED,
        allowed_sources=None,
        allowed_targets=frozenset({AgentRunState.CREATED}),
        required_metadata=frozenset({"specification"}),
    ),
    AgentRuntimeEventType.RUN_QUEUED: TransitionRule(
        event_type=AgentRuntimeEventType.RUN_QUEUED,
        allowed_sources=frozenset({AgentRunState.CREATED}),
        allowed_targets=frozenset({AgentRunState.QUEUED}),
        required_metadata=frozenset({"detail"}),
    ),
    AgentRuntimeEventType.RUN_CLAIMED: TransitionRule(
        event_type=AgentRuntimeEventType.RUN_CLAIMED,
        allowed_sources=frozenset({AgentRunState.QUEUED}),
        allowed_targets=frozenset({AgentRunState.CLAIMED}),
        required_metadata=frozenset({"executor_reference", "detail"}),
    ),
    AgentRuntimeEventType.RUN_START_REQUESTED: TransitionRule(
        event_type=AgentRuntimeEventType.RUN_START_REQUESTED,
        allowed_sources=frozenset({AgentRunState.CLAIMED}),
        allowed_targets=frozenset({AgentRunState.STARTING}),
        required_metadata=frozenset({"executor_reference", "detail"}),
    ),
    AgentRuntimeEventType.ATTEMPT_CREATED: TransitionRule(
        event_type=AgentRuntimeEventType.ATTEMPT_CREATED,
        allowed_sources=frozenset({AgentRunState.STARTING}),
        allowed_targets=frozenset({AgentRunState.STARTING}),
        required_metadata=frozenset({"attempt"}),
        requires_attempt=False,
        requires_active_attempt=False,
    ),
    AgentRuntimeEventType.ATTEMPT_STARTED: TransitionRule(
        event_type=AgentRuntimeEventType.ATTEMPT_STARTED,
        allowed_sources=frozenset({AgentRunState.STARTING}),
        allowed_targets=frozenset({AgentRunState.RUNNING}),
        required_metadata=frozenset({"detail"}),
        requires_attempt=True,
        requires_active_attempt=True,
    ),
    AgentRuntimeEventType.HEARTBEAT_RECORDED: TransitionRule(
        event_type=AgentRuntimeEventType.HEARTBEAT_RECORDED,
        allowed_sources=frozenset({AgentRunState.RUNNING, AgentRunState.CANCELLING}),
        allowed_targets=frozenset({AgentRunState.RUNNING, AgentRunState.CANCELLING}),
        required_metadata=frozenset({"detail"}),
        requires_attempt=True,
        requires_active_attempt=True,
    ),
    AgentRuntimeEventType.PAUSE_REQUESTED: TransitionRule(
        event_type=AgentRuntimeEventType.PAUSE_REQUESTED,
        allowed_sources=frozenset(
            {AgentRunState.QUEUED, AgentRunState.CLAIMED, AgentRunState.RUNNING}
        ),
        allowed_targets=frozenset({AgentRunState.PAUSE_REQUESTED}),
        required_metadata=frozenset({"pause"}),
    ),
    AgentRuntimeEventType.RUN_PAUSED: TransitionRule(
        event_type=AgentRuntimeEventType.RUN_PAUSED,
        allowed_sources=frozenset({AgentRunState.PAUSE_REQUESTED}),
        allowed_targets=frozenset({AgentRunState.PAUSED}),
        required_metadata=frozenset({"detail"}),
    ),
    AgentRuntimeEventType.RUN_RESUMED: TransitionRule(
        event_type=AgentRuntimeEventType.RUN_RESUMED,
        allowed_sources=frozenset({AgentRunState.PAUSED}),
        allowed_targets=frozenset(
            {AgentRunState.QUEUED, AgentRunState.CLAIMED, AgentRunState.RUNNING}
        ),
        required_metadata=frozenset({"target_state", "detail"}),
    ),
    AgentRuntimeEventType.RUN_BLOCKED: TransitionRule(
        event_type=AgentRuntimeEventType.RUN_BLOCKED,
        allowed_sources=frozenset({AgentRunState.CLAIMED, AgentRunState.RUNNING}),
        allowed_targets=frozenset({AgentRunState.BLOCKED}),
        required_metadata=frozenset({"block"}),
    ),
    AgentRuntimeEventType.RUN_UNBLOCKED: TransitionRule(
        event_type=AgentRuntimeEventType.RUN_UNBLOCKED,
        allowed_sources=frozenset({AgentRunState.BLOCKED}),
        allowed_targets=frozenset({AgentRunState.CLAIMED, AgentRunState.RUNNING}),
        required_metadata=frozenset({"target_state", "detail"}),
    ),
    AgentRuntimeEventType.CANCELLATION_REQUESTED: TransitionRule(
        event_type=AgentRuntimeEventType.CANCELLATION_REQUESTED,
        allowed_sources=frozenset(
            {
                AgentRunState.CREATED,
                AgentRunState.QUEUED,
                AgentRunState.CLAIMED,
                AgentRunState.STARTING,
                AgentRunState.RUNNING,
                AgentRunState.PAUSE_REQUESTED,
                AgentRunState.PAUSED,
                AgentRunState.BLOCKED,
            }
        ),
        allowed_targets=frozenset({AgentRunState.CANCEL_REQUESTED}),
        required_metadata=frozenset({"cancellation"}),
    ),
    AgentRuntimeEventType.CANCELLATION_STARTED: TransitionRule(
        event_type=AgentRuntimeEventType.CANCELLATION_STARTED,
        allowed_sources=frozenset({AgentRunState.CANCEL_REQUESTED}),
        allowed_targets=frozenset({AgentRunState.CANCELLING}),
        required_metadata=frozenset({"detail"}),
        requires_attempt=True,
        requires_active_attempt=True,
    ),
    AgentRuntimeEventType.RUN_CANCELLED: TransitionRule(
        event_type=AgentRuntimeEventType.RUN_CANCELLED,
        allowed_sources=frozenset({AgentRunState.CANCEL_REQUESTED, AgentRunState.CANCELLING}),
        allowed_targets=frozenset({AgentRunState.CANCELLED}),
        required_metadata=frozenset({"detail"}),
        terminal=True,
    ),
    AgentRuntimeEventType.CHECKPOINT_RECORDED: TransitionRule(
        event_type=AgentRuntimeEventType.CHECKPOINT_RECORDED,
        allowed_sources=frozenset(
            {
                AgentRunState.RUNNING,
                AgentRunState.PAUSED,
                AgentRunState.BLOCKED,
                AgentRunState.CANCELLING,
            }
        ),
        allowed_targets=frozenset(
            {
                AgentRunState.RUNNING,
                AgentRunState.PAUSED,
                AgentRunState.BLOCKED,
                AgentRunState.CANCELLING,
            }
        ),
        required_metadata=frozenset({"checkpoint"}),
        requires_attempt=True,
        requires_active_attempt=True,
        checkpoint_allowed=True,
    ),
    AgentRuntimeEventType.ATTEMPT_SUCCEEDED: TransitionRule(
        event_type=AgentRuntimeEventType.ATTEMPT_SUCCEEDED,
        allowed_sources=frozenset({AgentRunState.RUNNING}),
        allowed_targets=frozenset({AgentRunState.CLAIMED}),
        required_metadata=frozenset({"detail"}),
        requires_attempt=True,
        requires_active_attempt=True,
    ),
    AgentRuntimeEventType.ATTEMPT_FAILED: TransitionRule(
        event_type=AgentRuntimeEventType.ATTEMPT_FAILED,
        allowed_sources=frozenset(
            {
                AgentRunState.STARTING,
                AgentRunState.RUNNING,
                AgentRunState.PAUSE_REQUESTED,
                AgentRunState.PAUSED,
            }
        ),
        allowed_targets=frozenset({AgentRunState.BLOCKED}),
        required_metadata=frozenset({"failure", "blocking_reason"}),
        requires_attempt=True,
        requires_active_attempt=True,
    ),
    AgentRuntimeEventType.ATTEMPT_TIMED_OUT: TransitionRule(
        event_type=AgentRuntimeEventType.ATTEMPT_TIMED_OUT,
        allowed_sources=frozenset(
            {
                AgentRunState.STARTING,
                AgentRunState.RUNNING,
                AgentRunState.PAUSE_REQUESTED,
                AgentRunState.PAUSED,
            }
        ),
        allowed_targets=frozenset({AgentRunState.BLOCKED}),
        required_metadata=frozenset({"failure", "blocking_reason"}),
        requires_attempt=True,
        requires_active_attempt=True,
    ),
    AgentRuntimeEventType.ATTEMPT_ABANDONED: TransitionRule(
        event_type=AgentRuntimeEventType.ATTEMPT_ABANDONED,
        allowed_sources=frozenset(
            {
                AgentRunState.STARTING,
                AgentRunState.RUNNING,
                AgentRunState.PAUSE_REQUESTED,
                AgentRunState.PAUSED,
            }
        ),
        allowed_targets=frozenset({AgentRunState.BLOCKED}),
        required_metadata=frozenset({"failure", "blocking_reason"}),
        requires_attempt=True,
        requires_active_attempt=True,
    ),
    AgentRuntimeEventType.RUN_SUCCEEDED: TransitionRule(
        event_type=AgentRuntimeEventType.RUN_SUCCEEDED,
        allowed_sources=frozenset({AgentRunState.CLAIMED}),
        allowed_targets=frozenset({AgentRunState.SUCCEEDED}),
        required_metadata=frozenset({"detail"}),
        terminal=True,
    ),
    AgentRuntimeEventType.RUN_FAILED: TransitionRule(
        event_type=AgentRuntimeEventType.RUN_FAILED,
        allowed_sources=frozenset(
            {
                AgentRunState.CREATED,
                AgentRunState.QUEUED,
                AgentRunState.CLAIMED,
                AgentRunState.BLOCKED,
            }
        ),
        allowed_targets=frozenset({AgentRunState.FAILED}),
        required_metadata=frozenset({"failure"}),
        terminal=True,
    ),
    AgentRuntimeEventType.RUN_TIMED_OUT: TransitionRule(
        event_type=AgentRuntimeEventType.RUN_TIMED_OUT,
        allowed_sources=frozenset(
            {
                AgentRunState.CREATED,
                AgentRunState.QUEUED,
                AgentRunState.CLAIMED,
                AgentRunState.BLOCKED,
            }
        ),
        allowed_targets=frozenset({AgentRunState.TIMED_OUT}),
        required_metadata=frozenset({"failure"}),
        terminal=True,
    ),
    AgentRuntimeEventType.RUN_ABANDONED: TransitionRule(
        event_type=AgentRuntimeEventType.RUN_ABANDONED,
        allowed_sources=frozenset(
            {
                AgentRunState.CREATED,
                AgentRunState.QUEUED,
                AgentRunState.CLAIMED,
                AgentRunState.BLOCKED,
            }
        ),
        allowed_targets=frozenset({AgentRunState.ABANDONED}),
        required_metadata=frozenset({"failure"}),
        terminal=True,
    ),
    AgentRuntimeEventType.RECOVERY_PLANNED: TransitionRule(
        event_type=AgentRuntimeEventType.RECOVERY_PLANNED,
        allowed_sources=frozenset({AgentRunState.BLOCKED}),
        allowed_targets=frozenset({AgentRunState.BLOCKED}),
        required_metadata=frozenset({"plan"}),
        requires_attempt=False,
        requires_active_attempt=False,
    ),
}


def classify_state(state: AgentRunState) -> StateCategory:
    if state in PRE_EXECUTION_STATES:
        return StateCategory.PRE_EXECUTION
    if state in ACTIVE_STATES:
        return StateCategory.ACTIVE
    if state in INTERRUPTED_STATES:
        return StateCategory.INTERRUPTED
    if state in CANCELLATION_STATES:
        return StateCategory.CANCELLATION
    return StateCategory.TERMINAL
