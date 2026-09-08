"""Stage ports (interfaces) for the autonomy acceptance loop.

Each stage of the conceptual autonomy loop is expressed as a small protocol so
that deterministic fixtures can stand in for unfinished PR #62/#63 production
implementations behind a clean boundary. Production implementations plug in
later by implementing the same protocol — the harness never branches on
concrete fixture types.

Provenance rules:

- Every port implementation exposes :attr:`StageProvenance.implementation`,
  either ``"production"`` (real main-branch behavior) or ``"fixture"`` (an
  explicit deterministic stand-in for unfinished work).
- Fixture outputs are always labeled in evidence; fixtures are never described
  as real inference or real orchestration.
- :data:`STAGE_PROVENANCE` documents, for each stage, what is production on
  ``main`` today and what is fixture-backed pending PR #62/#63.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from app.autonomy.graph import WorkGraph


class StageKind(StrEnum):
    GROUND_CONTEXT = "ground_context"
    SELECT_TEAM = "select_team"
    DECOMPOSE = "decompose"
    ASSIGN = "assign"
    EXECUTE_SPECIALIST = "execute_specialist"
    EVALUATE_OUTPUT = "evaluate_output"
    RECOVER_RETRY = "recover_retry"
    UNLOCK_DEPENDENCIES = "unlock_dependencies"
    SYNTHESIZE = "synthesize"
    COMPLETE = "complete"


@dataclass(frozen=True)
class StageProvenance:
    stage: StageKind
    implementation: str  # "production" | "fixture"
    detail: str


STAGE_PROVENANCE: dict[StageKind, StageProvenance] = {
    StageKind.GROUND_CONTEXT: StageProvenance(
        stage=StageKind.GROUND_CONTEXT,
        implementation="production",
        detail="Real ContextAssembler over explicit bounded sources.",
    ),
    StageKind.SELECT_TEAM: StageProvenance(
        stage=StageKind.SELECT_TEAM,
        implementation="fixture",
        detail=(
            "Deterministic greedy cover over the production capability taxonomy. "
            "Capability inference is fixture-supplied until local planning models "
            "are qualified. Model-backed selection plugs in through this port; no "
            "parity with any production selector is claimed."
        ),
    ),
    StageKind.DECOMPOSE: StageProvenance(
        stage=StageKind.DECOMPOSE,
        implementation="fixture",
        detail=(
            "Explicit scripted work graphs. Replace with the PR #62 "
            "DecompositionService via DecompositionPort when it merges."
        ),
    ),
    StageKind.ASSIGN: StageProvenance(
        stage=StageKind.ASSIGN,
        implementation="production",
        detail="Deterministic capability-satisfaction assignment using the catalog taxonomy.",
    ),
    StageKind.EXECUTE_SPECIALIST: StageProvenance(
        stage=StageKind.EXECUTE_SPECIALIST,
        implementation="fixture",
        detail=(
            "Scripted specialist outcomes executed through the real AgentRuntimeService "
            "command ledger (durable attempts, checkpoints, recovery). Only the "
            "specialist output content is fixture-supplied."
        ),
    ),
    StageKind.EVALUATE_OUTPUT: StageProvenance(
        stage=StageKind.EVALUATE_OUTPUT,
        implementation="production",
        detail="Deterministic JSON/schema validation owned by the harness.",
    ),
    StageKind.RECOVER_RETRY: StageProvenance(
        stage=StageKind.RECOVER_RETRY,
        implementation="production",
        detail="Real runtime recovery plans, bounded attempts, and checkpoint lineage.",
    ),
    StageKind.UNLOCK_DEPENDENCIES: StageProvenance(
        stage=StageKind.UNLOCK_DEPENDENCIES,
        implementation="production",
        detail="Deterministic readiness computation over the validated work graph.",
    ),
    StageKind.SYNTHESIZE: StageProvenance(
        stage=StageKind.SYNTHESIZE,
        implementation="fixture",
        detail=(
            "Deterministic fixture synthesis over completed node outputs. Replace "
            "with the PR #63 coordinator synthesis via SynthesisPort when it merges."
        ),
    ),
    StageKind.COMPLETE: StageProvenance(
        stage=StageKind.COMPLETE,
        implementation="production",
        detail="Deterministic terminal-state mapping owned by the harness.",
    ),
}

EXPECTED_FIXTURE_STAGES = frozenset(
    {StageKind.SELECT_TEAM, StageKind.DECOMPOSE, StageKind.EXECUTE_SPECIALIST, StageKind.SYNTHESIZE}
)


@dataclass(frozen=True)
class TeamDecision:
    status: str  # "completed" | "blocked_missing_capability"
    manager_id: str | None
    member_ids: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    missing_capabilities: tuple[str, ...]
    reason_code: str | None = None


@dataclass(frozen=True)
class Assignment:
    node_id: str
    agent_id: str
    capability: str


@dataclass(frozen=True)
class SpecialistOutcome:
    """One scripted or real specialist attempt result.

    Attributes:
        status: ``"succeeded"``, ``"failed"``, or ``"malformed"``.
        output_text: Raw specialist output (treated as untrusted data).
        summary: Validated summary produced by output evaluation (empty until
            the EVALUATE_OUTPUT stage accepts the output).
    """

    status: str
    output_text: str
    summary: str = ""
    failure_category: str | None = None
    failure_detail: str | None = None


@dataclass(frozen=True)
class ValidatedOutput:
    valid: bool
    summary: str = ""
    failure_code: str | None = None
    failure_detail: str | None = None


@dataclass(frozen=True)
class NodeResult:
    node_id: str
    agent_id: str
    attempt_count: int
    output_digest: str
    summary: str
    run_id: str
    checkpoint_id: str


@dataclass(frozen=True)
class SynthesisResult:
    summary: str
    input_node_ids: tuple[str, ...]
    inputs_digest: str


@runtime_checkable
class TeamSelectionPort(Protocol):
    provenance: StageProvenance

    def select_team(
        self,
        *,
        required_capabilities: tuple[str, ...],
        workforce: tuple[dict[str, Any], ...],
    ) -> TeamDecision: ...


@runtime_checkable
class DecompositionPort(Protocol):
    provenance: StageProvenance

    def decompose(
        self,
        *,
        objective_key: str,
        team: TeamDecision,
        required_capabilities: tuple[str, ...],
    ) -> WorkGraph: ...


@runtime_checkable
class SpecialistExecutionPort(Protocol):
    provenance: StageProvenance

    def execute(
        self,
        *,
        node_id: str,
        attempt_number: int,
        is_repair: bool,
        assignment: Assignment,
    ) -> SpecialistOutcome: ...


@runtime_checkable
class OutputEvaluationPort(Protocol):
    provenance: StageProvenance

    def evaluate(self, *, node_id: str, raw_output: str) -> ValidatedOutput: ...


@runtime_checkable
class SynthesisPort(Protocol):
    provenance: StageProvenance

    def synthesize(
        self,
        *,
        objective_key: str,
        results: tuple[NodeResult, ...],
        expected_node_ids: tuple[str, ...],
    ) -> SynthesisResult: ...
