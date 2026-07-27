from __future__ import annotations

import pytest

from app.agent_runtime.errors import CheckpointLineageError, RecoveryNotAllowedError
from app.agent_runtime.recovery import plan_recovery
from app.models.agent_runtime import (
    CompleteAgentRunCommand,
    RecordCheckpointCommand,
    RequestCancellationCommand,
    RequestRecoveryPlanCommand,
)
from tests.agent_runtime_testkit import (
    claim_run,
    complete_attempt,
    create_run,
    make_service,
    make_spec,
    prepare_blocked_run,
    prepare_running_run,
    queue_run,
    ts,
)


def test_valid_failed_attempt_recovery_returns_plan() -> None:
    service = make_service()
    prepare_running_run(service)
    service.record_checkpoint(
        RecordCheckpointCommand(
            run_id="run-1",
            command_id="cmd-checkpoint-1",
            expected_run_version=6,
            timestamp=ts(5),
            actor_reference="worker-1",
            checkpoint_id="checkpoint-1",
            state_reference="checkpoint://state/1",
            integrity_digest="sha256:aaaaaaaaaaaaaaaa",
            source_metadata={"source": "test"},
        )
    )
    service.fail_attempt(
        __import__("app.models.agent_runtime", fromlist=["FailAttemptCommand"]).FailAttemptCommand(
            run_id="run-1",
            command_id="cmd-fail-attempt",
            expected_run_version=7,
            timestamp=ts(6),
            actor_reference="worker-1",
            failure_category="dependency",
            failure_detail="Dependency unavailable",
            source_metadata={"source": "test"},
        )
    )
    result = service.request_recovery_plan(
        RequestRecoveryPlanCommand(
            run_id="run-1",
            command_id="cmd-recovery-plan",
            expected_run_version=8,
            timestamp=ts(7),
            actor_reference="operator-1",
            source_metadata={"source": "test"},
        )
    )
    assert result.recovery_plan is not None
    assert result.recovery_plan.recovery_allowed is True
    assert result.recovery_plan.selected_checkpoint is not None
    assert result.recovery_plan.selected_checkpoint.checkpoint_id == "checkpoint-1"


def test_valid_timed_out_attempt_recovery_is_allowed() -> None:
    service = make_service()
    prepare_running_run(service)
    service.timeout_attempt(
        __import__(
            "app.models.agent_runtime", fromlist=["TimeoutAttemptCommand"]
        ).TimeoutAttemptCommand(
            run_id="run-1",
            command_id="cmd-timeout-attempt",
            expected_run_version=6,
            timestamp=ts(5),
            actor_reference="worker-1",
            source_metadata={"source": "test"},
        )
    )
    result = service.request_recovery_plan(
        RequestRecoveryPlanCommand(
            run_id="run-1",
            command_id="cmd-recovery-plan",
            expected_run_version=7,
            timestamp=ts(6),
            actor_reference="operator-1",
            source_metadata={"source": "test"},
        )
    )
    assert result.recovery_plan is not None
    assert result.recovery_plan.next_attempt_number == 2


def test_recovery_is_denied_for_active_attempts() -> None:
    service = make_service()
    prepare_running_run(service)
    with pytest.raises(RecoveryNotAllowedError):
        plan_recovery(
            service.repository.load_run("run-1"),
            service.repository.load_attempt_history("run-1"),
            service.repository.list_checkpoints("run-1"),
            service.repository.list_events("run-1"),
        )


def test_recovery_is_denied_for_succeeded_runs() -> None:
    service = make_service()
    prepare_running_run(service)
    complete_attempt(service, "run-1", expected_run_version=6)
    service.complete_run(
        CompleteAgentRunCommand(
            run_id="run-1",
            command_id="cmd-complete-run",
            expected_run_version=7,
            timestamp=ts(6),
            actor_reference="worker-1",
            source_metadata={"source": "test"},
        )
    )
    with pytest.raises(RecoveryNotAllowedError):
        service.request_recovery_plan(
            RequestRecoveryPlanCommand(
                run_id="run-1",
                command_id="cmd-plan",
                expected_run_version=8,
                timestamp=ts(7),
                actor_reference="operator-1",
                source_metadata={"source": "test"},
            )
        )


def test_recovery_is_denied_for_cancelled_runs() -> None:
    service = make_service()
    create_run(service)
    queue_run(service, "run-1", expected_run_version=1)
    claim_run(service, "run-1", expected_run_version=2)
    service.request_cancellation(
        RequestCancellationCommand(
            run_id="run-1",
            command_id="cmd-cancel",
            expected_run_version=3,
            timestamp=ts(3),
            actor_reference="operator-1",
            requester_reference="operator-1",
            reason_code="operator_cancel",
            detail="Stop it",
            source_metadata={"source": "test"},
        )
    )
    with pytest.raises(RecoveryNotAllowedError):
        service.request_recovery_plan(
            RequestRecoveryPlanCommand(
                run_id="run-1",
                command_id="cmd-plan",
                expected_run_version=5,
                timestamp=ts(4),
                actor_reference="operator-1",
                source_metadata={"source": "test"},
            )
        )


def test_recovery_is_denied_when_attempt_limit_is_exhausted() -> None:
    service = make_service()
    create_run(service, specification=make_spec(max_attempts=1))
    queue_run(service, "run-1", expected_run_version=1)
    claim_run(service, "run-1", expected_run_version=2)
    service.begin_attempt(
        __import__(
            "app.models.agent_runtime", fromlist=["BeginAttemptCommand"]
        ).BeginAttemptCommand(
            run_id="run-1",
            command_id="cmd-begin",
            expected_run_version=3,
            timestamp=ts(3),
            actor_reference="worker-1",
            executor_reference="worker-1",
            source_metadata={"source": "test"},
        )
    )
    service.start_attempt(
        __import__(
            "app.models.agent_runtime", fromlist=["StartAttemptCommand"]
        ).StartAttemptCommand(
            run_id="run-1",
            command_id="cmd-start",
            expected_run_version=5,
            timestamp=ts(4),
            actor_reference="worker-1",
            source_metadata={"source": "test"},
        )
    )
    service.fail_attempt(
        __import__("app.models.agent_runtime", fromlist=["FailAttemptCommand"]).FailAttemptCommand(
            run_id="run-1",
            command_id="cmd-fail",
            expected_run_version=6,
            timestamp=ts(5),
            actor_reference="worker-1",
            failure_category="dependency",
            failure_detail="Dependency unavailable",
            source_metadata={"source": "test"},
        )
    )
    with pytest.raises(RecoveryNotAllowedError):
        service.request_recovery_plan(
            RequestRecoveryPlanCommand(
                run_id="run-1",
                command_id="cmd-plan",
                expected_run_version=7,
                timestamp=ts(6),
                actor_reference="operator-1",
                source_metadata={"source": "test"},
            )
        )


def test_recovery_is_denied_for_invalid_checkpoint_lineage() -> None:
    service = make_service()
    blocked = prepare_blocked_run(service)
    assert blocked.snapshot is not None
    events = service.repository.list_events("run-1")
    checkpoints = [
        __import__("app.models.agent_runtime", fromlist=["AgentRunCheckpoint"]).AgentRunCheckpoint(
            checkpoint_id="checkpoint-bad",
            run_id="other-run",
            attempt_id="attempt-1",
            checkpoint_sequence=1,
            run_version=1,
            event_sequence=1,
            timestamp=ts(1),
            state_reference="checkpoint://bad",
            integrity_digest="sha256:aaaaaaaaaaaaaaaa",
        )
    ]
    with pytest.raises(CheckpointLineageError):
        plan_recovery(
            blocked.snapshot, service.repository.load_attempt_history("run-1"), checkpoints, events
        )


def test_recovery_is_denied_when_snapshot_and_ledger_disagree() -> None:
    service = make_service()
    blocked = prepare_blocked_run(service)
    assert blocked.snapshot is not None
    snapshot = blocked.snapshot.model_copy(update={"version": blocked.snapshot.version + 1})
    with pytest.raises(RecoveryNotAllowedError):
        plan_recovery(
            snapshot,
            service.repository.load_attempt_history("run-1"),
            service.repository.list_checkpoints("run-1"),
            service.repository.list_events("run-1"),
        )


def test_recovery_checkpoint_selection_is_deterministic() -> None:
    service = make_service()
    prepare_running_run(service)
    service.record_checkpoint(
        RecordCheckpointCommand(
            run_id="run-1",
            command_id="cmd-checkpoint-1",
            expected_run_version=6,
            timestamp=ts(5),
            actor_reference="worker-1",
            checkpoint_id="checkpoint-1",
            state_reference="checkpoint://state/1",
            integrity_digest="sha256:aaaaaaaaaaaaaaaa",
            source_metadata={"source": "test"},
        )
    )
    service.record_checkpoint(
        RecordCheckpointCommand(
            run_id="run-1",
            command_id="cmd-checkpoint-2",
            expected_run_version=7,
            timestamp=ts(6),
            actor_reference="worker-1",
            checkpoint_id="checkpoint-2",
            state_reference="checkpoint://state/2",
            integrity_digest="sha256:bbbbbbbbbbbbbbbb",
            source_metadata={"source": "test"},
        )
    )
    service.fail_attempt(
        __import__("app.models.agent_runtime", fromlist=["FailAttemptCommand"]).FailAttemptCommand(
            run_id="run-1",
            command_id="cmd-fail-attempt",
            expected_run_version=8,
            timestamp=ts(7),
            actor_reference="worker-1",
            failure_category="dependency",
            failure_detail="Dependency unavailable",
            source_metadata={"source": "test"},
        )
    )
    plan = plan_recovery(
        service.repository.load_run("run-1"),
        service.repository.load_attempt_history("run-1"),
        service.repository.list_checkpoints("run-1"),
        service.repository.list_events("run-1"),
    )
    assert plan.selected_checkpoint is not None
    assert plan.selected_checkpoint.checkpoint_id == "checkpoint-2"
