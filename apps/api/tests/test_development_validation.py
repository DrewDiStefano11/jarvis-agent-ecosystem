"""Safety, exact-state invalidation and useful focused validation regressions."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.development_validation.planner import (
    PUBLICATION,
    assess_validation,
    digest,
    plan_validation,
)
from app.models.development_validation import (
    CodeState,
    FileChange,
    ValidationEvidence,
    ValidationPlan,
)


def state(*paths, head="a", content="c"):
    return CodeState(
        repository_identity="github.com/example/jarvis",
        head_sha=head * 40,
        base_sha="b" * 40,
        changes=tuple(
            FileChange(path=path, before_hash="d" * 64, after_hash=content * 64) for path in paths
        ),
    )


def evidence(plan, *, outcome="passed", failure_kind=None, age=0):
    return tuple(
        ValidationEvidence(
            command_run_id="command-" + gate.gate,
            gate=gate.gate,
            policy_digest=plan.policy_digest,
            code_state_hash=plan.code_state_hash,
            scope_fingerprint=gate.scope_fingerprint,
            targets=gate.targets,
            outcome=outcome,
            failure_kind=failure_kind,
            finished_at=datetime(2026, 10, 6, tzinfo=UTC) + timedelta(seconds=age),
        )
        for gate in plan.gates
    )


def test_backend_iteration_uses_explicit_affected_tests_but_publication_requires_all_gates():
    code = state("apps/api/app/services/tasks.py")
    focused = plan_validation(code, affected_backend_tests=("apps/api/tests/test_api.py",))
    assert {gate.gate for gate in focused.gates} == {
        "backend_ruff",
        "backend_tests",
        "repository_integrity",
    }
    assert next(g for g in focused.gates if g.gate == "backend_tests").targets == (
        "apps/api/tests/test_api.py",
    )
    published = plan_validation(
        code, boundary="publication", affected_backend_tests=("apps/api/tests/test_api.py",)
    )
    assert {g.gate for g in published.gates} == set(PUBLICATION)
    assert all(not g.targets for g in published.gates)
    assert not assess_validation(code, published, evidence(focused)).ready


def test_ui_source_automatically_requires_browser_acceptance():
    plan = plan_validation(state("apps/web/src/pages/Tasks.tsx"))
    assert {g.gate for g in plan.gates} == {
        "frontend_typecheck",
        "frontend_lint",
        "frontend_tests",
        "frontend_build",
        "browser_acceptance",
        "repository_integrity",
    }


def test_migrations_need_roundtrip_and_backend_validation():
    plan = plan_validation(state("apps/api/migrations/versions/example.py"))
    assert {g.gate for g in plan.gates} == {
        "migrations",
        "backend_ruff",
        "backend_tests",
        "repository_integrity",
    }


def test_documentation_iterations_are_small_but_publication_is_authoritative():
    code = state("docs/new-feature.md")
    assert {g.gate for g in plan_validation(code).gates} == {"repository_integrity"}
    assert {g.gate for g in plan_validation(code, boundary="publication").gates} == set(PUBLICATION)
    assert set(PUBLICATION) <= {g.gate for g in plan_validation(state("docs/executable.py")).gates}


@pytest.mark.parametrize(
    "path",
    [
        "scripts/check.py",
        ".github/workflows/ci.yml",
        "AGENTS.md",
        "tools/check.py",
        "fixtures/behavior.txt",
        "apps/api/pyproject.toml",
    ],
)
def test_policy_or_unknown_code_changes_cannot_reuse_old_component_pass(path):
    old = state("apps/web/tests/tasks.test.tsx")
    before = plan_validation(old, boundary="publication")
    current = CodeState(
        **old.model_dump(exclude={"changes"}),
        changes=(*old.changes, FileChange(path=path, before_hash=None, after_hash="f" * 64)),
    )
    after = plan_validation(current, boundary="publication")
    assert not assess_validation(current, after, evidence(before)).ready


def test_unrelated_backend_change_preserves_iteration_frontend_evidence_only():
    old = state("apps/web/tests/tasks.test.tsx")
    before = plan_validation(old)
    current = CodeState(
        **old.model_dump(exclude={"changes"}),
        changes=(
            *old.changes,
            FileChange(
                path="apps/api/app/services/tasks.py", before_hash=None, after_hash="f" * 64
            ),
        ),
    )
    after = plan_validation(current)
    assessment = assess_validation(current, after, evidence(before))
    assert "frontend_tests" not in assessment.stale + assessment.missing
    assert "repository_integrity" in assessment.stale
    assert "backend_tests" in assessment.missing


@pytest.mark.parametrize("mutation", ["head", "content", "repository"])
def test_changed_exact_code_state_invalidates_publication_evidence(mutation):
    old = state("apps/api/app/main.py")
    before = plan_validation(old, boundary="publication")
    current = state(
        "apps/api/app/main.py",
        head="e" if mutation == "head" else "a",
        content="f" if mutation == "content" else "c",
    )
    if mutation == "repository":
        current = current.model_copy(update={"repository_identity": "github.com/other/jarvis"})
    after = plan_validation(current, boundary="publication")
    assert not assess_validation(current, after, evidence(before)).ready


@pytest.mark.parametrize(
    "kind",
    [
        "product",
        "test",
        "infrastructure",
        "ci_environment",
        "watchdog",
        "dependency",
        "migration",
        "lint_type_build",
        "unknown",
    ],
)
def test_no_failure_category_is_silently_waived(kind):
    code = state("docs/report.md")
    plan = plan_validation(code)
    assessment = assess_validation(code, plan, evidence(plan, outcome="failed", failure_kind=kind))
    assert not assessment.ready and assessment.failed == ("repository_integrity",)


def test_later_failure_cancellation_and_ambiguous_time_override_passing_evidence():
    code = state("docs/report.md")
    plan = plan_validation(code)
    passed = evidence(plan)
    for outcome, kind in (("failed", "infrastructure"), ("cancelled", None), ("unmeasured", None)):
        assert not assess_validation(
            code, plan, passed + evidence(plan, outcome=outcome, failure_kind=kind, age=1)
        ).ready
    assert not assess_validation(
        code, plan, passed + evidence(plan, outcome="failed", failure_kind="test")
    ).ready
    assert assess_validation(
        code, plan, evidence(plan, outcome="failed", failure_kind="test") + evidence(plan, age=1)
    ).ready


@pytest.mark.parametrize(
    "path",
    [
        ".git/config",
        "apps/api/.env",
        "apps/api/.env.local",
        "apps/api/.venv/bin/python",
        "apps/web/dist/index.html",
        "apps/api/data/jarvis.db-wal",
        "credentials/private.pem",
    ],
)
def test_protected_or_generated_changes_block_even_when_tests_pass(path):
    code = state(path)
    plan = plan_validation(code, boundary="publication")
    assessment = assess_validation(code, plan, evidence(plan))
    assert not assessment.ready and assessment.blocked_paths == (path,)


def test_configuration_example_is_not_a_secret_file():
    assert not plan_validation(state(".env.example")).blocked_paths


@pytest.mark.parametrize(
    "path",
    [
        "../secret",
        "C:/secret",
        "a\\b.py",
        "/tmp/file",
        "a/../b.py",
        "a//b.py",
        "NUL.py",
        " a.py",
        "a.py ",
        "a.py\x00",
    ],
)
def test_paths_reject_escape_and_cross_platform_aliases(path):
    with pytest.raises(ValidationError):
        state(path)


def test_untracked_and_deleted_changes_and_case_collisions_are_explicit():
    code = CodeState(
        repository_identity="github.com/example/jarvis",
        head_sha="a" * 40,
        base_sha="b" * 40,
        changes=(
            FileChange(path="new.py", before_hash=None, after_hash="c" * 64),
            FileChange(path="old.py", before_hash="d" * 64, after_hash=None),
        ),
    )
    assert len(code.changes) == 2
    with pytest.raises(ValidationError):
        state("app.py", "APP.py")


def test_self_rehashed_plan_cannot_remove_mandatory_gates_or_protected_paths():
    code = state("apps/api/.env.local")
    plan = plan_validation(code, boundary="publication")
    for field, value in (("gates", [plan.gates[0].model_dump(mode="json")]), ("blocked_paths", [])):
        payload = plan.model_dump(mode="json", exclude={"plan_hash"}) | {field: value}
        forged = ValidationPlan(**payload, plan_hash=digest(payload))
        with pytest.raises(ValueError):
            assess_validation(code, forged, evidence(forged))


def test_evidence_bounds_and_model_success_claims_are_not_accepted():
    code = state("docs/report.md")
    plan = plan_validation(code)
    with pytest.raises(ValueError, match="bound"):
        assess_validation(code, plan, evidence(plan) * 513)
    payload = evidence(plan)[0].model_dump() | {"model_approved": True}
    with pytest.raises(ValidationError):
        ValidationEvidence(**payload)
    with pytest.raises(ValidationError):
        ValidationEvidence(**(evidence(plan)[0].model_dump() | {"outcome": "failed"}))


def test_order_independence_and_changed_targets_cannot_share_evidence():
    first = state("apps/api/app/main.py", "apps/api/tests/test_api.py")
    second = CodeState(
        **first.model_dump(exclude={"changes"}), changes=tuple(reversed(first.changes))
    )
    before = plan_validation(first, affected_backend_tests=("apps/api/tests/test_api.py",))
    assert before == plan_validation(second, affected_backend_tests=("apps/api/tests/test_api.py",))
    after = plan_validation(second, affected_backend_tests=("apps/api/tests/test_persistence.py",))
    assert "backend_tests" in assess_validation(second, after, evidence(before)).missing
