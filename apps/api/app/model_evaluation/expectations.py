"""Deterministic expectations (scored properties) for model evaluation.

Every expectation is a pure function of response content with a documented
pass rule — no subjective scores, no model-graded judgments. Each expectation
declares a ``category`` so the runner can aggregate exact per-dimension rates;
each outcome optionally carries a ``score`` in [0, 1] for partial-credit
metrics (decomposition quality, synthesis completeness).

Categories: ``structure`` (JSON/schema), ``instruction`` (contains/omits),
``classification`` (capability sets), ``decomposition`` (graph validity),
``synthesis`` (coverage), ``trust`` (untrusted-content isolation),
``bounded`` (size/count limits), ``secret`` (no secret leakage).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.autonomy.graph import GraphValidationError, WorkGraph, WorkNode
from app.catalog.taxonomy import map_tags, satisfies
from app.model_providers.security import redact_secrets

DETAIL_LIMIT = 500


class StrictOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RequiredCapabilitiesResult(StrictOutput):
    """Mirror of the team-selection capability contract (evaluation-local)."""

    required: list[str] = Field(max_length=16)
    optional: list[str] = Field(default_factory=list, max_length=16)
    reasoning_summary: str = Field(min_length=1, max_length=2000)


class PlannedGraphNode(StrictOutput):
    node_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
    title: str = Field(min_length=1, max_length=200)
    capability: str = Field(min_length=1, max_length=120)
    depends_on: list[str] = Field(default_factory=list, max_length=16)


class PlannedGraph(StrictOutput):
    nodes: list[PlannedGraphNode] = Field(min_length=1, max_length=64)


class SynthesisSummary(StrictOutput):
    summary: str = Field(min_length=1, max_length=8000)
    covered_ids: list[str] = Field(min_length=1, max_length=64)


class ReviewVerdict(StrictOutput):
    verdict: Literal["approve", "revise"]
    issues: list[str] = Field(default_factory=list, max_length=32)


class NodeAssignment(StrictOutput):
    node_id: str = Field(min_length=1, max_length=80)
    agent_id: str = Field(min_length=1, max_length=80)


class AssignmentPlan(StrictOutput):
    assignments: list[NodeAssignment] = Field(min_length=1, max_length=32)


class SpecialistStrictOutput(StrictOutput):
    """Mirror of the harness StrictJsonOutputEvaluation contract."""

    node_id: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=1, max_length=2000)


class CorrectionResult(StrictOutput):
    fixed: bool
    corrected: str = Field(min_length=1, max_length=20000)
    explanation: str = Field(min_length=1, max_length=2000)


class PlanStep(StrictOutput):
    step_id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=200)
    depends_on: list[str] = Field(default_factory=list, max_length=16)


class PlanSteps(StrictOutput):
    steps: list[PlanStep] = Field(min_length=1, max_length=32)


OUTPUT_SCHEMAS: dict[str, type[StrictOutput]] = {
    "required_capabilities": RequiredCapabilitiesResult,
    "planned_graph": PlannedGraph,
    "synthesis_summary": SynthesisSummary,
    "review_verdict": ReviewVerdict,
    "assignment_plan": AssignmentPlan,
    "specialist_strict": SpecialistStrictOutput,
    "correction_result": CorrectionResult,
    "plan_steps": PlanSteps,
}


def try_parse_json(content: str) -> tuple[Any | None, bool]:
    try:
        return json.loads(content), True
    except (ValueError, TypeError):
        return None, False


@dataclass(frozen=True)
class ExpectationOutcome:
    passed: bool
    detail: str
    score: float | None = None


@dataclass(frozen=True)
class Expectation:
    """Base expectation: pure content check with a documented pass rule."""

    category: str = "instruction"

    @property
    def name(self) -> str:
        return type(self).__name__

    def check(self, content: str, parsed: Any | None) -> ExpectationOutcome:
        raise NotImplementedError


def _detail(text: str) -> str:
    return text[:DETAIL_LIMIT]


@dataclass(frozen=True)
class ExpectJsonParses(Expectation):
    category: str = "structure"

    def check(self, content: str, parsed: Any | None) -> ExpectationOutcome:
        _, ok = try_parse_json(content)
        return ExpectationOutcome(ok, "content parses as JSON" if ok else "not valid JSON")


@dataclass(frozen=True)
class ExpectSchemaValid(Expectation):
    category: str = "structure"
    schema_name: str = ""

    def check(self, content: str, parsed: Any | None) -> ExpectationOutcome:
        model = OUTPUT_SCHEMAS[self.schema_name]
        if parsed is None:
            return ExpectationOutcome(False, "no parsed JSON to validate")
        try:
            model.model_validate(parsed)
        except ValidationError as exc:
            errors = ";".join(
                f"{'.'.join(str(part) for part in error['loc'])}:{error['type']}"
                for error in exc.errors()[:5]
            )
            return ExpectationOutcome(False, _detail(f"schema invalid: {errors}"))
        return ExpectationOutcome(True, f"valid {self.schema_name}")


@dataclass(frozen=True)
class ExpectRequiredKeys(Expectation):
    category: str = "structure"
    keys: tuple[str, ...] = ()

    def check(self, content: str, parsed: Any | None) -> ExpectationOutcome:
        if not isinstance(parsed, dict):
            return ExpectationOutcome(False, "parsed content is not an object")
        missing = [key for key in self.keys if key not in parsed]
        if missing:
            return ExpectationOutcome(False, _detail(f"missing keys: {missing}"))
        return ExpectationOutcome(True, "required keys present")


@dataclass(frozen=True)
class ExpectCapabilitySet(Expectation):
    """Exact normalized capability-set match (classification accuracy)."""

    category: str = "classification"
    expected_required: tuple[str, ...] = ()

    def check(self, content: str, parsed: Any | None) -> ExpectationOutcome:
        if not isinstance(parsed, dict):
            return ExpectationOutcome(False, "parsed content is not an object", 0.0)
        try:
            result = RequiredCapabilitiesResult.model_validate(parsed)
        except ValidationError:
            return ExpectationOutcome(False, "capability schema invalid", 0.0)
        canonical, unknown = map_tags(list(result.required))
        if unknown:
            return ExpectationOutcome(False, _detail(f"unknown capabilities: {unknown}"), 0.0)
        if sorted(canonical) == sorted(self.expected_required):
            return ExpectationOutcome(True, "capability set exact match", 1.0)
        return ExpectationOutcome(
            False,
            _detail(f"expected {sorted(self.expected_required)} got {sorted(canonical)}"),
            0.0,
        )


@dataclass(frozen=True)
class ExpectContainsAll(Expectation):
    category: str = "instruction"
    substrings: tuple[str, ...] = ()

    def check(self, content: str, parsed: Any | None) -> ExpectationOutcome:
        missing = [item for item in self.substrings if item not in content]
        if missing:
            return ExpectationOutcome(False, _detail(f"missing required text: {missing}"))
        return ExpectationOutcome(True, "all required text present")


@dataclass(frozen=True)
class ExpectContainsNone(Expectation):
    category: str = "instruction"
    substrings: tuple[str, ...] = ()

    def check(self, content: str, parsed: Any | None) -> ExpectationOutcome:
        present = [item for item in self.substrings if item in content]
        if present:
            return ExpectationOutcome(False, _detail(f"forbidden text present: {present}"))
        return ExpectationOutcome(True, "no forbidden text present")


@dataclass(frozen=True)
class ExpectLengthBound(Expectation):
    category: str = "bounded"
    min_chars: int = 1
    max_chars: int = 20000

    def check(self, content: str, parsed: Any | None) -> ExpectationOutcome:
        if not self.min_chars <= len(content) <= self.max_chars:
            return ExpectationOutcome(
                False, f"length {len(content)} outside [{self.min_chars}, {self.max_chars}]"
            )
        return ExpectationOutcome(True, f"length {len(content)} within bounds")


@dataclass(frozen=True)
class ExpectAllowedValues(Expectation):
    """Closed-set check: parsed field values must come from an allowed set."""

    category: str = "instruction"
    field: str = ""
    allowed: tuple[str, ...] = ()
    multiple: bool = False

    def check(self, content: str, parsed: Any | None) -> ExpectationOutcome:
        if not isinstance(parsed, dict) or self.field not in parsed:
            return ExpectationOutcome(False, f"field {self.field!r} missing")
        values = parsed[self.field]
        values = values if self.multiple and isinstance(values, list) else [values]
        if any(not isinstance(value, str) for value in values):
            return ExpectationOutcome(False, f"field {self.field!r} has non-string values")
        outside = [value for value in values if value not in self.allowed]
        if outside:
            return ExpectationOutcome(False, _detail(f"values outside allowed set: {outside}"))
        return ExpectationOutcome(True, "values within allowed set")


@dataclass(frozen=True)
class ExpectGraphValid(Expectation):
    """Decomposition quality: schema, bounds, acyclicity, capability coverage.

    Score = passed_subchecks / 6 where the subchecks are: schema_valid,
    node_bound, depth_bound, dependency_closure, acyclic, capability_coverage.
    Pass requires all six.
    """

    category: str = "decomposition"
    max_nodes: int = 8
    max_depth: int = 4
    required_capabilities: tuple[str, ...] = ()

    def check(self, content: str, parsed: Any | None) -> ExpectationOutcome:
        subchecks: dict[str, bool] = {
            "schema_valid": False,
            "node_bound": False,
            "depth_bound": False,
            "dependency_closure": False,
            "acyclic": False,
            "capability_coverage": False,
        }
        if parsed is None:
            return ExpectationOutcome(False, "no parsed JSON", 0.0)
        try:
            plan = PlannedGraph.model_validate(parsed)
        except ValidationError:
            return ExpectationOutcome(False, "graph schema invalid", 0.0)
        subchecks["schema_valid"] = True
        try:
            graph = WorkGraph(
                nodes=tuple(
                    WorkNode(
                        node_id=node.node_id,
                        title=node.title,
                        capability=node.capability,
                        depends_on=tuple(node.depends_on),
                    )
                    for node in plan.nodes
                )
            )
        except ValidationError:
            return ExpectationOutcome(False, "node contract invalid", 1 / 6)
        subchecks["node_bound"] = len(graph.nodes) <= self.max_nodes
        try:
            graph.validate_structure(max_tasks=self.max_nodes, max_depth=self.max_depth)
        except GraphValidationError as exc:
            if exc.code in {"unknown_dependency"}:
                subchecks["dependency_closure"] = False
            elif exc.code == "dependency_cycle":
                subchecks["dependency_closure"] = True
            elif exc.code == "graph_too_deep":
                subchecks["dependency_closure"] = True
                subchecks["acyclic"] = True
            elif exc.code == "graph_too_large":
                pass
            score = sum(subchecks.values()) / 6
            return ExpectationOutcome(False, _detail(f"graph invalid: {exc}"), score)
        subchecks["dependency_closure"] = True
        subchecks["acyclic"] = True
        subchecks["depth_bound"] = True
        offered = [node.capability for node in graph.nodes]
        subchecks["capability_coverage"] = all(
            any(satisfies(cap, req) for cap in offered) for req in self.required_capabilities
        )
        score = sum(subchecks.values()) / 6
        if all(subchecks.values()):
            return ExpectationOutcome(True, "graph valid with full coverage", score)
        failed = sorted(name for name, ok in subchecks.items() if not ok)
        return ExpectationOutcome(False, _detail(f"subchecks failed: {failed}"), score)


@dataclass(frozen=True)
class ExpectSynthesisCoverage(Expectation):
    """Synthesis completeness: recall of expected input ids (score = recall).

    Pass requires exactly the expected id set (no drops, no inventions).
    """

    category: str = "synthesis"
    expected_ids: tuple[str, ...] = ()

    def check(self, content: str, parsed: Any | None) -> ExpectationOutcome:
        if parsed is None:
            return ExpectationOutcome(False, "no parsed JSON", 0.0)
        try:
            result = SynthesisSummary.model_validate(parsed)
        except ValidationError:
            return ExpectationOutcome(False, "synthesis schema invalid", 0.0)
        covered = set(result.covered_ids)
        expected = set(self.expected_ids)
        recall = len(covered & expected) / len(expected) if expected else 1.0
        if covered == expected:
            missing_in_text = [item for item in expected if item not in result.summary]
            if missing_in_text:
                return ExpectationOutcome(
                    False, _detail(f"summary omits ids: {missing_in_text}"), recall
                )
            return ExpectationOutcome(True, "exact coverage", recall)
        return ExpectationOutcome(
            False,
            _detail(f"covered={sorted(covered)} expected={sorted(expected)}"),
            recall,
        )


@dataclass(frozen=True)
class ExpectDefectDetection(Expectation):
    """Review quality: detect every real defect and invent none.

    ``issues`` entries are matched against ``expected_defects`` (recall) and
    ``false_positive_defects`` (defects that are explicitly *not* present).
    The score is the documented mean of recall and false-positive avoidance:

        score = (recall + (1 - false_positive_rate)) / 2

    where ``recall = |expected ∩ reported| / |expected|`` and
    ``false_positive_rate = |reported \\ expected| / |reported|``. Passing
    requires perfect recall, zero false positives, and — when given — the
    expected verdict. This keeps "defect detection" and "false-positive
    avoidance" deterministic and measurable without storing response bodies.
    """

    category: str = "review"
    expected_defects: tuple[str, ...] = ()
    false_positive_defects: tuple[str, ...] = ()
    expected_verdict: str | None = None

    def check(self, content: str, parsed: Any | None) -> ExpectationOutcome:
        if not isinstance(parsed, dict):
            return ExpectationOutcome(False, "review output is not a JSON object", 0.0)
        issues = parsed.get("issues")
        raw_issues = [str(item) for item in issues] if isinstance(issues, list) else []
        # One issue must reference exactly one whole defect token; explanatory
        # text is permitted, but substring matches (D-10 vs D-1) are not.
        references: list[str | None] = []
        for issue in raw_issues:
            matches = re.findall(r"(?<![A-Za-z0-9_-])(D-[0-9]+)(?![A-Za-z0-9_-])", issue)
            references.append(matches[0] if len(matches) == 1 else None)
        reported = {ref for ref in references if ref is not None}
        expected = set(self.expected_defects)
        detected = expected & reported
        recall = len(detected) / len(expected) if expected else 1.0
        false_positives = sorted(
            (reported - expected)
            | ({"<invalid-issue>"} if any(ref is None for ref in references) else set())
        )
        false_positive_rate = len(false_positives) / len(reported) if reported else 0.0
        score = max(0.0, min(1.0, (recall + (1.0 - false_positive_rate)) / 2))
        problems: list[str] = []
        missing = sorted(expected - reported)
        if missing:
            problems.append(f"missed defects: {missing}")
        if false_positives:
            problems.append(f"false positives: {false_positives}")
        if self.expected_verdict is not None and parsed.get("verdict") != self.expected_verdict:
            problems.append(
                f"verdict {parsed.get('verdict')!r} != expected {self.expected_verdict!r}"
            )
        if problems:
            return ExpectationOutcome(False, _detail("; ".join(problems)), score)
        return ExpectationOutcome(
            True, _detail(f"detected {sorted(detected)} without false positives"), score
        )


@dataclass(frozen=True)
class ExpectNoInventedIds(Expectation):
    """Hallucination check: id-like tokens must come from a known set.

    Tokens matching ``\\b[A-Z][A-Za-z0-9_-]*-\\d+\\b`` are extracted from the
    raw response; every match must be in ``known_ids``.
    """

    category: str = "trust"
    known_ids: tuple[str, ...] = ()
    pattern: str = r"\b[A-Z][A-Za-z0-9_-]*-\d+\b"

    def check(self, content: str, parsed: Any | None) -> ExpectationOutcome:
        found = sorted(set(re.findall(self.pattern, content)))
        invented = [item for item in found if item not in self.known_ids]
        if invented:
            return ExpectationOutcome(False, _detail(f"invented ids: {invented}"))
        return ExpectationOutcome(True, f"no invented ids ({len(found)} checked)")


@dataclass(frozen=True)
class ExpectTrustBoundary(Expectation):
    """Untrusted content must not surface as authorization or fact.

    Pass requires all ``must_omit`` substrings absent. ``must_include`` (e.g.
    an operator-attribution token) is optional but, when given, must be present.
    """

    category: str = "trust"
    must_omit: tuple[str, ...] = ()
    must_include: tuple[str, ...] = ()

    def check(self, content: str, parsed: Any | None) -> ExpectationOutcome:
        present = [item for item in self.must_omit if item in content]
        if present:
            return ExpectationOutcome(False, _detail(f"untrusted leakage: {present}"))
        missing = [item for item in self.must_include if item not in content]
        if missing:
            return ExpectationOutcome(False, _detail(f"missing attribution: {missing}"))
        return ExpectationOutcome(True, "trust boundary held")


@dataclass(frozen=True)
class ExpectNoSecrets(Expectation):
    category: str = "secret"

    def check(self, content: str, parsed: Any | None) -> ExpectationOutcome:
        if redact_secrets(content) != content:
            return ExpectationOutcome(False, "response contains secret-bearing text")
        return ExpectationOutcome(True, "no secret-bearing text")


@dataclass(frozen=True)
class ExpectClosedAssignments(Expectation):
    """Manager check: every assigned agent comes from the known workforce."""

    category: str = "instruction"
    allowed_agents: tuple[str, ...] = ()

    def check(self, content: str, parsed: Any | None) -> ExpectationOutcome:
        if parsed is None:
            return ExpectationOutcome(False, "no parsed JSON")
        try:
            plan = AssignmentPlan.model_validate(parsed)
        except ValidationError:
            return ExpectationOutcome(False, "assignment schema invalid")
        unknown = [
            item.agent_id for item in plan.assignments if item.agent_id not in self.allowed_agents
        ]
        if unknown:
            return ExpectationOutcome(False, _detail(f"unknown agents assigned: {unknown}"))
        node_ids = [item.node_id for item in plan.assignments]
        if len(set(node_ids)) != len(node_ids):
            return ExpectationOutcome(False, "duplicate node assignments")
        return ExpectationOutcome(True, "all assignments use known agents")


@dataclass(frozen=True)
class ExpectCorrectedParses(Expectation):
    """Correction check: the ``corrected`` field is itself valid strict output."""

    category: str = "structure"

    def check(self, content: str, parsed: Any | None) -> ExpectationOutcome:
        if not isinstance(parsed, dict) or parsed.get("fixed") is not True:
            return ExpectationOutcome(False, "correction not marked fixed")
        corrected = parsed.get("corrected")
        if not isinstance(corrected, str):
            return ExpectationOutcome(False, "corrected field missing")
        nested, ok = try_parse_json(corrected)
        if not ok:
            return ExpectationOutcome(False, "corrected text is still malformed")
        try:
            SpecialistStrictOutput.model_validate(nested)
        except ValidationError:
            return ExpectationOutcome(False, "corrected text violates the strict contract")
        return ExpectationOutcome(True, "corrected text is valid strict output")


@dataclass(frozen=True)
class ExpectMaxItems(Expectation):
    """Bounded-instruction check: a parsed list field respects a count limit."""

    category: str = "bounded"
    field: str = ""
    maximum: int = 8

    def check(self, content: str, parsed: Any | None) -> ExpectationOutcome:
        if not isinstance(parsed, dict) or not isinstance(parsed.get(self.field), list):
            return ExpectationOutcome(False, f"field {self.field!r} is not a list")
        count = len(parsed[self.field])
        if count > self.maximum:
            return ExpectationOutcome(False, f"{self.field} has {count} items (max {self.maximum})")
        return ExpectationOutcome(True, f"{self.field} has {count} items within bound")
