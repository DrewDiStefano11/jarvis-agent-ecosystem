"""Deterministic fixture personas for qualification (CI-safe, never real models).

Every persona is a scripted *fixture model* that replays fixed responses for
the PR #64 case catalog. They exist so qualification logic — gates, scoring,
levels, ranking, recommendations, report generation — can be exercised in CI
without an installed model, and so operators can see what each verdict looks
like.

Fixture evidence is never real model performance: fixture profiles always carry
:data:`app.model_qualification.profile.FIXTURE_WARNING`.

Personas:

- ``fixture-model/qualified`` replays every reference output.
- ``fixture-model/conditional`` replays reference outputs twice with a harmless
  reformatting on the second pass, so every case passes but consistency fails
  (an advisory gate) — the canonical "useful but bounded" result.
- ``fixture-model/unqualified`` replays adversarial outputs, so the cases fail.
- ``fixture-model/malformed-json`` emits unparseable output for every case.
- ``fixture-model/unavailable`` simulates an offline provider (never quality 0).
- ``fixture-model/weak-decomposer`` is strong everywhere but emits invalid work
  graphs, which trips the decomposer hard gate.
- ``fixture-model/weak-reviewer`` misses a defect and reports a false positive.
- ``fixture-model/weak-classifier`` invents a capability on one case.
- ``fixture-model/weak-specialist`` obeys untrusted context inside data.
- ``fixture-model/repair-dependent`` needs one repair per case and then fixes it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.model_evaluation.cases import EvaluationCase, all_cases
from app.model_evaluation.providers import EvaluationUnavailableError, ScriptedFixtureProvider
from app.model_qualification.roles import role_names

#: Suffix appended to free-text reference outputs to create a harmless variation.
_VARIATION_SUFFIX = " The operator instruction is recorded verbatim."

#: Non-empty unparseable response (the provider contract rejects empty content).
UNPARSEABLE_RESPONSE = "{not valid json"


@dataclass(frozen=True)
class FixturePersona:
    name: str
    summary: str
    script: dict[str, tuple[Any, ...]] = field(default_factory=dict)
    repetitions: int = 1
    allow_repair: bool = False
    expected: dict[str, str] = field(default_factory=dict)

    def provider(self) -> ScriptedFixtureProvider:
        return ScriptedFixtureProvider(
            {case_id: list(entries) for case_id, entries in self.script.items()},
            model_name=self.name,
        )


def _vary(output: str) -> str:
    """Produce a different-but-equivalent response (consistency probe)."""
    try:
        parsed = json.loads(output)
    except (ValueError, TypeError):
        return output + _VARIATION_SUFFIX
    return json.dumps(parsed, sort_keys=True, indent=2)


def _reference_script(cases: tuple[EvaluationCase, ...]) -> dict[str, tuple[str, ...]]:
    return {case.case_id: (case.reference_output,) for case in cases}


def _adversarial_script(cases: tuple[EvaluationCase, ...]) -> dict[str, tuple[str, ...]]:
    """Pick one known-bad output per case, deterministically.

    Cases without a declared adversarial output replay an unparseable response
    (never an empty one: the provider contract rejects empty model content).
    """
    script: dict[str, tuple[str, ...]] = {}
    for case in cases:
        if not case.adversarial_outputs:
            script[case.case_id] = (UNPARSEABLE_RESPONSE,)
            continue
        if "malformed" in case.adversarial_outputs:
            chosen = case.adversarial_outputs["malformed"]
        else:
            chosen = case.adversarial_outputs[sorted(case.adversarial_outputs)[0]]
        script[case.case_id] = (chosen,)
    return script


def _reference_with_overrides(
    cases: tuple[EvaluationCase, ...], overrides: dict[str, str]
) -> dict[str, tuple[str, ...]]:
    script = _reference_script(cases)
    for case_id, output in overrides.items():
        script[case_id] = (output,)
    return script


def _persona_qualified(cases: tuple[EvaluationCase, ...]) -> FixturePersona:
    return FixturePersona(
        name="fixture-model/qualified",
        summary="Replays every reference output; expected to qualify for every role.",
        script=_reference_script(cases),
        expected={role: "qualified" for role in role_names()},
    )


def _persona_conditional(cases: tuple[EvaluationCase, ...]) -> FixturePersona:
    script = {case.case_id: (case.reference_output, _vary(case.reference_output)) for case in cases}
    return FixturePersona(
        name="fixture-model/conditional",
        summary=(
            "Passes every case twice with reformatted output; consistency fails, so "
            "every role is conditional (bounded use with review)."
        ),
        script=script,
        repetitions=2,
        expected={role: "conditional" for role in role_names()},
    )


def _persona_unqualified(cases: tuple[EvaluationCase, ...]) -> FixturePersona:
    return FixturePersona(
        name="fixture-model/unqualified",
        summary="Replays adversarial outputs; every role fails its mandatory gates.",
        script=_adversarial_script(cases),
        expected={role: "unqualified" for role in role_names()},
    )


def _persona_malformed(cases: tuple[EvaluationCase, ...]) -> FixturePersona:
    return FixturePersona(
        name="fixture-model/malformed-json",
        summary="Emits unparseable output for every case; malformed-response gate fails.",
        script={case.case_id: (UNPARSEABLE_RESPONSE,) for case in cases},
        expected={role: "unqualified" for role in role_names()},
    )


def _persona_unavailable(cases: tuple[EvaluationCase, ...]) -> FixturePersona:
    error = EvaluationUnavailableError("provider_unavailable", "simulated offline provider")
    return FixturePersona(
        name="fixture-model/unavailable",
        summary="Simulates an offline provider; every role is not_evaluated, never failed.",
        script={case.case_id: (error,) for case in cases},
        expected={role: "not_evaluated" for role in role_names()},
    )


def _persona_weak_decomposer(cases: tuple[EvaluationCase, ...]) -> FixturePersona:
    overrides = {
        "decompose-basic": _adversarial(cases, "decompose-basic", "cyclic"),
        "decompose-bounded": _adversarial(cases, "decompose-bounded", "oversized"),
    }
    expected = {role: "qualified" for role in role_names()}
    expected["decomposer"] = "unqualified"
    return FixturePersona(
        name="fixture-model/weak-decomposer",
        summary="Strong everywhere except work graphs; the decomposer hard gate fails.",
        script=_reference_with_overrides(cases, overrides),
        expected=expected,
    )


def _persona_weak_reviewer(cases: tuple[EvaluationCase, ...]) -> FixturePersona:
    overrides = {
        "review-defects": json.dumps(
            {"verdict": "revise", "issues": ["D-1", "D-3"]}, sort_keys=True
        )
    }
    expected = {role: "qualified" for role in role_names()}
    expected["reviewer"] = "unqualified"
    return FixturePersona(
        name="fixture-model/weak-reviewer",
        summary=(
            "Misses a real defect and reports a false positive; the reviewer "
            "defect-detection hard gate fails."
        ),
        script=_reference_with_overrides(cases, overrides),
        expected=expected,
    )


def _persona_weak_classifier(cases: tuple[EvaluationCase, ...]) -> FixturePersona:
    overrides = {
        "capability-unknown": _adversarial(cases, "capability-unknown", "invents_capability")
    }
    expected = {role: "qualified" for role in role_names()}
    expected["capability_classifier"] = "unqualified"
    return FixturePersona(
        name="fixture-model/weak-classifier",
        summary="Invents a capability on one case; classifier accuracy gate fails.",
        script=_reference_with_overrides(cases, overrides),
        expected=expected,
    )


def _persona_weak_specialist(cases: tuple[EvaluationCase, ...]) -> FixturePersona:
    overrides = {"trust-boundary": _adversarial(cases, "trust-boundary", "echoes_untrusted")}
    expected = {role: "qualified" for role in role_names()}
    expected["specialist"] = "unqualified"
    return FixturePersona(
        name="fixture-model/weak-specialist",
        summary="Follows untrusted context as instruction; the specialist trust gate fails.",
        script=_reference_with_overrides(cases, overrides),
        expected=expected,
    )


def _persona_repair_dependent(cases: tuple[EvaluationCase, ...]) -> FixturePersona:
    script: dict[str, tuple[str, ...]] = {}
    for case in cases:
        script[case.case_id] = (UNPARSEABLE_RESPONSE, case.reference_output)
    expected = {role: "conditional" for role in role_names()}
    return FixturePersona(
        name="fixture-model/repair-dependent",
        summary=(
            "Fails first and repairs correctly; repairs are measured, so frequent "
            "repairs keep the model conditional."
        ),
        script=script,
        allow_repair=True,
        expected=expected,
    )


def _adversarial(cases: tuple[EvaluationCase, ...], case_id: str, name: str) -> str:
    for case in cases:
        if case.case_id == case_id:
            if name not in case.adversarial_outputs:
                raise KeyError(f"case {case_id!r} has no adversarial output {name!r}")
            return case.adversarial_outputs[name]
    raise KeyError(f"unknown evaluation case: {case_id!r}")


def all_personas(
    cases: tuple[EvaluationCase, ...] | None = None,
) -> dict[str, FixturePersona]:
    """Every fixture persona keyed by its fixture model name."""
    catalog = cases or all_cases()
    builders = (
        _persona_qualified,
        _persona_conditional,
        _persona_unqualified,
        _persona_malformed,
        _persona_unavailable,
        _persona_weak_decomposer,
        _persona_weak_reviewer,
        _persona_weak_classifier,
        _persona_weak_specialist,
        _persona_repair_dependent,
    )
    return {persona.name: persona for persona in (builder(catalog) for builder in builders)}


def persona_names() -> tuple[str, ...]:
    return tuple(all_personas())


def persona(name: str) -> FixturePersona:
    try:
        return all_personas()[name]
    except KeyError as exc:
        raise ValueError(
            f"unknown fixture persona {name!r} (available: {list(persona_names())})"
        ) from exc


def build_provider(name: str) -> ScriptedFixtureProvider:
    return persona(name).provider()


def personas_document() -> dict[str, dict[str, Any]]:
    return {
        name: {
            "summary": item.summary,
            "repetitions": item.repetitions,
            "allow_repair": item.allow_repair,
            "expected": dict(item.expected),
            "scripted_cases": sorted(item.script),
        }
        for name, item in all_personas().items()
    }


__all__ = [
    "FixturePersona",
    "all_personas",
    "build_provider",
    "persona",
    "persona_names",
    "personas_document",
]
