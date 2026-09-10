"""Acceptance harness tests: in-memory scenarios, units, and evidence."""

from __future__ import annotations

import json

import pytest

from app.autonomy.bounds import HarnessBounds
from app.autonomy.events import EventRecorder, scrub_detail
from app.autonomy.evidence import read_evidence_json, render_markdown_summary, write_evidence_json
from app.autonomy.fixtures import (
    DeterministicClock,
    FixtureDecomposer,
    FixtureSpecialistExecutor,
    FixtureSynthesizer,
    FixtureTeamSelector,
    FixtureUndefinedError,
    static_workforce,
)
from app.autonomy.gates import ExecutionGate, GateClosedError
from app.autonomy.graph import GraphValidationError, WorkGraph, WorkNode
from app.autonomy.harness import (
    AutonomyHarness,
    HarnessConfig,
    HarnessHooks,
    StrictJsonOutputEvaluation,
)
from app.autonomy.ports import EXPECTED_FIXTURE_STAGES, STAGE_PROVENANCE, Assignment, StageKind
from app.autonomy.production import ContextGroundingAdapter, RuntimeExecutionAdapter
from app.autonomy.scenarios import (
    SCENARIO_NAMES,
    build_harness,
    cancellation_spec,
    golden_path_spec,
    in_memory_runtime,
    production_assembler,
    run_scenario,
)

MEMORY_SCENARIOS = tuple(
    name for name in SCENARIO_NAMES if name not in {"authorization_revoked", "restart_recovery"}
)


@pytest.mark.parametrize("name", MEMORY_SCENARIOS)
def test_memory_scenarios_pass(name: str) -> None:
    outcome = run_scenario(name, "test-sha")
    failures = [check for check in outcome.evidence.checks if not check.passed]
    assert outcome.evidence.verdict == "pass", [
        (check.name, check.expected, check.actual) for check in failures
    ]
    assert outcome.result.terminal_state == outcome.evidence.terminal_state


def test_unknown_scenario_rejected() -> None:
    with pytest.raises(ValueError, match="unknown scenario"):
        run_scenario("nope", "test-sha")


def test_db_scenarios_require_tmp_path() -> None:
    with pytest.raises(ValueError, match="tmp_path"):
        run_scenario("restart_recovery", "test-sha")


def test_fixture_stages_are_explicitly_labeled() -> None:
    outcome = run_scenario("golden_path", "test-sha")
    provenance = {entry["stage"]: entry["implementation"] for entry in outcome.evidence.provenance}
    assert set(provenance) == {stage.value for stage in StageKind}
    for stage in EXPECTED_FIXTURE_STAGES:
        assert provenance[stage.value] == "fixture"
    for stage in StageKind:
        if stage not in EXPECTED_FIXTURE_STAGES:
            assert provenance[stage.value] == "production"
    assert STAGE_PROVENANCE[StageKind.DECOMPOSE].implementation == "fixture"
    assert STAGE_PROVENANCE[StageKind.SYNTHESIZE].implementation == "fixture"


def test_evidence_json_round_trip_and_summary(tmp_path) -> None:
    outcome = run_scenario("golden_path", "test-sha")
    target = write_evidence_json(tmp_path / "evidence.json", outcome.evidence)
    reloaded = read_evidence_json(target)
    assert reloaded == outcome.evidence
    summary = render_markdown_summary(outcome.evidence)
    assert "verdict: **pass**" in summary
    assert "golden_path" in summary
    assert "test-sha" in summary


def test_graph_rejects_cycle() -> None:
    graph = WorkGraph(
        nodes=(
            WorkNode(node_id="A", title="a", capability="software.backend", depends_on=("B",)),
            WorkNode(node_id="B", title="b", capability="software.backend", depends_on=("A",)),
        )
    )
    with pytest.raises(GraphValidationError) as excinfo:
        graph.validate_structure(max_tasks=8, max_depth=4)
    assert excinfo.value.code == "dependency_cycle"


def test_graph_rejects_unknown_dependency() -> None:
    graph = WorkGraph(nodes=(WorkNode(node_id="A", title="a", capability="c", depends_on=("Z",)),))
    with pytest.raises(GraphValidationError) as excinfo:
        graph.validate_structure(max_tasks=8, max_depth=4)
    assert excinfo.value.code == "unknown_dependency"


def test_graph_rejects_excess_depth() -> None:
    graph = WorkGraph(
        nodes=(
            WorkNode(node_id="A", title="a", capability="c"),
            WorkNode(node_id="B", title="b", capability="c", depends_on=("A",)),
            WorkNode(node_id="C", title="c", capability="c", depends_on=("B",)),
        )
    )
    with pytest.raises(GraphValidationError) as excinfo:
        graph.validate_structure(max_tasks=8, max_depth=2)
    assert excinfo.value.code == "graph_too_deep"


def test_graph_rejects_self_dependency_and_duplicates() -> None:
    self_dep = WorkGraph(
        nodes=(WorkNode(node_id="A", title="a", capability="c", depends_on=("A",)),)
    )
    with pytest.raises(GraphValidationError) as excinfo:
        self_dep.validate_structure(max_tasks=8, max_depth=4)
    assert excinfo.value.code == "self_dependency"
    duplicate = WorkGraph(
        nodes=(
            WorkNode(node_id="A", title="a", capability="c"),
            WorkNode(node_id="A", title="b", capability="c"),
        )
    )
    with pytest.raises(GraphValidationError) as excinfo:
        duplicate.validate_structure(max_tasks=8, max_depth=4)
    assert excinfo.value.code == "duplicate_node_id"


def test_graph_ready_poisoning_and_order() -> None:
    graph = WorkGraph(
        nodes=(
            WorkNode(node_id="B", title="b", capability="c"),
            WorkNode(node_id="A", title="a", capability="c"),
            WorkNode(node_id="D", title="d", capability="c", depends_on=("A", "B")),
        )
    )
    graph.validate_structure(max_tasks=8, max_depth=4)
    assert [node.node_id for node in graph.ready(frozenset(), frozenset())] == ["A", "B"]
    assert [node.node_id for node in graph.ready(frozenset({"A"}), frozenset())] == ["B"]
    assert [node.node_id for node in graph.ready(frozenset({"A", "B"}), frozenset())] == ["D"]
    # Failed A poisons dependent D; B stays ready.
    assert [node.node_id for node in graph.ready(frozenset(), frozenset({"A"}))] == ["B"]
    assert graph.ready(frozenset({"B"}), frozenset({"A"})) == ()


def test_team_selector_reports_unknown_capabilities_missing() -> None:
    selector = FixtureTeamSelector()
    decision = selector.select_team(
        required_capabilities=("software.backend", "business.nope"),
        workforce=static_workforce(),
    )
    assert decision.status == "blocked_missing_capability"
    assert decision.missing_capabilities == ("business.nope",)
    assert decision.manager_id == "agent-planner-1"
    assert set(decision.member_ids) <= {agent["id"] for agent in static_workforce()}


def test_team_selector_prefers_least_overlap_then_stable_key() -> None:
    selector = FixtureTeamSelector()
    workforce = (
        {
            "id": "wide",
            "agent_type": "specialist",
            "stable_key": "a",
            "capabilities": ["software.backend", "research.market", "software.testing"],
        },
        {
            "id": "narrow",
            "agent_type": "specialist",
            "stable_key": "b",
            "capabilities": ["software.backend"],
        },
    )
    decision = selector.select_team(
        required_capabilities=("software.backend",), workforce=workforce
    )
    assert decision.status == "completed"
    assert decision.member_ids == ("narrow",)
    assert decision.manager_id is None


def test_strict_output_evaluation_accepts_contract() -> None:
    evaluator = StrictJsonOutputEvaluation(max_output_chars=20000)
    valid = evaluator.evaluate(
        node_id="A", raw_output=json.dumps({"node_id": "A", "summary": "Done."})
    )
    assert valid.valid and valid.summary == "Done."
    assert not evaluator.evaluate(node_id="A", raw_output="{bad").valid
    assert evaluator.evaluate(node_id="A", raw_output="{bad").failure_code == "output_not_json"
    mismatch = evaluator.evaluate(
        node_id="A", raw_output=json.dumps({"node_id": "B", "summary": "x"})
    )
    assert mismatch.failure_code == "node_mismatch"
    assert (
        evaluator.evaluate(node_id="A", raw_output=json.dumps({"node_id": "A"})).failure_code
        == "output_schema_invalid"
    )
    small = StrictJsonOutputEvaluation(max_output_chars=10)
    assert small.evaluate(node_id="A", raw_output="x" * 11).failure_code == "output_too_large"


def test_gate_first_wins_and_cannot_clear() -> None:
    gate = ExecutionGate()
    gate.check("boundary")
    gate.request_cancellation("operator_cancel", "first")
    gate.request_cancellation("other", "second")
    assert gate.snapshot().reason_code == "operator_cancel"
    with pytest.raises(GateClosedError) as excinfo:
        gate.check("node_start")
    assert excinfo.value.reason_code == "cancelled"
    gate.activate_emergency_stop("estop", "stop")
    assert gate.snapshot().emergency_stopped is True
    with pytest.raises(GateClosedError) as excinfo:
        gate.check("node_start")
    assert excinfo.value.reason_code == "emergency_stop"


def test_timeline_scrubs_secrets_and_bounds_detail() -> None:
    recorder = EventRecorder(max_events=10)
    entry = recorder.record(
        StageKind.EXECUTE_SPECIALIST,
        "output_received",
        node_id="A",
        detail={
            "api_key": "sk-should-be-dropped",
            "summary": 'token "Bearer abc.def.ghi" leaked',
            "nested": {"password": "x"},
            "count": 3,
        },
    )
    assert entry is not None
    blob = json.dumps(entry.model_dump(mode="json"))
    assert "sk-should-be-dropped" not in blob
    assert "abc.def.ghi" not in blob
    assert "api_key" not in entry.detail
    assert entry.detail["count"] == 3
    assert len(scrub_detail({f"k{i}": i for i in range(40)})) <= 16


def test_recorder_drops_beyond_bound() -> None:
    recorder = EventRecorder(max_events=2)
    assert recorder.record(StageKind.COMPLETE, "one") is not None
    assert recorder.record(StageKind.COMPLETE, "two") is not None
    assert recorder.record(StageKind.COMPLETE, "three") is None
    assert recorder.dropped == 1


def test_fixture_undefined_fails_loudly() -> None:
    decomposer = FixtureDecomposer({})
    with pytest.raises(FixtureUndefinedError) as excinfo:
        decomposer.decompose(objective_key="x", team=None, required_capabilities=())  # type: ignore[arg-type]
    assert excinfo.value.code == "fixture_undefined"
    executor = FixtureSpecialistExecutor({})
    with pytest.raises(FixtureUndefinedError):
        executor.execute(
            node_id="A",
            attempt_number=1,
            is_repair=False,
            assignment=Assignment(node_id="A", agent_id="a", capability="c"),
        )
    synthesizer = FixtureSynthesizer()
    with pytest.raises(FixtureUndefinedError):
        synthesizer.synthesize(objective_key="x", results=(), expected_node_ids=("A",))


def test_model_call_bound_blocks_deterministically() -> None:
    spec = golden_path_spec()
    spec.bounds = HarnessBounds(max_model_calls=1)
    harness = build_harness(spec, in_memory_runtime())
    result = harness.run()
    assert result.terminal_state == "blocked"
    assert result.reason_code == "bounds_exceeded"


def test_deadline_bound_blocks_deterministically() -> None:
    spec = golden_path_spec()
    ticks = iter([1000.0, 2000.0 + HarnessBounds().deadline_seconds + 1.0])
    harness = build_harness(spec, in_memory_runtime(), monotonic=lambda: next(ticks, 10**9))
    result = harness.run()
    assert result.terminal_state == "blocked"
    assert result.reason_code == "bounds_exceeded"


def test_cancel_during_attempt_finalizes_in_flight_run() -> None:
    spec = cancellation_spec()

    def after_event(entry, harness) -> None:
        if entry.event == "attempt_started":
            harness.gate.request_cancellation("operator_cancel", "mid-attempt cancel")

    harness = build_harness(spec, in_memory_runtime(), hooks=HarnessHooks(after_event=after_event))
    result = harness.run()
    assert result.terminal_state == "cancelled"
    assert result.reason_code == "cancelled"
    run_id = f"auto-{spec.objective_key}-A"
    snapshot = harness.runtime.load_run(run_id)
    assert snapshot is not None
    assert snapshot.state.value == "cancelled"
    assert "A" not in result.node_results


def test_retry_succeeds_single_durable_result() -> None:
    outcome = run_scenario("retry_succeeds", "test-sha")
    assert outcome.evidence.verdict == "pass"
    adapter = outcome.harness.runtime
    run_id = outcome.result.node_results["A"].run_id
    assert adapter.is_processed(run_id, f"{run_id}:complete-run")
    assert adapter.is_processed(run_id, f"{run_id}:begin:a1")
    assert adapter.is_processed(run_id, f"{run_id}:begin:a2")


def test_deterministic_ids_and_clock() -> None:
    first = run_scenario("golden_path", "test-sha")
    second = run_scenario("golden_path", "test-sha")
    assert first.harness.runtime.issued_command_ids == second.harness.runtime.issued_command_ids
    assert [e.event for e in first.harness.recorder.events] == [
        e.event for e in second.harness.recorder.events
    ]


def test_harness_without_grounding_adapter_skips_context() -> None:
    spec = golden_path_spec()
    config = HarnessConfig(
        objective_key=spec.objective_key,
        title=spec.title,
        request_text=spec.request_text,
        task_id=f"autonomy-task-{spec.objective_key}",
        required_capabilities=spec.required_capabilities,
        workforce=static_workforce(),
        bounds=spec.bounds,
    )
    harness = AutonomyHarness(
        config,
        team_selector=FixtureTeamSelector(),
        decomposer=FixtureDecomposer({spec.objective_key: spec.decomposition}),
        executor=FixtureSpecialistExecutor(spec.scripts, spec.repair_scripts),
        evaluator=StrictJsonOutputEvaluation(max_output_chars=20000),
        synthesizer=FixtureSynthesizer(),
        runtime=in_memory_runtime(),
        gate=ExecutionGate(),
        clock=DeterministicClock(),
        grounding=None,
    )
    result = harness.run()
    assert result.terminal_state == "succeeded"
    assert any(e.event == "context_skipped_no_adapter" for e in harness.recorder.events)


def test_production_grounding_adapter_exposes_provenance() -> None:
    adapter = ContextGroundingAdapter(production_assembler())
    assert adapter.is_fixture is False
    assert adapter.provenance.implementation == "production"


def test_runtime_adapter_rejects_unknown_category_safely() -> None:
    from app.models.agent_runtime import FailureClassification

    assert (
        RuntimeExecutionAdapter.classify_category("not-a-category")
        == FailureClassification.EXECUTION
    )
    assert (
        RuntimeExecutionAdapter.classify_category("validation") == FailureClassification.VALIDATION
    )
