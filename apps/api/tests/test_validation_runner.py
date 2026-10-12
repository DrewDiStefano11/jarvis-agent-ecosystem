"""Fixed family descriptions and non-bypassable execution denial."""

import pytest

from app.core.errors import DomainError
from app.development_validation.evidence import build_execution_plan
from app.development_validation.planner import digest
from app.models.development_validation import CodeState, FileChange
from app.models.validation_runner import RunnerAvailability
from app.validation_runner.catalog import (
    DisabledValidationRunner,
    describe_invocations,
    verify_invocations,
)


def plan(**options):
    state = CodeState(
        repository_identity="github.com/example/jarvis",
        head_sha="a" * 40,
        base_sha="b" * 40,
        changes=(
            FileChange(
                path="apps/api/app/example.py",
                before_hash=None,
                before_mode=None,
                after_hash="c" * 64,
                after_mode="100644",
            ),
        ),
    )
    return build_execution_plan(state, **options)


def test_fixed_families_cover_every_publication_gate_and_browser():
    expected = plan(boundary="publication", browser_required=True)
    invocations = describe_invocations(expected)
    assert len(invocations) == 9
    assert verify_invocations(expected, invocations) == invocations
    assert {g.gate for g in invocations} == set(DisabledValidationRunner.availability().gates)
    ruff = next(g for g in invocations if g.gate == "backend_ruff")
    assert ruff.invocations[0].arguments[-1] == "--check"
    assert ruff.invocations[1].arguments[1:3] == ("ruff", "check")
    assert next(g for g in invocations if g.gate == "frontend_build").invocations[0].arguments == (
        "build",
    )


def test_focused_targets_come_only_from_deterministic_plan():
    expected = plan(affected_backend_tests=("apps/api/tests/test_api.py",))
    backend = next(g for g in describe_invocations(expected) if g.gate == "backend_tests")
    assert backend.invocations[0].arguments == (
        "-m",
        "pytest",
        "tests/test_api.py",
        "--timeout=300",
    )
    full = next(
        g
        for g in describe_invocations(
            plan(boundary="publication", affected_backend_tests=("apps/api/tests/test_api.py",))
        )
        if g.gate == "backend_tests"
    )
    assert full.invocations[0].arguments == ("-m", "pytest", "tests", "--timeout=300")


@pytest.mark.parametrize(
    "field,value",
    [
        ("arguments", ("-c", "arbitrary python")),
        ("working_directory", "."),
        ("program", "python"),
    ],
)
def test_rehashed_forged_invocations_rejected(field, value):
    expected = plan(boundary="publication")
    groups = list(describe_invocations(expected))
    index = next(i for i, g in enumerate(groups) if g.gate == "frontend_build")
    group = groups[index]
    altered = group.invocations[0].model_copy(update={field: value})
    forged = group.model_copy(update={"invocations": (altered,)})
    forged = forged.model_copy(
        update={
            "invocation_digest": digest(
                forged.model_dump(mode="json", exclude={"invocation_digest"})
            )
        }
    )
    groups[index] = forged
    with pytest.raises(ValueError):
        verify_invocations(expected, groups)


def test_caller_claims_never_enable_execution_or_invoke_callbacks(monkeypatch):
    calls = []
    runner = DisabledValidationRunner()
    monkeypatch.setenv("JARVIS_VALIDATION_EXECUTION_ENABLED", "true")
    for invocation_parameters in [
        dict(),
        dict(approved=True, sandbox_verified=True),
        dict(launch=lambda *args: calls.append(args)),
        dict(plan=plan(), invocations=describe_invocations(plan())),
    ]:
        with pytest.raises(DomainError) as failure:
            runner.execute(**invocation_parameters)
        assert failure.value.code == "VALIDATION_EXECUTION_ISOLATION_UNAVAILABLE"
    assert not calls
    assert not runner.availability().enabled and not runner.availability().isolation_implemented
    with pytest.raises(ValueError):
        RunnerAvailability(gates=runner.availability().gates, enabled=True)


def test_invocation_iterator_is_bounded_before_candidate_materialization():
    expected = plan(boundary="publication")
    item = describe_invocations(expected)[0]
    consumed = []

    def unbounded_candidates():
        while True:
            consumed.append(1)
            yield item

    with pytest.raises(ValueError):
        verify_invocations(expected, unbounded_candidates())
    assert len(consumed) <= 10
