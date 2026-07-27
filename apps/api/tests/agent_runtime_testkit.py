from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.agent_runtime.repository import InMemoryAgentRuntimeRepository
from app.agent_runtime.service import AgentRuntimeService
from app.models.agent_runtime import (
    AgentRunSpecification,
    BeginAttemptCommand,
    ClaimAgentRunCommand,
    CompleteAttemptCommand,
    ConfirmPauseCommand,
    CreateAgentRunCommand,
    FailAttemptCommand,
    FailureClassification,
    QueueAgentRunCommand,
    RequestPauseCommand,
    ResumeAgentRunCommand,
    RuntimeCommandResult,
    StartAttemptCommand,
)

BASE_TIME = datetime(2026, 1, 1, tzinfo=UTC)


class SequenceFactory:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.index = 0

    def __call__(self) -> str:
        self.index += 1
        return f"{self.prefix}-{self.index}"


def ts(second: int) -> datetime:
    return BASE_TIME + timedelta(seconds=second)


def make_service() -> AgentRuntimeService:
    return AgentRuntimeService(
        InMemoryAgentRuntimeRepository(),
        utc_clock=lambda: ts(10_000),
        run_id_factory=SequenceFactory("run"),
        attempt_id_factory=SequenceFactory("attempt"),
        event_id_factory=SequenceFactory("event"),
        checkpoint_id_factory=SequenceFactory("checkpoint"),
    )


def make_spec(
    *,
    run_id: str = "run-1",
    task_id: str = "task-1",
    agent_id: str = "agent-1",
    created_at: datetime | None = None,
    parent_run_id: str | None = None,
    correlation_id: str | None = "corr-1",
    causation_id: str | None = "cause-1",
    max_attempts: int = 3,
) -> AgentRunSpecification:
    return AgentRunSpecification(
        run_id=run_id,
        task_id=task_id,
        agent_id=agent_id,
        requested_operation="summarize quarterly planning",
        created_at=created_at or ts(0),
        deadline=ts(3_600),
        parent_run_id=parent_run_id,
        correlation_id=correlation_id,
        causation_id=causation_id,
        idempotency_key=f"idem-{run_id}",
        maximum_permitted_attempts=max_attempts,
        metadata={"scope": "test"},
        requested_capabilities=("planning", "reporting"),
    )


def create_run(
    service: AgentRuntimeService,
    *,
    specification: AgentRunSpecification | None = None,
    command_id: str = "cmd-create",
    timestamp: datetime | None = None,
) -> RuntimeCommandResult:
    spec = specification or make_spec()
    return service.create_run(
        CreateAgentRunCommand(
            specification=spec,
            command_id=command_id,
            expected_run_version=0,
            timestamp=timestamp or spec.created_at,
            actor_reference="operator-1",
            source_metadata={"source": "test"},
        )
    )


def queue_run(
    service: AgentRuntimeService,
    run_id: str,
    *,
    expected_run_version: int,
    command_id: str = "cmd-queue",
    second: int = 1,
) -> RuntimeCommandResult:
    return service.queue_run(
        QueueAgentRunCommand(
            run_id=run_id,
            command_id=command_id,
            expected_run_version=expected_run_version,
            timestamp=ts(second),
            actor_reference="scheduler-1",
            source_metadata={"source": "test"},
        )
    )


def claim_run(
    service: AgentRuntimeService,
    run_id: str,
    *,
    expected_run_version: int,
    command_id: str = "cmd-claim",
    second: int = 2,
) -> RuntimeCommandResult:
    return service.claim_run(
        ClaimAgentRunCommand(
            run_id=run_id,
            command_id=command_id,
            expected_run_version=expected_run_version,
            timestamp=ts(second),
            actor_reference="scheduler-1",
            executor_reference="worker-1",
            source_metadata={"source": "test"},
        )
    )


def begin_attempt(
    service: AgentRuntimeService,
    run_id: str,
    *,
    expected_run_version: int,
    command_id: str = "cmd-begin",
    second: int = 3,
    checkpoint_id: str | None = None,
) -> RuntimeCommandResult:
    return service.begin_attempt(
        BeginAttemptCommand(
            run_id=run_id,
            command_id=command_id,
            expected_run_version=expected_run_version,
            timestamp=ts(second),
            actor_reference="worker-1",
            executor_reference="worker-1",
            resume_from_checkpoint_id=checkpoint_id,
            source_metadata={"source": "test"},
        )
    )


def start_attempt(
    service: AgentRuntimeService,
    run_id: str,
    *,
    expected_run_version: int,
    command_id: str = "cmd-start",
    second: int = 4,
    attempt_id: str | None = None,
) -> RuntimeCommandResult:
    return service.start_attempt(
        StartAttemptCommand(
            run_id=run_id,
            command_id=command_id,
            expected_run_version=expected_run_version,
            timestamp=ts(second),
            actor_reference="worker-1",
            attempt_id=attempt_id,
            source_metadata={"source": "test"},
        )
    )


def complete_attempt(
    service: AgentRuntimeService,
    run_id: str,
    *,
    expected_run_version: int,
    command_id: str = "cmd-attempt-success",
    second: int = 5,
    attempt_id: str | None = None,
) -> RuntimeCommandResult:
    return service.complete_attempt(
        CompleteAttemptCommand(
            run_id=run_id,
            command_id=command_id,
            expected_run_version=expected_run_version,
            timestamp=ts(second),
            actor_reference="worker-1",
            attempt_id=attempt_id,
            source_metadata={"source": "test"},
        )
    )


def fail_attempt(
    service: AgentRuntimeService,
    run_id: str,
    *,
    expected_run_version: int,
    command_id: str = "cmd-attempt-fail",
    second: int = 5,
    attempt_id: str | None = None,
    detail: str = "Dependency unavailable",
    category: FailureClassification = FailureClassification.DEPENDENCY,
) -> RuntimeCommandResult:
    return service.fail_attempt(
        FailAttemptCommand(
            run_id=run_id,
            command_id=command_id,
            expected_run_version=expected_run_version,
            timestamp=ts(second),
            actor_reference="worker-1",
            attempt_id=attempt_id,
            failure_category=category,
            failure_detail=detail,
            source_metadata={"source": "test"},
        )
    )


def request_pause(
    service: AgentRuntimeService,
    run_id: str,
    *,
    expected_run_version: int,
    command_id: str = "cmd-pause-request",
    second: int = 5,
    reason_code: str = "operator_pause",
    detail: str = "Pause requested",
) -> RuntimeCommandResult:
    return service.request_pause(
        RequestPauseCommand(
            run_id=run_id,
            command_id=command_id,
            expected_run_version=expected_run_version,
            timestamp=ts(second),
            actor_reference="operator-1",
            reason_code=reason_code,
            detail=detail,
            source_metadata={"source": "test"},
        )
    )


def confirm_pause(
    service: AgentRuntimeService,
    run_id: str,
    *,
    expected_run_version: int,
    command_id: str = "cmd-pause-confirm",
    second: int = 6,
) -> RuntimeCommandResult:
    return service.confirm_pause(
        ConfirmPauseCommand(
            run_id=run_id,
            command_id=command_id,
            expected_run_version=expected_run_version,
            timestamp=ts(second),
            actor_reference="operator-1",
            source_metadata={"source": "test"},
        )
    )


def resume_run(
    service: AgentRuntimeService,
    run_id: str,
    *,
    expected_run_version: int,
    command_id: str = "cmd-resume",
    second: int = 7,
) -> RuntimeCommandResult:
    return service.resume_run(
        ResumeAgentRunCommand(
            run_id=run_id,
            command_id=command_id,
            expected_run_version=expected_run_version,
            timestamp=ts(second),
            actor_reference="operator-1",
            source_metadata={"source": "test"},
        )
    )


def prepare_running_run(
    service: AgentRuntimeService, *, run_id: str = "run-1"
) -> RuntimeCommandResult:
    create_run(service, specification=make_spec(run_id=run_id), command_id=f"cmd-create-{run_id}")
    queue_run(service, run_id, expected_run_version=1, command_id=f"cmd-queue-{run_id}")
    claim_run(service, run_id, expected_run_version=2, command_id=f"cmd-claim-{run_id}")
    begin_attempt(service, run_id, expected_run_version=3, command_id=f"cmd-begin-{run_id}")
    return start_attempt(service, run_id, expected_run_version=5, command_id=f"cmd-start-{run_id}")


def prepare_pause_requested_run(
    service: AgentRuntimeService, *, run_id: str = "run-1"
) -> RuntimeCommandResult:
    prepare_running_run(service, run_id=run_id)
    return request_pause(
        service,
        run_id,
        expected_run_version=6,
        command_id=f"cmd-pause-request-{run_id}",
    )


def prepare_paused_run(
    service: AgentRuntimeService, *, run_id: str = "run-1"
) -> RuntimeCommandResult:
    prepare_pause_requested_run(service, run_id=run_id)
    return confirm_pause(
        service,
        run_id,
        expected_run_version=7,
        command_id=f"cmd-pause-confirm-{run_id}",
    )


def prepare_blocked_run(
    service: AgentRuntimeService, *, run_id: str = "run-1"
) -> RuntimeCommandResult:
    prepare_running_run(service, run_id=run_id)
    return fail_attempt(
        service,
        run_id,
        expected_run_version=6,
        command_id=f"cmd-fail-{run_id}",
    )
