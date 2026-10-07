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


def file_change(**values):
    return FileChange(
        **values,
        before_mode="100644" if values["before_hash"] is not None else None,
        after_mode="100644" if values["after_hash"] is not None else None,
    )


def state(*paths, head="a", content="c"):
    return CodeState(
        repository_identity="github.com/example/jarvis",
        head_sha=head * 40,
        base_sha="b" * 40,
        changes=tuple(
            file_change(path=path, before_hash="d" * 64, after_hash=content * 64) for path in paths
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
    assert not assess_validation(
        code, published, evidence(focused), affected_backend_tests=("apps/api/tests/test_api.py",)
    ).ready


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
        changes=(*old.changes, file_change(path=path, before_hash=None, after_hash="f" * 64)),
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
            file_change(
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
            file_change(path="new.py", before_hash=None, after_hash="c" * 64),
            file_change(path="old.py", before_hash="d" * 64, after_hash=None),
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
    assert (
        "backend_tests"
        in assess_validation(
            second,
            after,
            evidence(before),
            affected_backend_tests=("apps/api/tests/test_persistence.py",),
        ).missing
    )


@pytest.mark.parametrize(
    "path",
    [
        "backups/jarvis-20260101T000000.sqlite3",
        "data/runtime.sqlite3-wal",
        "data/runtime.sqlite3-shm",
        "data/runtime.sqlite3-journal",
        "data/runtime.sqlite",
        "data/unknown-wal",
    ],
)
def test_all_sqlite_backups_and_sidecars_block_publication(path):
    code = state(path)
    plan = plan_validation(code, boundary="publication")
    assert plan.blocked_paths == (path,)
    assert not assess_validation(code, plan, evidence(plan)).ready


@pytest.mark.parametrize("mutation", ["targets", "browser"])
def test_self_rehashed_derivation_cannot_replace_trusted_policy(mutation):
    code = state("apps/api/app/main.py")
    trusted_targets = ("apps/api/tests/test_api.py",)
    trusted = plan_validation(code, affected_backend_tests=trusted_targets, browser_required=True)
    assert assess_validation(
        code,
        trusted,
        evidence(trusted),
        affected_backend_tests=trusted_targets,
        browser_required=True,
    ).ready
    replacement = plan_validation(
        code,
        affected_backend_tests=("apps/api/tests/test_diagnostics.py",)
        if mutation == "targets"
        else trusted_targets,
        browser_required=mutation != "browser",
    )
    with pytest.raises(ValueError):
        assess_validation(
            code,
            replacement,
            evidence(replacement),
            affected_backend_tests=trusted_targets,
            browser_required=True,
        )
    with pytest.raises(ValueError):
        assess_validation(code, trusted, evidence(trusted))


@pytest.mark.parametrize(
    "path",
    [
        "dist/bundle.js",
        "coverage/report.json",
        "apps/api/dist/package.whl",
        "apps/api/build/generated.py",
        "packages/example/coverage/report.json",
        "package.egg-info/PKG-INFO",
        "apps/api/__pycache__/cached.pyc",
    ],
)
def test_generated_outputs_are_blocked_everywhere(path):
    code = state(path)
    plan = plan_validation(code, boundary="publication")
    assert plan.blocked_paths == (path,)
    assert not assess_validation(code, plan, evidence(plan)).ready


def test_mode_only_change_is_exact_state_and_invalidates_scope_evidence():
    content_hash = "c" * 64
    old = CodeState(
        repository_identity="github.com/example/jarvis",
        head_sha="a" * 40,
        base_sha="b" * 40,
        changes=(
            FileChange(
                path="apps/api/app/main.py",
                before_hash=content_hash,
                after_hash=content_hash,
                before_mode="100755",
                after_mode="100644",
            ),
        ),
    )
    before = plan_validation(old)
    mode_change = FileChange(
        path="apps/api/app/main.py",
        before_hash=content_hash,
        after_hash=content_hash,
        before_mode="100644",
        after_mode="100755",
    )
    current = CodeState(**old.model_dump(exclude={"changes"}), changes=(mode_change,))
    after = plan_validation(current)
    assert mode_change.before_hash == mode_change.after_hash
    assert after.code_state_hash != before.code_state_hash
    assert "backend_tests" in assess_validation(current, after, evidence(before)).stale


def test_same_blob_symlink_transition_is_represented_but_blocked():
    content_hash = "c" * 64
    change = FileChange(
        path="apps/api/app/main.py",
        before_hash=content_hash,
        after_hash=content_hash,
        before_mode="100644",
        after_mode="120000",
    )
    code = CodeState(
        repository_identity="github.com/example/jarvis",
        head_sha="a" * 40,
        base_sha="b" * 40,
        changes=(change,),
    )
    plan = plan_validation(code, boundary="publication")
    assert plan.blocked_paths == (change.path,)
    assert not assess_validation(code, plan, evidence(plan)).ready


@pytest.mark.parametrize(
    "before_hash,before_mode,after_hash,after_mode",
    [
        (None, "100644", "c" * 64, "100644"),
        ("c" * 64, None, None, None),
        ("c" * 64, "100644", "c" * 64, "100644"),
    ],
)
def test_mode_presence_and_unchanged_pairs_fail_closed(
    before_hash, before_mode, after_hash, after_mode
):
    with pytest.raises(ValidationError):
        FileChange(
            path="example.py",
            before_hash=before_hash,
            before_mode=before_mode,
            after_hash=after_hash,
            after_mode=after_mode,
        )


@pytest.mark.parametrize(
    "path",
    [
        ".local/backend-ci/runtime/pytest.log",
        "apps/api/.local/production-acceptance/results.json",
        ".coverage",
        "reports/worker.pid",
        "reports/upload.partial",
        "jarvis-diagnostic.json",
        "jarvis-diagnostic.md",
        "model-qualification.json",
        "model-qualification-summary.md",
        "reports/profile-runtime.json",
        "apps/web/public/mockServiceWorker.js",
        "apps/web/vite.config.js",
        "apps/web/vite.config.d.ts",
        "apps/web/public/assets/office/office-8192x5460.png",
        "apps/web/public/assets/office/sprites/generated/agent-sheet-01.png",
        "apps/web/public/assets/office/sprites/generated/agent-sheet-06.png",
    ],
)
def test_repository_declared_generated_outputs_never_become_ready(path):
    code = state(path)
    plan = plan_validation(code, boundary="publication")
    assert plan.blocked_paths == (path,)
    assert not assess_validation(code, plan, evidence(plan)).ready


@pytest.mark.parametrize(
    "path",
    [
        ".gitignore",
        ".env.example",
        "apps/web/vite.config.ts",
        "apps/web/public/assets/office/sprites/generated/manifest.json",
        "docs/model-qualification.md",
    ],
)
def test_output_policy_keeps_legitimate_source_files_available(path):
    assert plan_validation(state(path), boundary="publication").blocked_paths == ()


def test_all_repository_ignore_rules_are_enforced():
    from pathlib import Path

    from app.development_validation.planner import IGNORE_PATTERNS, ignored_output

    repository = Path(__file__).resolve().parents[3]
    declared = tuple(
        line.strip().casefold()
        for line in (repository / ".gitignore").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    assert IGNORE_PATTERNS == declared
    for pattern in declared:
        sample = pattern.replace("*", "sample").strip("/")
        if pattern.endswith("/"):
            sample += "/artifact.json"
        assert ignored_output(sample), pattern
        assert plan_validation(state(sample), boundary="publication").blocked_paths == (sample,)


@pytest.mark.parametrize("rule", ["new-output/", "!dist/allowed.js", "**/output/", "bad\\rule"])
def test_changed_or_unsupported_trusted_output_policy_cannot_reuse_evidence(tmp_path, rule):
    import runpy
    from pathlib import Path

    from app.development_validation import planner

    copied = tmp_path / "apps/api/app/development_validation/planner.py"
    copied.parent.mkdir(parents=True)
    copied.write_text(Path(planner.__file__).read_text())
    original = runpy.run_path(str(copied))
    assert original["POLICY_DIGEST"] == planner.POLICY_DIGEST
    copied.write_text(
        copied.read_text().replace("IGNORE_PATTERNS = (", f"IGNORE_PATTERNS = (\n    {rule!r},")
    )
    if rule == "new-output/":
        changed = runpy.run_path(str(copied))
        assert changed["POLICY_DIGEST"] != original["POLICY_DIGEST"]
        assert changed["ignored_output"]("new-output/artifact.json")
        code = state("apps/api/app/main.py")
        previous = original["plan_validation"](code)
        current = changed["plan_validation"](code)
        assert not changed["assess_validation"](code, current, evidence(previous)).ready
    else:
        with pytest.raises(ValueError, match="output policy"):
            runpy.run_path(str(copied))
