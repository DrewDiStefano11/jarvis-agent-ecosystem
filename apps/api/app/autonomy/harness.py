"""Deterministic autonomy acceptance orchestrator.

:class:`AutonomyHarness` executes the conceptual autonomy loop::

    objective -> ground context -> select team -> decompose -> assign
    -> execute ready specialists -> evaluate outputs -> retry/recover
    -> unlock dependencies -> manager synthesis -> complete objective

Execution properties:

- Deterministic: injected clock, deterministic command/checkpoint/run ids, no
  randomness, no sleeps, ordered fixture scripts.
- Bounded: every loop enforces :class:`app.autonomy.bounds.HarnessBounds`.
- Durable: specialist execution flows through the real
  :class:`app.agent_runtime.service.AgentRuntimeService` ledger. Ready nodes
  are driven sequentially in ``node_id`` order; the recorded ``ready_set``
  event exposes the parallelizable surface without nondeterministic
  concurrency.
- Resumable: :meth:`run` accepts a :class:`ResumeState` verified against
  durable truth; completed work is never repeated.
- Fail-closed: authorization refusals, gate closures, and bound violations
  produce deterministic terminal states — never silent continuation.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.autonomy.bounds import BoundsExceededError, BoundTracker, HarnessBounds
from app.autonomy.events import EventRecorder, TimelineEvent, scrub_text
from app.autonomy.fixtures import DeterministicClock, FixtureUndefinedError
from app.autonomy.gates import ExecutionGate, GateClosedError
from app.autonomy.graph import GraphValidationError, WorkGraph, WorkNode
from app.autonomy.ports import (
    Assignment,
    DecompositionPort,
    NodeResult,
    OutputEvaluationPort,
    SpecialistExecutionPort,
    StageKind,
    StageProvenance,
    SynthesisPort,
    SynthesisResult,
    TeamDecision,
    TeamSelectionPort,
    ValidatedOutput,
)
from app.autonomy.production import (
    ContextGroundingAdapter,
    RuntimeExecutionAdapter,
    RuntimeRefusal,
    ValidatedCheckpointPayload,
    digest_output,
)
from app.catalog.taxonomy import satisfies
from app.context.assembler import hash_content
from app.models.agent_runtime import (
    AgentRunSnapshot,
    AgentRunState,
    FailureClassification,
    RecoveryStatus,
)
from app.models.context import (
    ContextAssembly,
    ContextPolicy,
    ContextSource,
    ContextSourceMetadata,
    ContextSourceType,
    CreateContextAssemblyRequest,
    TrustLevel,
)
from app.models.domain import Task

OBJECTIVE_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,39}$"

# Sentinel returned through the attempt pipeline to request the next attempt.
_RETRY_SENTINEL: Any = object()

EVALUATE_PROVENANCE = StageProvenance(
    stage=StageKind.EVALUATE_OUTPUT,
    implementation="production",
    detail="Deterministic JSON/schema validation owned by the harness.",
)


class StrictJsonOutputEvaluation:
    """Production output evaluation: strict JSON contract, no inference.

    A specialist output is accepted only when it is a JSON object
    ``{"node_id": <this node>, "summary": <1..2000 chars>}`` within the
    output-size bound. Anything else yields a machine-readable failure code
    (``output_not_json``, ``output_schema_invalid``, ``node_mismatch``,
    ``output_too_large``) and bounded repair behavior.
    """

    is_fixture = False
    provenance = EVALUATE_PROVENANCE

    def __init__(self, *, max_output_chars: int) -> None:
        self.max_output_chars = max_output_chars

    def evaluate(self, *, node_id: str, raw_output: str) -> ValidatedOutput:
        if len(raw_output) > self.max_output_chars:
            return ValidatedOutput(
                valid=False,
                failure_code="output_too_large",
                failure_detail=f"output exceeds {self.max_output_chars} characters",
            )
        try:
            payload = json.loads(raw_output)
        except (ValueError, TypeError):
            return ValidatedOutput(
                valid=False,
                failure_code="output_not_json",
                failure_detail="specialist output is not valid JSON",
            )
        if not isinstance(payload, dict):
            return ValidatedOutput(
                valid=False,
                failure_code="output_schema_invalid",
                failure_detail="specialist output must be a JSON object",
            )
        summary = payload.get("summary")
        echo = payload.get("node_id")
        if echo != node_id:
            return ValidatedOutput(
                valid=False,
                failure_code="node_mismatch",
                failure_detail="specialist output does not identify this work node",
            )
        if not isinstance(summary, str) or not 1 <= len(summary) <= 2000:
            return ValidatedOutput(
                valid=False,
                failure_code="output_schema_invalid",
                failure_detail="specialist output summary violates the size contract",
            )
        return ValidatedOutput(valid=True, summary=summary)


class HarnessConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective_key: str = Field(pattern=OBJECTIVE_KEY_PATTERN)
    title: str = Field(min_length=3, max_length=200)
    request_text: str = Field(min_length=3, max_length=8000)
    task_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
    project_id: str = Field(default="autonomy-acceptance", min_length=1, max_length=120)
    required_capabilities: tuple[str, ...] = Field(default=(), max_length=16)
    workforce: tuple[dict[str, Any], ...] = Field(default=())
    bounds: HarnessBounds = Field(default_factory=HarnessBounds)
    pause_points: frozenset[str] = Field(default=frozenset())
    max_completed_nodes: int | None = Field(default=None, ge=1, le=64)
    extra_context_sources: tuple[ContextSource, ...] = Field(default=())


@dataclass(frozen=True)
class ResumeState:
    completed: tuple[NodeResult, ...] = ()
    failed_nodes: tuple[str, ...] = ()
    clock_second: int = 0


@dataclass
class HarnessResult:
    terminal_state: str
    reason_code: str
    objective_key: str = ""
    failure_detail: str = ""
    team: TeamDecision | None = None
    graph: WorkGraph | None = None
    assignments: dict[str, Assignment] = field(default_factory=dict)
    node_results: dict[str, NodeResult] = field(default_factory=dict)
    failed_nodes: set[str] = field(default_factory=set)
    synthesis: SynthesisResult | None = None
    assembly: ContextAssembly | None = None
    run_ids: list[str] = field(default_factory=list)
    checkpoint_ids: list[str] = field(default_factory=list)
    clock_second: int = 0

    @property
    def execution_id(self) -> str:
        return f"autonomy-exec-{self.objective_key}"


HookFn = Callable[[TimelineEvent, "AutonomyHarness"], None]


@dataclass
class HarnessHooks:
    after_event: HookFn | None = None


class ResumeIntegrityError(RuntimeError):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(f"resume_integrity_violation: {detail}")


class AutonomyHarness:
    """Executes one bounded autonomy acceptance run."""

    def __init__(
        self,
        config: HarnessConfig,
        *,
        team_selector: TeamSelectionPort,
        decomposer: DecompositionPort,
        executor: SpecialistExecutionPort,
        evaluator: OutputEvaluationPort,
        synthesizer: SynthesisPort,
        runtime: RuntimeExecutionAdapter,
        gate: ExecutionGate,
        clock: DeterministicClock,
        grounding: ContextGroundingAdapter | None = None,
        hooks: HarnessHooks | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self.config = config
        self.team_selector = team_selector
        self.decomposer = decomposer
        self.executor = executor
        self.evaluator = evaluator
        self.synthesizer = synthesizer
        self.runtime = runtime
        self.gate = gate
        self.clock = clock
        self.grounding = grounding
        self.hooks = hooks or HarnessHooks()
        self.tracker = BoundTracker(config.bounds, monotonic=monotonic)
        self.recorder = EventRecorder(max_events=config.bounds.max_timeline_events, clock=clock)
        self._versions: dict[str, int] = {}

    # -- helpers ---------------------------------------------------------
    def _record(
        self,
        stage: StageKind,
        event: str,
        *,
        node_id: str | None = None,
        attempt_number: int | None = None,
        detail: Mapping[str, Any] | None = None,
    ) -> TimelineEvent | None:
        entry = self.recorder.record(
            stage, event, node_id=node_id, attempt_number=attempt_number, detail=detail
        )
        if entry is not None and self.hooks.after_event is not None:
            self.hooks.after_event(entry, self)
        return entry

    def _now(self) -> datetime:
        return self.clock.tick()

    def _run_id(self, node_id: str) -> str:
        return f"auto-{self.config.objective_key}-{node_id}"

    def _checkpoint_id(self, run_id: str, attempt: int) -> str:
        return f"{run_id}:ckpt-a{attempt}"

    def _terminal(
        self,
        result: HarnessResult,
        terminal_state: str,
        reason_code: str,
        failure_detail: str = "",
    ) -> HarnessResult:
        result.terminal_state = terminal_state
        result.reason_code = reason_code
        result.failure_detail = failure_detail
        result.clock_second = self.clock.second
        return result

    # -- main flow -------------------------------------------------------
    def run(self, resume: ResumeState | None = None) -> HarnessResult:
        result = HarnessResult(
            terminal_state="failed",
            reason_code="runtime_error",
            objective_key=self.config.objective_key,
        )
        completed: dict[str, NodeResult] = {}
        failed: set[str] = set()
        if resume is not None:
            try:
                self._verify_resume(resume, result, completed, failed)
            except ResumeIntegrityError as exc:
                self._record(StageKind.COMPLETE, "resume_rejected", detail={"detail": exc.detail})
                return self._terminal(result, "failed", "resume_integrity_violation", exc.detail)
        result.node_results = completed
        result.failed_nodes = failed
        self._record(
            StageKind.GROUND_CONTEXT,
            "objective_received",
            detail={
                "objective_key": self.config.objective_key,
                "title": self.config.title,
                "required_capabilities": list(self.config.required_capabilities),
                "resumed": resume is not None,
            },
        )
        try:
            self.tracker.check_deadline()
            if not self._ground_context(result):
                return result
            if not self._select_team(result):
                return result
            if not self._decompose_and_assign(result):
                return result
            terminal = self._execute_loop(result, completed, failed)
            if terminal is not None:
                return terminal
            return self._synthesize(result, completed)
        except GateClosedError as exc:
            return self._handle_gate_closure(result, completed, failed, exc)
        except BoundsExceededError as exc:
            self._record(
                StageKind.COMPLETE,
                "bounds_exceeded",
                detail={"bound": exc.bound, "limit": exc.limit, "observed": exc.observed},
            )
            return self._terminal(result, "blocked", "bounds_exceeded", str(exc))
        except FixtureUndefinedError as exc:
            self._record(
                StageKind.COMPLETE,
                "fixture_undefined",
                detail={"fixture": exc.fixture, "key": exc.key},
            )
            return self._terminal(result, "failed", "fixture_undefined", str(exc))
        except RuntimeRefusal as exc:
            return self._handle_refusal(result, completed, exc)

    # -- resume ----------------------------------------------------------
    def _verify_resume(
        self,
        resume: ResumeState,
        result: HarnessResult,
        completed: dict[str, NodeResult],
        failed: set[str],
    ) -> None:
        self.clock.restore(resume.clock_second)
        for node_result in resume.completed:
            snapshot = self.runtime.load_run(node_result.run_id)
            if snapshot is None or snapshot.state != AgentRunState.SUCCEEDED:
                raise ResumeIntegrityError(
                    f"completed node {node_result.node_id} is not durably succeeded"
                )
            checkpoints = {
                checkpoint.checkpoint_id: checkpoint
                for checkpoint in self.runtime.load_checkpoints(node_result.run_id)
            }
            checkpoint = checkpoints.get(node_result.checkpoint_id)
            if checkpoint is None or checkpoint.integrity_digest != node_result.output_digest:
                raise ResumeIntegrityError(
                    f"completed node {node_result.node_id} checkpoint lineage mismatch"
                )
            completed[node_result.node_id] = node_result
            if node_result.run_id not in result.run_ids:
                result.run_ids.append(node_result.run_id)
            if node_result.checkpoint_id not in result.checkpoint_ids:
                result.checkpoint_ids.append(node_result.checkpoint_id)
        for node_id in resume.failed_nodes:
            snapshot = self.runtime.load_run(self._run_id(node_id))
            if snapshot is None or snapshot.state != AgentRunState.FAILED:
                raise ResumeIntegrityError(f"failed node {node_id} is not durably failed")
            failed.add(node_id)
        self._record(
            StageKind.RECOVER_RETRY,
            "recovery_resumed",
            detail={
                "completed": sorted(completed),
                "failed": sorted(failed),
                "clock_second": self.clock.second,
            },
        )

    # -- stages ----------------------------------------------------------
    def _ground_context(self, result: HarnessResult) -> bool:
        if self.grounding is None:
            self._record(StageKind.GROUND_CONTEXT, "context_skipped_no_adapter", detail={})
            return True
        task = Task(
            id=self.config.task_id,
            title=self.config.title,
            description=self.config.request_text[:2000],
            request=self.config.request_text,
            createdBy="autonomy-harness",
            createdAt=self.clock.now(),
            updatedAt=self.clock.now(),
        )
        objective_source = ContextSource(
            sourceId=f"{self.config.objective_key}-objective",
            sourceType=ContextSourceType.TASK_REQUEST,
            trustLevel=TrustLevel.TASK_REQUEST,
            title=self.config.title,
            content=self.config.request_text,
            contentHash=hash_content(self.config.request_text),
            # Acceptance objectives are operator-authored and reviewed by
            # construction; approval admits the source to context, it never
            # raises its TASK_REQUEST trust level.
            metadata=ContextSourceMetadata(approved=True),
        )
        command = CreateContextAssemblyRequest(
            taskId=self.config.task_id,
            projectId=self.config.project_id,
            completionCriteria="Complete the high-level objective reliably.",
            policy=ContextPolicy(),
            sources=[objective_source, *self.config.extra_context_sources],
        )
        try:
            assembly = self.grounding.ground(task, command, created_at=self.clock.now())
        except Exception as exc:
            self._record(
                StageKind.GROUND_CONTEXT,
                "context_rejected",
                detail={"error": type(exc).__name__},
            )
            self._terminal(result, "blocked", "context_rejected", type(exc).__name__)
            return False
        result.assembly = assembly
        findings_by_source: dict[str, int] = {}
        for finding in assembly.manifest.injectionFindings:
            findings_by_source[finding.sourceId] = (
                findings_by_source.get(finding.sourceId, 0) + finding.count
            )
        self._record(
            StageKind.GROUND_CONTEXT,
            "context_grounded",
            detail={
                "assembly_id": assembly.id,
                "included": len(assembly.manifest.includedSources),
                "excluded": len(assembly.manifest.excludedSources),
                "trust_levels": {
                    item.sourceId: item.trustLevel.value
                    for item in assembly.manifest.includedSources
                },
                "injection_findings": findings_by_source,
            },
        )
        return True

    def _select_team(self, result: HarnessResult) -> bool:
        team = self.team_selector.select_team(
            required_capabilities=self.config.required_capabilities,
            workforce=self.config.workforce,
        )
        result.team = team
        if team.status != "completed":
            self._record(
                StageKind.SELECT_TEAM,
                "team_blocked",
                detail={
                    "reason_code": team.reason_code,
                    "missing": list(team.missing_capabilities),
                },
            )
            self._terminal(
                result,
                "blocked",
                "missing_capability",
                f"missing capabilities: {','.join(team.missing_capabilities)}",
            )
            return False
        self._record(
            StageKind.SELECT_TEAM,
            "team_selected",
            detail={
                "manager_id": team.manager_id,
                "member_ids": list(team.member_ids),
                "required": list(team.required_capabilities),
            },
        )
        return True

    def _decompose_and_assign(self, result: HarnessResult) -> bool:
        assert result.team is not None
        graph = self.decomposer.decompose(
            objective_key=self.config.objective_key,
            team=result.team,
            required_capabilities=self.config.required_capabilities,
        )
        try:
            graph.validate_structure(
                max_tasks=self.config.bounds.max_tasks,
                max_depth=self.config.bounds.max_dependency_depth,
            )
        except GraphValidationError as exc:
            self._record(StageKind.DECOMPOSE, "graph_rejected", detail={"code": exc.code})
            self._terminal(result, "blocked", "graph_invalid", str(exc))
            return False
        result.graph = graph
        order = graph.topological_order()
        self._record(
            StageKind.DECOMPOSE,
            "graph_planned",
            detail={"nodes": list(graph.node_ids()), "depth": graph.depth(), "order": list(order)},
        )
        workforce = {str(agent.get("id")): agent for agent in self.config.workforce}
        assignments: dict[str, Assignment] = {}
        for node_id in order:
            node = graph.by_id(node_id)
            candidates: list[str] = []
            for agent_id in result.team.member_ids:
                agent = workforce.get(agent_id)
                if agent is None:
                    continue
                caps = [str(item) for item in agent.get("capabilities", [])]
                if any(satisfies(cap, node.capability) for cap in caps):
                    candidates.append(agent_id)
            candidates.sort(key=lambda agent_id: str(workforce[agent_id].get("stable_key", "")))
            if not candidates:
                self._record(
                    StageKind.ASSIGN,
                    "node_unassignable",
                    node_id=node_id,
                    detail={"capability": node.capability},
                )
                self._terminal(
                    result,
                    "blocked",
                    "assignment_failed",
                    f"no team member satisfies capability {node.capability}",
                )
                return False
            assignments[node_id] = Assignment(
                node_id=node_id, agent_id=candidates[0], capability=node.capability
            )
            self._record(
                StageKind.ASSIGN,
                "node_assigned",
                node_id=node_id,
                detail={"agent_id": candidates[0], "capability": node.capability},
            )
        result.assignments = assignments
        return True

    def _execute_loop(
        self,
        result: HarnessResult,
        completed: dict[str, NodeResult],
        failed: set[str],
    ) -> HarnessResult | None:
        assert result.graph is not None
        graph = result.graph
        while len(completed) + len(failed) < len(graph.nodes):
            self.tracker.check_deadline()
            self.gate.check("scheduling")
            ready = graph.ready(frozenset(completed), frozenset(failed))
            self._record(
                StageKind.UNLOCK_DEPENDENCIES,
                "ready_set",
                detail={
                    "ready": [node.node_id for node in ready],
                    "completed": sorted(completed),
                    "failed": sorted(failed),
                },
            )
            if not ready:
                remaining = [
                    node.node_id
                    for node in graph.nodes
                    if node.node_id not in completed and node.node_id not in failed
                ]
                self._record(
                    StageKind.UNLOCK_DEPENDENCIES,
                    "dependencies_blocked",
                    detail={"remaining": remaining},
                )
                return self._terminal(
                    result, "failed", "dependency_failed", f"blocked nodes: {','.join(remaining)}"
                )
            node = ready[0]
            terminal = self._drive_node(result, graph.by_id(node.node_id), completed, failed)
            if terminal is not None:
                return terminal
        return None

    # -- node driving ----------------------------------------------------
    def _drive_node(
        self,
        result: HarnessResult,
        node: WorkNode,
        completed: dict[str, NodeResult],
        failed: set[str],
    ) -> HarnessResult | None:
        run_id = self._run_id(node.node_id)
        assignment = result.assignments[node.node_id]
        self.gate.check(f"node_start:{node.node_id}")
        snapshot = self._ensure_claimed(node, run_id, assignment)
        if run_id not in result.run_ids:
            result.run_ids.append(run_id)
        self._record(
            StageKind.EXECUTE_SPECIALIST,
            "node_started",
            node_id=node.node_id,
            detail={"run_id": run_id, "agent_id": assignment.agent_id},
        )
        if snapshot.state == AgentRunState.SUCCEEDED:
            rebuilt = self._rebuild_completed(node, run_id, assignment)
            if rebuilt is None:
                return self._terminal(
                    result, "failed", "resume_integrity_violation", f"node {node.node_id}"
                )
            completed[node.node_id] = rebuilt
            return None
        if snapshot.state == AgentRunState.FAILED:
            failed.add(node.node_id)
            self._record(
                StageKind.EXECUTE_SPECIALIST,
                "node_failed",
                node_id=node.node_id,
                detail={"resumed": True},
            )
            return self._terminal(result, "failed", "node_failed", f"node {node.node_id}")
        if snapshot.state == AgentRunState.CANCELLED:
            return self._terminal(result, "cancelled", "cancelled", f"node {node.node_id}")
        return self._attempt_loop(result, node, run_id, assignment, completed, failed)

    def _ensure_claimed(
        self,
        node: WorkNode,
        run_id: str,
        assignment: Assignment,
    ) -> AgentRunSnapshot:
        snapshot = self.runtime.load_run(run_id)
        if snapshot is None:
            spec = self.runtime.build_specification(
                run_id=run_id,
                task_id=self.config.task_id,
                agent_id=assignment.agent_id,
                operation=f"autonomy specialist work for {node.node_id}: {node.title}"[:500],
                capability=node.capability,
                created_at=self._now(),
                max_attempts=self.config.bounds.max_attempts_per_node,
            )
            outcome = self.runtime.create_run(
                spec,
                command_id=self.runtime.command_id(run_id, "create"),
                timestamp=self._now(),
            )
            snapshot = outcome.snapshot
        assert snapshot is not None
        version = snapshot.version
        if snapshot.state == AgentRunState.CREATED:
            outcome = self.runtime.queue_run(
                run_id,
                command_id=self.runtime.command_id(run_id, "queue"),
                expected_version=version,
                timestamp=self._now(),
            )
            version = outcome.snapshot.version
            snapshot = outcome.snapshot
        if snapshot.state == AgentRunState.QUEUED:
            outcome = self.runtime.claim_run(
                run_id,
                command_id=self.runtime.command_id(run_id, "claim"),
                expected_version=version,
                timestamp=self._now(),
            )
            snapshot = outcome.snapshot
        self._versions[run_id] = snapshot.version
        return snapshot

    def _rebuild_completed(
        self, node: WorkNode, run_id: str, assignment: Assignment
    ) -> NodeResult | None:
        checkpoints = self.runtime.load_checkpoints(run_id)
        attempts = self.runtime.load_attempts(run_id)
        if not checkpoints or not attempts:
            return None
        latest = max(checkpoints, key=lambda item: item.checkpoint_sequence)
        summary = str(latest.metadata.get("summaryPreview", ""))
        return NodeResult(
            node_id=node.node_id,
            agent_id=assignment.agent_id,
            attempt_count=len(attempts),
            output_digest=latest.integrity_digest,
            summary=summary,
            run_id=run_id,
            checkpoint_id=latest.checkpoint_id,
        )

    def _attempt_loop(
        self,
        result: HarnessResult,
        node: WorkNode,
        run_id: str,
        assignment: Assignment,
        completed: dict[str, NodeResult],
        failed: set[str],
    ) -> HarnessResult | None:
        attempts = list(self.runtime.load_attempts(run_id))
        snapshot = self.runtime.load_run(run_id)
        assert snapshot is not None
        if snapshot.state == AgentRunState.STARTING and snapshot.active_attempt_id is not None:
            active = next(
                item for item in attempts if item.attempt_id == snapshot.active_attempt_id
            )
            return self._execute_attempt(
                result, node, run_id, assignment, completed, failed, active.attempt_number
            )
        if snapshot.state == AgentRunState.RUNNING and snapshot.active_attempt_id is not None:
            self._record(
                StageKind.RECOVER_RETRY,
                "interrupted_attempt_found",
                node_id=node.node_id,
                detail={"attempt_id": snapshot.active_attempt_id},
            )
            version = snapshot.version
            outcome = self.runtime.fail_attempt(
                run_id,
                command_id=self.runtime.command_id(
                    run_id, "fail-attempt", f"a{len(attempts)}-interrupted"
                ),
                expected_version=version,
                timestamp=self._now(),
                category=FailureClassification.TIMEOUT,
                detail="Worker interrupted before resume; resuming from durable truth",
            )
            self._versions[run_id] = outcome.snapshot.version
            self._record(
                StageKind.RECOVER_RETRY,
                "recovery_resumed",
                node_id=node.node_id,
                detail={"interrupted_attempts": len(attempts)},
            )
        attempt_number = len(self.runtime.load_attempts(run_id)) + 1
        while attempt_number <= self.config.bounds.max_attempts_per_node:
            self.tracker.check_deadline()
            self.gate.check(f"attempt_start:{node.node_id}:{attempt_number}")
            resume_from: str | None = None
            if attempt_number > 1 or self._recovery_required(run_id):
                plan_outcome = self.runtime.request_recovery_plan(
                    run_id,
                    command_id=self.runtime.command_id(run_id, "recovery", f"a{attempt_number}"),
                    expected_version=self._versions[run_id],
                    timestamp=self._now(),
                )
                self._versions[run_id] = plan_outcome.snapshot.version
                plan = plan_outcome.recovery_plan
                selected = (
                    plan.selected_checkpoint.checkpoint_id
                    if plan and plan.selected_checkpoint
                    else None
                )
                resume_from = selected
                self._record(
                    StageKind.RECOVER_RETRY,
                    "recovery_planned",
                    node_id=node.node_id,
                    attempt_number=attempt_number,
                    detail={"resume_from_checkpoint_id": selected},
                )
                unblocked = self.runtime.unblock_run(
                    run_id,
                    command_id=self.runtime.command_id(run_id, "unblock", f"a{attempt_number}"),
                    expected_version=self._versions[run_id],
                    timestamp=self._now(),
                )
                self._versions[run_id] = unblocked.snapshot.version
                self._record(
                    StageKind.RECOVER_RETRY,
                    "recovery_unblocked",
                    node_id=node.node_id,
                    attempt_number=attempt_number,
                    detail={},
                )
            begin = self.runtime.begin_attempt(
                run_id,
                command_id=self.runtime.command_id(run_id, "begin", f"a{attempt_number}"),
                expected_version=self._versions[run_id],
                timestamp=self._now(),
                resume_from_checkpoint_id=resume_from,
            )
            self._versions[run_id] = begin.snapshot.version
            terminal = self._execute_attempt(
                result, node, run_id, assignment, completed, failed, attempt_number
            )
            if terminal is _RETRY_SENTINEL:
                attempt_number += 1
                continue
            if terminal is not None:
                return terminal
            return None
        return self._terminal(
            result, "failed", "retry_exhausted", f"node {node.node_id} exceeded attempt bound"
        )

    def _recovery_required(self, run_id: str) -> bool:
        snapshot = self.runtime.load_run(run_id)
        return snapshot is not None and snapshot.recovery_status in {
            RecoveryStatus.REQUIRED,
            RecoveryStatus.PLANNED,
        }

    def _execute_attempt(
        self,
        result: HarnessResult,
        node: WorkNode,
        run_id: str,
        assignment: Assignment,
        completed: dict[str, NodeResult],
        failed: set[str],
        attempt_number: int,
    ) -> HarnessResult | object | None:
        started = self.runtime.start_attempt(
            run_id,
            command_id=self.runtime.command_id(run_id, "start", f"a{attempt_number}"),
            expected_version=self._versions[run_id],
            timestamp=self._now(),
        )
        self._versions[run_id] = started.snapshot.version
        self.tracker.record_attempt()
        self.tracker.record_model_call()
        self._record(
            StageKind.EXECUTE_SPECIALIST,
            "attempt_started",
            node_id=node.node_id,
            attempt_number=attempt_number,
            detail={"run_id": run_id},
        )
        outcome = self.executor.execute(
            node_id=node.node_id,
            attempt_number=attempt_number,
            is_repair=False,
            assignment=assignment,
        )
        self._record(
            StageKind.EXECUTE_SPECIALIST,
            "output_received",
            node_id=node.node_id,
            attempt_number=attempt_number,
            detail={"status": outcome.status, "output_chars": len(outcome.output_text)},
        )
        if outcome.status == "failed":
            return self._fail_attempt_outcome(
                result,
                node,
                run_id,
                completed,
                failed,
                attempt_number,
                outcome.failure_category or "execution",
                outcome.failure_detail or "failed",
            )
        accepted_text, validated = self._evaluate_with_repairs(
            result, node, run_id, assignment, attempt_number, outcome.output_text
        )
        if not validated.valid:
            return self._fail_attempt_outcome(
                result,
                node,
                run_id,
                completed,
                failed,
                attempt_number,
                "validation",
                validated.failure_detail or "output validation failed",
            )
        return self._complete_attempt_outcome(
            result, node, run_id, assignment, completed, attempt_number, accepted_text, validated
        )

    def _evaluate_with_repairs(
        self,
        result: HarnessResult,
        node: WorkNode,
        run_id: str,
        assignment: Assignment,
        attempt_number: int,
        raw_output: str,
    ) -> tuple[str, ValidatedOutput]:
        evaluated = self.evaluator.evaluate(node_id=node.node_id, raw_output=raw_output)
        accepted = raw_output
        repairs_used = 0
        while not evaluated.valid and repairs_used < self.config.bounds.max_node_repairs:
            self._record(
                StageKind.EVALUATE_OUTPUT,
                "output_rejected",
                node_id=node.node_id,
                attempt_number=attempt_number,
                detail={
                    "failure_code": evaluated.failure_code,
                    "repair_number": repairs_used + 1,
                },
            )
            self.tracker.record_model_call()
            self.tracker.record_repair()
            repair = self.executor.execute(
                node_id=node.node_id,
                attempt_number=attempt_number,
                is_repair=True,
                assignment=assignment,
            )
            repairs_used += 1
            if repair.status == "failed":
                evaluated = ValidatedOutput(
                    valid=False,
                    failure_code="repair_failed",
                    failure_detail=repair.failure_detail or "repair attempt failed",
                )
                break
            accepted = repair.output_text
            evaluated = self.evaluator.evaluate(node_id=node.node_id, raw_output=accepted)
        if evaluated.valid:
            self._record(
                StageKind.EVALUATE_OUTPUT,
                "output_validated",
                node_id=node.node_id,
                attempt_number=attempt_number,
                detail={
                    "repairs_used": repairs_used,
                    "output_digest": digest_output(accepted)[:16],
                },
            )
        return accepted, evaluated

    def _fail_attempt_outcome(
        self,
        result: HarnessResult,
        node: WorkNode,
        run_id: str,
        completed: dict[str, NodeResult],
        failed: set[str],
        attempt_number: int,
        category_name: str,
        detail: str,
    ) -> HarnessResult | object:
        category = RuntimeExecutionAdapter.classify_category(category_name)
        self._record(
            StageKind.EXECUTE_SPECIALIST,
            "attempt_failed",
            node_id=node.node_id,
            attempt_number=attempt_number,
            detail={"category": category.value},
        )
        failed_outcome = self.runtime.fail_attempt(
            run_id,
            command_id=self.runtime.command_id(run_id, "fail-attempt", f"a{attempt_number}"),
            expected_version=self._versions[run_id],
            timestamp=self._now(),
            category=category,
            detail=detail,
        )
        self._versions[run_id] = failed_outcome.snapshot.version
        if attempt_number >= self.config.bounds.max_attempts_per_node:
            terminal = self.runtime.fail_run(
                run_id,
                command_id=self.runtime.command_id(run_id, "fail-run"),
                expected_version=self._versions[run_id],
                timestamp=self._now(),
                category=category,
                detail=f"retry exhausted after {attempt_number} attempts",
            )
            self._versions[run_id] = terminal.snapshot.version
            failed.add(node.node_id)
            self._record(
                StageKind.EXECUTE_SPECIALIST,
                "node_failed",
                node_id=node.node_id,
                detail={"attempts": attempt_number, "category": category.value},
            )
            return self._terminal(
                result, "failed", "retry_exhausted", f"node {node.node_id}: {category.value}"
            )
        return _RETRY_SENTINEL

    def _complete_attempt_outcome(
        self,
        result: HarnessResult,
        node: WorkNode,
        run_id: str,
        assignment: Assignment,
        completed: dict[str, NodeResult],
        attempt_number: int,
        accepted_text: str,
        validated: ValidatedOutput,
    ) -> HarnessResult | None:
        self.gate.check(f"checkpoint:{node.node_id}:{attempt_number}")
        digest = digest_output(accepted_text)
        checkpoint_id = self._checkpoint_id(run_id, attempt_number)
        recorded = self.runtime.record_validated_checkpoint(
            run_id,
            command_id=self.runtime.command_id(run_id, "checkpoint", f"a{attempt_number}"),
            checkpoint_id=checkpoint_id,
            expected_version=self._versions[run_id],
            timestamp=self._now(),
            payload=ValidatedCheckpointPayload(
                node_id=node.node_id,
                attempt_number=attempt_number,
                output_digest=digest,
                summary=scrub_text(validated.summary),
            ),
        )
        self._versions[run_id] = recorded.snapshot.version
        if checkpoint_id not in result.checkpoint_ids:
            result.checkpoint_ids.append(checkpoint_id)
        self._record(
            StageKind.EXECUTE_SPECIALIST,
            "checkpoint_recorded",
            node_id=node.node_id,
            attempt_number=attempt_number,
            detail={"checkpoint_id": checkpoint_id, "output_digest": digest[:16]},
        )
        if f"after_checkpoint:{node.node_id}:{attempt_number}" in self.config.pause_points:
            self._record(StageKind.COMPLETE, "objective_paused", detail={"point": "checkpoint"})
            return self._terminal(result, "paused", "paused_partial", "pause point reached")
        completed_attempt = self.runtime.complete_attempt(
            run_id,
            command_id=self.runtime.command_id(run_id, "complete-attempt", f"a{attempt_number}"),
            expected_version=self._versions[run_id],
            timestamp=self._now(),
        )
        self._versions[run_id] = completed_attempt.snapshot.version
        finished = self.runtime.complete_run(
            run_id,
            command_id=self.runtime.command_id(run_id, "complete-run"),
            expected_version=self._versions[run_id],
            timestamp=self._now(),
        )
        self._versions[run_id] = finished.snapshot.version
        completed[node.node_id] = NodeResult(
            node_id=node.node_id,
            agent_id=assignment.agent_id,
            attempt_count=attempt_number,
            output_digest=digest,
            summary=validated.summary,
            run_id=run_id,
            checkpoint_id=checkpoint_id,
        )
        self._record(
            StageKind.EXECUTE_SPECIALIST,
            "node_completed",
            node_id=node.node_id,
            attempt_number=attempt_number,
            detail={"checkpoint_id": checkpoint_id, "output_digest": digest[:16]},
        )
        limit = self.config.max_completed_nodes
        if f"after_node:{node.node_id}" in self.config.pause_points or (
            limit is not None and len(completed) >= limit
        ):
            self._record(StageKind.COMPLETE, "objective_paused", detail={"point": "node"})
            return self._terminal(result, "paused", "paused_partial", "pause point reached")
        return None

    # -- synthesis -------------------------------------------------------
    def _synthesize(self, result: HarnessResult, completed: dict[str, NodeResult]) -> HarnessResult:
        assert result.graph is not None
        self.gate.check("synthesis")
        self.tracker.record_model_call()
        order = result.graph.topological_order()
        ordered = tuple(completed[node_id] for node_id in order)
        self._record(
            StageKind.SYNTHESIZE,
            "synthesis_started",
            detail={"inputs": [item.node_id for item in ordered]},
        )
        try:
            synthesis = self.synthesizer.synthesize(
                objective_key=self.config.objective_key,
                results=ordered,
                expected_node_ids=tuple(order),
            )
        except FixtureUndefinedError as exc:
            return self._terminal(result, "failed", "synthesis_input_mismatch", str(exc))
        result.synthesis = synthesis
        self._record(
            StageKind.SYNTHESIZE,
            "synthesis_completed",
            detail={
                "inputs_digest": synthesis.inputs_digest[:16],
                "summary_chars": len(synthesis.summary),
            },
        )
        self._record(StageKind.COMPLETE, "objective_succeeded", detail={})
        return self._terminal(result, "succeeded", "ok")

    # -- terminal handling -------------------------------------------------
    def _handle_gate_closure(
        self,
        result: HarnessResult,
        completed: dict[str, NodeResult],
        failed: set[str],
        exc: GateClosedError,
    ) -> HarnessResult:
        if exc.reason_code == "cancelled":
            self._record(StageKind.COMPLETE, "cancellation_observed", detail={})
            self._cancel_in_flight(result)
            self._record(StageKind.COMPLETE, "objective_cancelled", detail={})
            return self._terminal(result, "cancelled", "cancelled", str(exc))
        self._record(StageKind.COMPLETE, "emergency_stop_observed", detail={})
        return self._terminal(result, "blocked", "emergency_stop", str(exc))

    def _cancel_in_flight(self, result: HarnessResult) -> None:
        """Finalize non-terminal runs through the runtime cancellation chain.

        This is operator-authorized teardown, not autonomous execution, so it
        intentionally bypasses the closed gate. Each step is idempotent by
        deterministic command id.
        """
        for run_id in result.run_ids:
            snapshot = self.runtime.load_run(run_id)
            if snapshot is None or snapshot.state in {
                AgentRunState.SUCCEEDED,
                AgentRunState.FAILED,
                AgentRunState.CANCELLED,
                AgentRunState.TIMED_OUT,
                AgentRunState.ABANDONED,
            }:
                continue
            version = snapshot.version
            try:
                outcome = self.runtime.request_cancellation(
                    run_id,
                    command_id=self.runtime.command_id(run_id, "cancel-request"),
                    expected_version=version,
                    timestamp=self._now(),
                )
                version = outcome.snapshot.version
                snapshot = outcome.snapshot
                if snapshot.active_attempt_id is not None:
                    outcome = self.runtime.confirm_cancellation_start(
                        run_id,
                        command_id=self.runtime.command_id(run_id, "cancel-start"),
                        expected_version=version,
                        timestamp=self._now(),
                    )
                    version = outcome.snapshot.version
                self.runtime.confirm_cancellation(
                    run_id,
                    command_id=self.runtime.command_id(run_id, "cancel-confirm"),
                    expected_version=version,
                    timestamp=self._now(),
                )
            except RuntimeRefusal as exc:
                self._record(
                    StageKind.COMPLETE,
                    "cancellation_refused",
                    detail={"run_id": run_id, "code": exc.code},
                )
            else:
                self._record(StageKind.COMPLETE, "run_cancelled", detail={"run_id": run_id})

    def _handle_refusal(
        self,
        result: HarnessResult,
        completed: dict[str, NodeResult],
        exc: RuntimeRefusal,
    ) -> HarnessResult:
        self._record(
            StageKind.COMPLETE,
            "runtime_refused",
            detail={"code": exc.code, "operation": exc.operation},
        )
        if exc.code in {"authorization_denied", "authentication_failed"}:
            reason = "authorization_revoked" if completed else "authorization_denied"
            return self._terminal(result, "blocked", reason, f"{exc.code}: {exc.operation}")
        return self._terminal(result, "failed", f"runtime_{exc.code}", exc.operation)
