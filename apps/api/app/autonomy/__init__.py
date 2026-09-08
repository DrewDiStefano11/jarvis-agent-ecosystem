"""Autonomy acceptance harness and observability (independent of PR #62/#63).

This package answers: "Can Jarvis take a high-level objective, choose a team,
decompose work, execute specialists, recover from failures, synthesize
results, and complete the objective reliably?"

Provenance is explicit everywhere: production stages reuse real main-branch
behavior (context assembler, capability taxonomy, agent-runtime ledger);
unfinished PR #62/#63 stages are explicit deterministic fixtures behind the
ports in :mod:`app.autonomy.ports`. See ``docs/autonomy-acceptance.md``.
"""

from app.autonomy.bounds import DEFAULT_BOUNDS, BoundsExceededError, BoundTracker, HarnessBounds
from app.autonomy.events import EventRecorder, TimelineEvent
from app.autonomy.evidence import (
    AcceptanceCheck,
    AcceptanceEvidence,
    EvidenceCounts,
    EvidenceIds,
    InferenceIdentity,
    make_check,
    read_evidence_json,
    render_markdown_summary,
    write_evidence_json,
    write_markdown_summary,
)
from app.autonomy.fixtures import (
    DeterministicClock,
    FixtureDecomposer,
    FixtureSpecialistExecutor,
    FixtureSynthesizer,
    FixtureTeamSelector,
    FixtureUndefinedError,
    static_workforce,
)
from app.autonomy.gates import ExecutionGate, GateClosedError, GateState
from app.autonomy.graph import GraphValidationError, WorkGraph, WorkNode
from app.autonomy.harness import (
    AutonomyHarness,
    HarnessConfig,
    HarnessHooks,
    HarnessResult,
    ResumeIntegrityError,
    ResumeState,
    StrictJsonOutputEvaluation,
)
from app.autonomy.ports import (
    EXPECTED_FIXTURE_STAGES,
    STAGE_PROVENANCE,
    Assignment,
    DecompositionPort,
    NodeResult,
    OutputEvaluationPort,
    SpecialistExecutionPort,
    SpecialistOutcome,
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
    map_runtime_error,
)

__all__ = [
    "DEFAULT_BOUNDS",
    "EXPECTED_FIXTURE_STAGES",
    "STAGE_PROVENANCE",
    "AcceptanceCheck",
    "AcceptanceEvidence",
    "Assignment",
    "AutonomyHarness",
    "BoundTracker",
    "BoundsExceededError",
    "ContextGroundingAdapter",
    "DecompositionPort",
    "DeterministicClock",
    "EvidenceCounts",
    "EvidenceIds",
    "EventRecorder",
    "ExecutionGate",
    "FixtureDecomposer",
    "FixtureSpecialistExecutor",
    "FixtureSynthesizer",
    "FixtureTeamSelector",
    "FixtureUndefinedError",
    "GateClosedError",
    "GateState",
    "GraphValidationError",
    "HarnessBounds",
    "HarnessConfig",
    "HarnessHooks",
    "HarnessResult",
    "InferenceIdentity",
    "NodeResult",
    "OutputEvaluationPort",
    "ResumeIntegrityError",
    "ResumeState",
    "RuntimeExecutionAdapter",
    "RuntimeRefusal",
    "SpecialistExecutionPort",
    "SpecialistOutcome",
    "StageKind",
    "StageProvenance",
    "StrictJsonOutputEvaluation",
    "SynthesisPort",
    "SynthesisResult",
    "TeamDecision",
    "TeamSelectionPort",
    "TimelineEvent",
    "ValidatedCheckpointPayload",
    "ValidatedOutput",
    "WorkGraph",
    "WorkNode",
    "digest_output",
    "make_check",
    "map_runtime_error",
    "read_evidence_json",
    "render_markdown_summary",
    "static_workforce",
    "write_evidence_json",
    "write_markdown_summary",
]
