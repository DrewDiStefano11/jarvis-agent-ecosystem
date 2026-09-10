"""Durable recovery scenarios: revocation, restart, resume integrity."""

from __future__ import annotations

from pathlib import Path

from app.autonomy.gates import ExecutionGate
from app.autonomy.harness import ResumeState
from app.autonomy.production import RuntimeExecutionAdapter, digest_output
from app.autonomy.scenarios import (
    build_harness,
    golden_path_spec,
    in_memory_runtime,
    restart_recovery_spec,
    run_scenario,
)
from app.models.agent_runtime import AgentRunState


def test_authorization_revoked_scenario_passes(tmp_path: Path) -> None:
    outcome = run_scenario("authorization_revoked", "test-sha", tmp_path=tmp_path)
    failures = [check for check in outcome.evidence.checks if not check.passed]
    assert outcome.evidence.verdict == "pass", [
        (check.name, check.expected, check.actual) for check in failures
    ]


def test_restart_recovery_scenario_passes(tmp_path: Path) -> None:
    outcome = run_scenario("restart_recovery", "test-sha", tmp_path=tmp_path)
    failures = [check for check in outcome.evidence.checks if not check.passed]
    assert outcome.evidence.verdict == "pass", [
        (check.name, check.expected, check.actual) for check in failures
    ]
    phases = {entry.detail.get("resume_phase") for entry in outcome.evidence.timeline}
    assert phases == {"pre_restart", "post_restart"}


def test_resume_rejects_bogus_completed_result() -> None:
    from app.autonomy.ports import NodeResult

    spec = golden_path_spec()
    harness = build_harness(spec, in_memory_runtime())
    bogus = NodeResult(
        node_id="A",
        agent_id="agent-backend-1",
        attempt_count=1,
        output_digest=digest_output("bogus"),
        summary="bogus",
        run_id="auto-golden-path-A",
        checkpoint_id="auto-golden-path-A:ckpt-a1",
    )
    result = harness.run(ResumeState(completed=(bogus,), clock_second=0))
    assert result.terminal_state == "failed"
    assert result.reason_code == "resume_integrity_violation"


def test_resume_rejects_digest_mismatch_after_success() -> None:
    from dataclasses import replace

    from app.autonomy.ports import NodeResult

    spec = golden_path_spec()
    adapter = in_memory_runtime()
    harness = build_harness(spec, adapter)
    result = harness.run()
    assert result.terminal_state == "succeeded"
    genuine: NodeResult = result.node_results["A"]
    tampered = replace(genuine, output_digest=digest_output("tampered"))
    resumed_harness = build_harness(spec, adapter, clock_second=result.clock_second)
    resumed = resumed_harness.run(
        ResumeState(completed=(tampered,), clock_second=result.clock_second)
    )
    assert resumed.terminal_state == "failed"
    assert resumed.reason_code == "resume_integrity_violation"


def test_resume_of_success_is_idempotent_without_new_commands() -> None:
    spec = golden_path_spec()
    adapter = in_memory_runtime()
    harness = build_harness(spec, adapter)
    result = harness.run()
    assert result.terminal_state == "succeeded"
    commands_before = list(adapter.issued_command_ids)
    events_before = {run_id: adapter.event_count(run_id) for run_id in result.run_ids}
    resumed_harness = build_harness(spec, adapter, clock_second=result.clock_second)
    completed = tuple(result.node_results[node_id] for node_id in ("A", "B"))
    resumed = resumed_harness.run(
        ResumeState(completed=completed, clock_second=result.clock_second)
    )
    assert resumed.terminal_state == "succeeded"
    assert resumed.reason_code == "ok"
    # Resume re-derives the same deterministic command ids; nothing new commits.
    assert adapter.issued_command_ids == commands_before
    for run_id, count in events_before.items():
        assert adapter.event_count(run_id) == count


def test_pause_then_resume_continues_without_repeating_work() -> None:
    from dataclasses import replace

    spec = restart_recovery_spec()
    paused_spec = replace(spec, pause_points=frozenset({"after_node:A"}))
    adapter = in_memory_runtime()
    harness = build_harness(paused_spec, adapter)
    partial = harness.run()
    assert partial.terminal_state == "paused"
    assert set(partial.node_results) == {"A"}
    events_before = adapter.event_count(partial.node_results["A"].run_id)
    resumed_harness = build_harness(spec, adapter, clock_second=partial.clock_second)
    completed = (partial.node_results["A"],)
    result = resumed_harness.run(
        ResumeState(completed=completed, clock_second=partial.clock_second)
    )
    assert result.terminal_state == "succeeded"
    assert adapter.event_count(partial.node_results["A"].run_id) == events_before
    assert set(result.node_results) == {"A", "B", "C"}


def test_gate_state_is_inspectable_after_stop() -> None:
    gate = ExecutionGate()
    gate.activate_emergency_stop("estop_test", "inspectable")
    snapshot = gate.snapshot()
    assert snapshot.emergency_stopped is True
    assert snapshot.reason_code == "estop_test"
    assert snapshot.changed_at is not None


def test_cancelled_objective_runs_are_terminal_and_stable() -> None:
    outcome = run_scenario("cancellation", "test-sha")
    assert outcome.evidence.verdict == "pass"
    adapter: RuntimeExecutionAdapter = outcome.harness.runtime
    for run_id in outcome.result.run_ids:
        snapshot = adapter.load_run(run_id)
        assert snapshot is not None
        assert snapshot.state in {AgentRunState.SUCCEEDED, AgentRunState.CANCELLED}
        reloaded = adapter.load_run(run_id)
        assert reloaded == snapshot
