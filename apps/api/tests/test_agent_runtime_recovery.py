from __future__ import annotations

import pytest

from app.agent_runtime.errors import (
    CheckpointLineageError,
    CommandConflictError,
    RecoveryNotAllowedError,
    VersionConflictError,
)
from app.agent_runtime.recovery import plan_recovery
from app.models.agent_runtime import (
    CompleteAgentRunCommand,
    RecordCheckpointCommand,
    RequestCancellationCommand,
    RequestRecoveryPlanCommand,
    UnblockAgentRunCommand,
)
from tests.agent_runtime_testkit import (
    begin_attempt,
    claim_run,
    complete_attempt,
    create_run,
    fail_attempt,
    make_service,
    make_spec,
    prepare_blocked_run,
    prepare_running_run,
    queue_run,
    ts,
)


def _prepare_planned_recovery_run(
    service,
    *,
    checkpoint_ids: tuple[str, ...] = ("checkpoint-1",),
    unblock: bool = True,
):
    prepare_running_run(service)
    version = 6
    for offset, checkpoint_id in enumerate(checkpoint_ids, start=0):
        service.record_checkpoint(
            RecordCheckpointCommand(
                run_id="run-1",
                command_id=f"cmd-{checkpoint_id}",
                expected_run_version=version + offset,
                timestamp=ts(5 + offset),
                actor_reference="worker-1",
                checkpoint_id=checkpoint_id,
                state_reference=f"checkpoint://{checkpoint_id}",
                integrity_digest=f"sha256:{'a' * 16}{offset}",
                source_metadata={"source": "test"},
            )
        )
    fail_version = 6 + len(checkpoint_ids)
    fail_attempt(
        service,
        "run-1",
        expected_run_version=fail_version,
        command_id="cmd-fail-attempt",
        second=5 + len(checkpoint_ids),
    )
    plan = service.request_recovery_plan(
        RequestRecoveryPlanCommand(
            run_id="run-1",
            command_id="cmd-recovery-plan",
            expected_run_version=fail_version + 1,
            timestamp=ts(6 + len(checkpoint_ids)),
            actor_reference="operator-1",
            source_metadata={"source": "test"},
        )
    )
    if unblock:
        service.unblock_run(
            UnblockAgentRunCommand(
                run_id="run-1",
                command_id="cmd-unblock",
                expected_run_version=fail_version + 2,
                timestamp=ts(7 + len(checkpoint_ids)),
                actor_reference="operator-1",
                source_metadata={"source": "test"},
            )
        )
    return plan


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


def test_unblock_recovery_required_run_is_rejected_without_mutation() -> None:
    service = make_service()
    blocked = prepare_blocked_run(service)
    assert blocked.snapshot is not None
    assert blocked.snapshot.state.value == "blocked"
    assert blocked.snapshot.recovery_status.value == "required"
    before_snapshot = service.repository.load_run("run-1")
    before_events = service.repository.list_events("run-1")
    before_attempts = service.repository.load_attempt_history("run-1")
    before_checkpoints = service.repository.list_checkpoints("run-1")
    before_processed = service.repository.get_processed_command("run-1", "cmd-unblock-too-early")
    with pytest.raises(RecoveryNotAllowedError):
        service.unblock_run(
            UnblockAgentRunCommand(
                run_id="run-1",
                command_id="cmd-unblock-too-early",
                expected_run_version=7,
                timestamp=ts(6),
                actor_reference="operator-1",
                source_metadata={"source": "test"},
            )
        )
    assert service.repository.load_run("run-1") == before_snapshot
    assert service.repository.list_events("run-1") == before_events
    assert service.repository.load_attempt_history("run-1") == before_attempts
    assert service.repository.list_checkpoints("run-1") == before_checkpoints
    assert (
        service.repository.get_processed_command("run-1", "cmd-unblock-too-early")
        == before_processed
    )


def test_recovery_planned_run_can_be_unblocked_and_replayed_idempotently() -> None:
    service = make_service()
    _prepare_planned_recovery_run(service, checkpoint_ids=("checkpoint-1",), unblock=False)
    command = UnblockAgentRunCommand(
        run_id="run-1",
        command_id="cmd-unblock-after-plan",
        expected_run_version=9,
        timestamp=ts(8),
        actor_reference="operator-1",
        source_metadata={"source": "test"},
    )
    first = service.unblock_run(command)
    assert first.snapshot is not None
    assert first.snapshot.state.value == "claimed"
    assert first.snapshot.recovery_status.value == "planned"
    replay = service.unblock_run(command)
    assert replay.idempotent_replay is True
    assert replay.snapshot == first.snapshot
    with pytest.raises(CommandConflictError):
        service.unblock_run(command.model_copy(update={"timestamp": ts(9)}))


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


@pytest.mark.parametrize(
    "resume_from_checkpoint_id",
    [None, "checkpoint-1"],
)
def test_begin_attempt_requires_the_selected_recovery_checkpoint(
    resume_from_checkpoint_id: str | None,
) -> None:
    service = make_service()
    plan = _prepare_planned_recovery_run(service, checkpoint_ids=("checkpoint-1", "checkpoint-2"))
    before_snapshot = service.repository.load_run("run-1")
    before_events = service.repository.list_events("run-1")
    before_attempts = service.repository.load_attempt_history("run-1")
    before_checkpoints = service.repository.list_checkpoints("run-1")
    before_processed = service.repository.get_processed_command("run-1", "cmd-begin-recovery")
    assert plan.recovery_plan is not None
    assert plan.recovery_plan.selected_checkpoint is not None
    with pytest.raises(CheckpointLineageError):
        begin_attempt(
            service,
            "run-1",
            expected_run_version=11,
            command_id="cmd-begin-recovery",
            second=10,
            checkpoint_id=resume_from_checkpoint_id,
        )
    assert service.repository.load_run("run-1") == before_snapshot
    assert service.repository.list_events("run-1") == before_events
    assert service.repository.load_attempt_history("run-1") == before_attempts
    assert service.repository.list_checkpoints("run-1") == before_checkpoints
    assert (
        service.repository.get_processed_command("run-1", "cmd-begin-recovery") == before_processed
    )


def test_begin_attempt_accepts_exact_selected_recovery_checkpoint_and_clears_recovery_after_commit() -> (
    None
):
    service = make_service()
    plan = _prepare_planned_recovery_run(service, checkpoint_ids=("checkpoint-1", "checkpoint-2"))
    assert plan.recovery_plan is not None
    assert plan.recovery_plan.selected_checkpoint is not None
    before = service.repository.load_run("run-1")
    assert before is not None and before.recovery_status.value == "planned"
    result = begin_attempt(
        service,
        "run-1",
        expected_run_version=11,
        command_id="cmd-begin-recovery",
        second=10,
        checkpoint_id=plan.recovery_plan.selected_checkpoint.checkpoint_id,
    )
    assert result.snapshot is not None
    assert result.snapshot.recovery_status.value == "none"
    assert result.snapshot.active_attempt_id is not None
    attempt = service.repository.load_attempt_history("run-1")[-1]
    assert (
        attempt.resumed_from_checkpoint_id == plan.recovery_plan.selected_checkpoint.checkpoint_id
    )
    assert result.snapshot.state.value == "starting"
    replay_plan = begin_attempt(
        service,
        "run-1",
        expected_run_version=11,
        command_id="cmd-begin-recovery",
        second=10,
        checkpoint_id=plan.recovery_plan.selected_checkpoint.checkpoint_id,
    )
    assert replay_plan.idempotent_replay is True
    assert replay_plan.snapshot == result.snapshot
    with pytest.raises(CommandConflictError):
        begin_attempt(
            service,
            "run-1",
            expected_run_version=11,
            command_id="cmd-begin-recovery",
            second=10,
            checkpoint_id="checkpoint-1",
        )


def test_begin_attempt_rejects_arbitrary_checkpoint_when_plan_selects_none() -> None:
    service = make_service()
    _prepare_planned_recovery_run(service, checkpoint_ids=())
    before_snapshot = service.repository.load_run("run-1")
    before_events = service.repository.list_events("run-1")
    with pytest.raises(CheckpointLineageError):
        begin_attempt(
            service,
            "run-1",
            expected_run_version=9,
            command_id="cmd-begin-recovery-no-checkpoint",
            second=8,
            checkpoint_id="checkpoint-arbitrary",
        )
    assert service.repository.load_run("run-1") == before_snapshot
    assert service.repository.list_events("run-1") == before_events


def test_begin_attempt_rejects_stale_expected_version_for_recovery() -> None:
    service = make_service()
    plan = _prepare_planned_recovery_run(service, checkpoint_ids=("checkpoint-1",))
    assert plan.recovery_plan is not None and plan.recovery_plan.selected_checkpoint is not None
    with pytest.raises(VersionConflictError):
        begin_attempt(
            service,
            "run-1",
            expected_run_version=7,
            command_id="cmd-begin-recovery-stale",
            second=9,
            checkpoint_id=plan.recovery_plan.selected_checkpoint.checkpoint_id,
        )
