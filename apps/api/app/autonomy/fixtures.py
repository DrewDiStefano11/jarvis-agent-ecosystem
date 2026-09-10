"""Explicit deterministic fixtures for unfinished autonomy stages.

Every class in this module is a stand-in for functionality that is not
production-ready on ``main`` (notably PR #62 decomposition/assignment and PR
#63 coordinator synthesis/retry). Fixtures are:

- deterministic (no randomness, no wall-clock dependence, ordered scripts),
- explicitly labeled (``is_fixture = True`` plus fixture provenance in every
  evidence record),
- replaceable (each implements a port from :mod:`app.autonomy.ports`).

Fixtures are never described as real inference, real planning, or real
orchestration. Production replacements plug in through the same ports without
changing the harness.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.agent_runtime.authorization import (
    RuntimeActorContext,
    RuntimeAuthorizationContext,
)
from app.autonomy.graph import WorkGraph, WorkNode
from app.autonomy.ports import (
    Assignment,
    DecompositionPort,
    NodeResult,
    SpecialistExecutionPort,
    SpecialistOutcome,
    StageKind,
    StageProvenance,
    SynthesisPort,
    SynthesisResult,
    TeamDecision,
    TeamSelectionPort,
)
from app.catalog.taxonomy import map_tags, satisfies
from app.models.agent_runtime import stable_hash
from app.models.identity import AuthorizationDecision


class FixtureUndefinedError(LookupError):
    """Raised when a fixture script has no entry for the requested key."""

    def __init__(self, fixture: str, key: str) -> None:
        self.fixture = fixture
        self.key = key
        self.code = "fixture_undefined"
        super().__init__(
            f"fixture {fixture!r} has no scripted entry for {key!r}; "
            "acceptance scenarios must declare explicit scripts"
        )


class FixtureTeamSelector:
    """Deterministic greedy team cover over the production capability taxonomy.

    Capability inference itself (objective text -> required capabilities) is
    fixture-supplied by the scenario until local planning models are qualified;
    the set-cover selection over the workforce is deterministic and reuses the
    production :func:`app.catalog.taxonomy.satisfies` semantics.

    Selection rules (documented, deterministic):

    - Manager: lowest ``stable_key`` workforce agent with ``agent_type ==
      "planner"``; ``None`` when no planner exists.
    - Specialists: greedy maximum coverage of required capabilities over
      non-planner agents; ties break toward fewer total capabilities, then
      lowest ``stable_key``. At most 6 specialists.
    - Required capabilities are normalized with :func:`map_tags`. Unknown
      capabilities are reported missing — never invented, never granted.
    """

    is_fixture = True
    provenance = StageProvenance(
        stage=StageKind.SELECT_TEAM,
        implementation="fixture",
        detail="Deterministic greedy cover; capability inference is fixture-supplied.",
    )

    MAX_SPECIALISTS = 6

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def select_team(
        self,
        *,
        required_capabilities: tuple[str, ...],
        workforce: tuple[dict[str, Any], ...],
    ) -> TeamDecision:
        self.calls.append(
            {
                "required_capabilities": list(required_capabilities),
                "workforce_ids": [agent.get("id") for agent in workforce],
            }
        )
        canonical, unknown = map_tags(list(required_capabilities))
        uncovered: set[str] = set(canonical) | set(unknown)
        ordered_workforce = sorted(workforce, key=lambda agent: str(agent.get("stable_key", "")))
        manager_id: str | None = None
        for agent in ordered_workforce:
            if agent.get("agent_type") == "planner":
                manager_id = str(agent.get("id"))
                break
        selected: list[dict[str, Any]] = []
        candidates = [agent for agent in ordered_workforce if agent.get("id") != manager_id]
        candidates = [agent for agent in candidates if agent.get("agent_type") != "planner"]
        while uncovered and len(selected) < self.MAX_SPECIALISTS:
            best: dict[str, Any] | None = None
            best_cover = 0
            best_total = 0
            for agent in candidates:
                caps = [str(item) for item in agent.get("capabilities", [])]
                cover = {req for req in uncovered for cap in caps if satisfies(cap, req)}
                if not cover:
                    continue
                total = len(caps)
                if (
                    best is None
                    or len(cover) > best_cover
                    or (
                        len(cover) == best_cover
                        and (
                            total < best_total
                            or (
                                total == best_total
                                and str(agent.get("stable_key", ""))
                                < str(best.get("stable_key", ""))
                            )
                        )
                    )
                ):
                    best = agent
                    best_cover = len(cover)
                    best_total = total
            if best is None:
                break
            selected.append(best)
            candidates.remove(best)
            caps = [str(item) for item in best.get("capabilities", [])]
            for req in [req for req in uncovered for cap in caps if satisfies(cap, req)]:
                uncovered.discard(req)
        missing = sorted(uncovered)
        status = "completed" if not missing else "blocked_missing_capability"
        return TeamDecision(
            status=status,
            manager_id=manager_id,
            member_ids=tuple(str(agent.get("id")) for agent in selected),
            required_capabilities=tuple(sorted(canonical)),
            missing_capabilities=tuple(missing),
            reason_code=None if not missing else "missing_capability",
        )


class FixtureDecomposer:
    """Scripted work-graph provider standing in for PR #62 decomposition.

    Scripts map ``objective_key`` to an explicit node list. Unknown objectives
    raise :class:`FixtureUndefinedError` — the harness never invents work.
    """

    is_fixture = True
    provenance = StageProvenance(
        stage=StageKind.DECOMPOSE,
        implementation="fixture",
        detail="Explicit scripted work graphs standing in for PR #62 decomposition.",
    )

    def __init__(self, scripts: dict[str, list[dict[str, Any]]]) -> None:
        self._scripts = scripts
        self.calls: list[str] = []

    def decompose(
        self,
        *,
        objective_key: str,
        team: TeamDecision,
        required_capabilities: tuple[str, ...],
    ) -> WorkGraph:
        self.calls.append(objective_key)
        if objective_key not in self._scripts:
            raise FixtureUndefinedError("FixtureDecomposer", objective_key)
        nodes = tuple(
            WorkNode(
                node_id=str(spec["node_id"]),
                title=str(spec.get("title", spec["node_id"])),
                capability=str(spec["capability"]),
                depends_on=tuple(str(dep) for dep in spec.get("depends_on", [])),
                summary_hint=str(spec.get("summary_hint", "")),
            )
            for spec in self._scripts[objective_key]
        )
        return WorkGraph(nodes=nodes)


OutcomeScript = dict[str, Any]


class FixtureSpecialistExecutor:
    """Scripted specialist outcomes; only output *content* is fixture-supplied.

    Durable execution (attempts, checkpoints, recovery, lineage) always flows
    through the real :class:`app.agent_runtime.service.AgentRuntimeService`.

    Scripts map ``node_id`` to an ordered list of attempt outcomes, each one of:

    - ``{"status": "succeed", "summary": ..., "output": ...}``
    - ``{"status": "fail", "category": ..., "detail": ...}``
    - ``{"status": "malformed", "output": ...}``

    Repair scripts map ``node_id`` to an ordered list of repair outcomes used
    when the harness retries a malformed output with stricter instructions.
    Attempts beyond the scripted list raise :class:`FixtureUndefinedError` so a
    runaway retry loop fails loudly instead of silently passing.
    """

    is_fixture = True
    provenance = StageProvenance(
        stage=StageKind.EXECUTE_SPECIALIST,
        implementation="fixture",
        detail="Scripted specialist output content over the real runtime ledger.",
    )

    def __init__(
        self,
        scripts: dict[str, list[OutcomeScript]],
        repair_scripts: dict[str, list[OutcomeScript]] | None = None,
    ) -> None:
        self._scripts = scripts
        self._repair_scripts = repair_scripts or {}
        self.calls: list[dict[str, Any]] = []

    def execute(
        self,
        *,
        node_id: str,
        attempt_number: int,
        is_repair: bool,
        assignment: Assignment,
    ) -> SpecialistOutcome:
        self.calls.append(
            {
                "node_id": node_id,
                "attempt_number": attempt_number,
                "is_repair": is_repair,
                "agent_id": assignment.agent_id,
            }
        )
        if is_repair:
            script = self._repair_scripts.get(node_id, [])
            index = sum(
                1 for call in self.calls if call["node_id"] == node_id and call["is_repair"]
            )
        else:
            script = self._scripts.get(node_id, [])
            index = attempt_number
        if index < 1 or index > len(script):
            raise FixtureUndefinedError(
                "FixtureSpecialistExecutor", f"{node_id}:repair={is_repair}:{index}"
            )
        entry = script[index - 1]
        status = str(entry.get("status", ""))
        if status == "succeed":
            return SpecialistOutcome(
                status="succeeded",
                output_text=str(entry.get("output", entry.get("summary", ""))),
            )
        if status == "fail":
            return SpecialistOutcome(
                status="failed",
                output_text=str(entry.get("detail", "specialist failed")),
                failure_category=str(entry.get("category", "execution")),
                failure_detail=str(entry.get("detail", "specialist failed")),
            )
        if status == "malformed":
            return SpecialistOutcome(status="malformed", output_text=str(entry.get("output", "")))
        raise FixtureUndefinedError("FixtureSpecialistExecutor", f"{node_id}:status={status!r}")


class FixtureSynthesizer:
    """Deterministic synthesis over completed node outputs (PR #63 stand-in).

    The synthesizer asserts it receives exactly the expected completed inputs —
    no invented results, no dropped results — and joins validated summaries in
    ``node_id`` order. It performs no inference.
    """

    is_fixture = True
    provenance = StageProvenance(
        stage=StageKind.SYNTHESIZE,
        implementation="fixture",
        detail="Deterministic join standing in for PR #63 coordinator synthesis.",
    )

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def synthesize(
        self,
        *,
        objective_key: str,
        results: tuple[NodeResult, ...],
        expected_node_ids: tuple[str, ...],
    ) -> SynthesisResult:
        received = tuple(sorted(result.node_id for result in results))
        self.calls.append({"objective_key": objective_key, "received": list(received)})
        if received != tuple(sorted(expected_node_ids)):
            raise FixtureUndefinedError(
                "FixtureSynthesizer",
                f"{objective_key}:expected={sorted(expected_node_ids)}:received={list(received)}",
            )
        ordered = sorted(results, key=lambda result: result.node_id)
        summary = "\n".join(f"{result.node_id}: {result.summary}" for result in ordered)
        digest = stable_hash(
            {"objective": objective_key, "inputs": [result.output_digest for result in ordered]}
        )
        return SynthesisResult(
            summary=summary,
            input_node_ids=tuple(result.node_id for result in ordered),
            inputs_digest=digest,
        )


class DeterministicClock:
    """Manual-advance clock for deterministic harness timestamps."""

    def __init__(self, base: datetime | None = None, *, second: int = 0) -> None:
        self._base = base or datetime(2026, 1, 1, tzinfo=UTC)
        self._second = second

    @property
    def second(self) -> int:
        return self._second

    def now(self) -> datetime:
        return self._base + timedelta(seconds=self._second)

    def tick(self, seconds: int = 1) -> datetime:
        self._second += max(0, seconds)
        return self.now()

    def restore(self, second: int) -> None:
        self._second = max(0, second)


class RecordingRuntimeAuthorizer:
    """Allow-all authorizer fixture that records every decision input.

    Used by the untrusted-context scenario to prove authorization decisions
    derive only from actor identity, operation, and run snapshot — never from
    assembled context text. It grants nothing durable: decisions are
    in-memory, scoped to the single scenario run, and never include
    ``runtime.admin``.
    """

    is_fixture = True

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def authenticate(self, actor_id: str | None) -> RuntimeActorContext:
        actor = (actor_id or "").strip() or "scenario-actor"
        self.calls.append({"call": "authenticate", "actor_id": actor})
        return RuntimeActorContext(actor_id=actor, stable_key="scenario")

    def authorize(
        self,
        actor: RuntimeActorContext,
        operation: str,
        *,
        specification: Any | None = None,
        snapshot: Any | None = None,
    ) -> RuntimeAuthorizationContext:
        target = specification or (snapshot.specification if snapshot is not None else None)
        resource_id = target.task_id if target is not None else "unknown"
        self.calls.append(
            {
                "call": "authorize",
                "actor_id": actor.actor_id,
                "operation": operation,
                "resource_id": resource_id,
            }
        )
        decision = AuthorizationDecision(
            allowed=True,
            permission_key=f"runtime.{operation}",
            actor_agent_id=actor.actor_id,
            resource_type="task",
            resource_id=resource_id,
            matched_grants=["scenario-fixture"],
            matched_denials=[],
            decisive_rule="scenario_fixture_allow",
            reason_code="scenario_fixture",
        )
        return RuntimeAuthorizationContext(
            actor=actor,
            permission_key=f"runtime.{operation}",
            resource_type="task",
            resource_id=resource_id,
            allowed_by_admin=False,
            decision=decision,
        )


def static_workforce() -> tuple[dict[str, Any], ...]:
    """Small deterministic workforce used by acceptance scenarios."""
    return (
        {
            "id": "agent-planner-1",
            "agent_type": "planner",
            "stable_key": "jarvis-planner",
            "capabilities": ["management.planning", "management.coordination"],
        },
        {
            "id": "agent-backend-1",
            "agent_type": "specialist",
            "stable_key": "specialist-backend",
            "capabilities": ["software.backend", "software.backend.api"],
        },
        {
            "id": "agent-research-1",
            "agent_type": "specialist",
            "stable_key": "specialist-research",
            "capabilities": ["research.market", "business.strategy"],
        },
        {
            "id": "agent-qa-1",
            "agent_type": "specialist",
            "stable_key": "specialist-qa",
            "capabilities": ["software.testing", "software.code-review"],
        },
    )


def _assert_port_conformance() -> None:
    assert isinstance(FixtureTeamSelector(), TeamSelectionPort)
    assert isinstance(FixtureDecomposer({}), DecompositionPort)
    assert isinstance(FixtureSpecialistExecutor({}), SpecialistExecutionPort)
    assert isinstance(FixtureSynthesizer(), SynthesisPort)


_assert_port_conformance()
