"""Safety, exact-state invalidation and useful focused validation regressions."""

import importlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from itertools import permutations

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pydantic import ValidationError
from sqlalchemy import func, select

from app.core.errors import DomainError
from app.db.models import AuditEventRow, OutboxEventRow
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
from app.models.source_change import (
    FileIdentity,
    PlanReferences,
    SourceChange,
    SourceChangeIntent,
    SourceContent,
    SourceEntry,
    SourceObservation,
    Topology,
    canonical_path,
)
from app.models.source_edit_planning import PersistEditingPlanRequest
from app.source_change.planner import (
    observation_identity,
    plan_source_changes,
    verify_source_plan,
)
from app.source_edit_planning import projection
from app.source_edit_planning.repository import SourceEditingPlanRow
from app.source_edit_planning.service import SourceEditingPlanService
from tests.test_self_build_workspaces import workspace as source_plan_workspace  # noqa: F401


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


# Pure source proposal regression coverage.


def content(text="original\n", mode="100644"):
    data = text.encode("utf-8")
    return SourceContent(
        text=text, digest=sha256(data).hexdigest(), byte_count=len(data), mode=mode
    )


def identity(value):
    return FileIdentity(**value.model_dump(exclude={"text"}))


def observation(*entries, **values):
    return SourceObservation(
        **(
            dict(
                repository_identity="github.com/example/jarvis",
                head_sha="a" * 40,
                base_sha="b" * 40,
                root=Topology(kind="directory"),
                entries=entries,
            )
            | values
        )
    )


def intent(observed, *changes, **values):
    return SourceChangeIntent(
        **(
            dict(
                source=observation_identity(observed),
                references=PlanReferences(
                    original_plan_hash="c" * 64, authorization_reference="auth-1"
                ),
                changes=changes,
            )
            | values
        )
    )


def change(path="hello.py", before=None, after=None, operation="modify"):
    return SourceChange(
        path=path,
        operation=operation,
        before=before,
        after=after,
    )


def fixture():
    old = content()
    observed = observation(SourceEntry(path="hello.py", kind="file", content=old))
    proposal = intent(observed, change(before=identity(old), after=content("replacement\n")))
    return observed, proposal


def refused(code, proposal, observed):
    with pytest.raises(DomainError) as failure:
        plan_source_changes(proposal, observed)
    assert failure.value.code == code
    assert "hello.py" not in str(failure.value)
    return failure.value


def test_add_modify_delete_and_mode_only_preview_are_exact():
    old = content()
    observed = observation(
        SourceEntry(path="add.py", kind="absent"),
        SourceEntry(path="delete.py", kind="file", content=old),
        SourceEntry(path="modify.py", kind="file", content=old),
        SourceEntry(path="mode.py", kind="file", content=old),
    )
    proposal = intent(
        observed,
        change("add.py", after=content(""), operation="add"),
        change("delete.py", before=identity(old), operation="delete"),
        change("modify.py", before=identity(old), after=content("new\n")),
        change("mode.py", before=identity(old), after=content(mode="100755")),
    )
    plan = plan_source_changes(proposal, observed)
    assert not plan.mutation_supported
    assert verify_source_plan(plan, observed) == plan
    assert [item.operation for item in plan.preview] == ["add", "delete", "modify", "modify"]
    assert plan.preview[2].before.mode == "100644"
    assert plan.preview[2].after.mode == "100755"
    assert "text" not in str(plan.preview)
    assert plan.references == proposal.references


def test_all_permutations_have_identical_canonical_plans():
    entries = tuple(SourceEntry(path=f"file-{index}.py", kind="absent") for index in range(3))
    changes = tuple(
        change(item.path, after=content(str(index)), operation="add")
        for index, item in enumerate(entries)
    )
    plans = []
    for permuted_entries in permutations(entries):
        observed = observation(*permuted_entries)
        for permuted_changes in permutations(changes):
            plans.append(plan_source_changes(intent(observed, *permuted_changes), observed))
    assert all(plan == plans[0] for plan in plans)


@pytest.mark.parametrize(
    "path",
    [
        "../hello.py",
        "/hello.py",
        "C:/hello.py",
        "C:hello.py",
        "//host/share/file",
        "a\\b",
        "a//b",
        "a/./b",
        "a/../b",
        "file:stream",
        "file.",
        "file ",
        " a/b",
        "a/ b",
        "a/NUL",
        "a/CON.txt",
        "a/COM1.py",
        "a/LPT9",
        "a/COM¹",
        "a/LPT².py",
        "a/CONIN$",
        "a/CONOUT$",
        "a/FOO~1/bar",
        "a/<b",
        "a/b?",
        "a/*",
        "a/b|",
        'a/b"',
        "a/\x00b",
        "a/\x7fb",
        "a/\u202eb",
        "a/\u200bb",
        "a/e\u0301.py",
        "",
        "a" * 241,
    ],
)
def test_noncanonical_windows_and_traversal_paths_are_refused(path):
    with pytest.raises((ValueError, DomainError)):
        canonical_path(path)


@pytest.mark.parametrize(
    "path",
    [
        ".git/config",
        ".AWS/config",
        ".codex/config",
        ".ssh/id",
        ".env",
        ".env.local",
        "credentials.json",
        "a/.secrets.json",
        "a/id_ed25519",
        "a/key.pem",
        "a/key.key",
        "a/state.db",
        "a/state.sqlite3",
        "a/state.db-wal",
        "a/state.db-shm",
    ],
)
def test_credential_and_runtime_authority_paths_are_denied(path):
    with pytest.raises(DomainError) as failure:
        change(path, after=content(), operation="add")
    assert failure.value.code == "TOOL_PATH_DENIED"
    assert path not in str(failure.value)


@pytest.mark.parametrize(
    "paths",
    [
        ("A.py", "a.py"),
        ("Foo/a.py", "foo/b.py"),
        ("ß/a.py", "ss/b.py"),
        ("same.py", "same.py"),
    ],
)
def test_leaf_and_directory_case_collisions_are_platform_independent(paths):
    changes = tuple(change(path, after=content(), operation="add") for path in paths)
    with pytest.raises(ValidationError, match="SOURCE_PATH_COLLISION"):
        intent(observation(), *changes)
    with pytest.raises(ValidationError, match="SOURCE_PATH_COLLISION"):
        observation(*(SourceEntry(path=path, kind="absent") for path in paths))


def test_file_ancestor_conflicts_are_rejected_before_planning():
    with pytest.raises(ValidationError, match="SOURCE_PATH_CONFLICT"):
        intent(
            observation(),
            change("foo", after=content(), operation="add"),
            change("foo/bar", after=content(), operation="add"),
        )


def test_alias_against_unchanged_sibling_or_parent_refuses():
    observed = observation(SourceEntry(path="Hello.py", kind="file"))
    refused(
        "SOURCE_PATH_COLLISION",
        intent(observed, change("hello.py", after=content(), operation="add")),
        observed,
    )
    observed = observation(
        SourceEntry(path="Dir", kind="directory"), SourceEntry(path="Dir/a.py", kind="absent")
    )
    refused(
        "SOURCE_PATH_COLLISION",
        intent(observed, change("dir/a.py", after=content(), operation="add")),
        observed,
    )


@pytest.mark.parametrize(
    "root",
    [
        Topology(kind="symlink"),
        Topology(kind="directory", reparse_point=True),
        Topology(kind="file"),
        Topology(kind="absent"),
        Topology(kind="special"),
    ],
)
def test_root_and_root_ancestor_escape_refuses(root):
    observed, proposal = fixture()
    observed = observed.model_copy(update={"root": root})
    proposal = proposal.model_copy(update={"source": observation_identity(observed)})
    refused("SOURCE_ROOT_UNSAFE", proposal, observed)


@pytest.mark.parametrize(
    "kind,reparse",
    [
        ("symlink", False),
        ("directory", True),
        ("file", False),
        ("absent", False),
        ("special", False),
    ],
)
def test_parent_symlink_junction_reparse_and_nondirectory_refuses(kind, reparse):
    observed = observation(
        SourceEntry(path="dir", kind=kind, reparse_point=reparse),
        SourceEntry(path="dir/hello.py", kind="absent"),
    )
    refused(
        "SOURCE_PARENT_UNSAFE",
        intent(observed, change("dir/hello.py", after=content(), operation="add")),
        observed,
    )


@pytest.mark.parametrize(
    "kind,reparse,links",
    [
        ("symlink", False, 1),
        ("file", True, 1),
        ("special", False, 1),
        ("file", False, 2),
        ("directory", False, 1),
        ("file", False, 0),
    ],
)
def test_leaf_links_special_files_and_hardlinks_refuse(kind, reparse, links):
    observed = observation(
        SourceEntry(path="hello.py", kind=kind, reparse_point=reparse, link_count=links)
    )
    refused(
        "SOURCE_PATH_UNSAFE",
        intent(observed, change(before=identity(content()), after=content("new"))),
        observed,
    )


@pytest.mark.parametrize("operation", ["add", "modify", "delete"])
def test_incomplete_leaf_evidence_never_means_absent(operation):
    observed = observation()
    before = identity(content()) if operation != "add" else None
    after = content("new") if operation != "delete" else None
    refused(
        "SOURCE_OBSERVATION_INCOMPLETE",
        intent(observed, change(before=before, after=after, operation=operation)),
        observed,
    )


def test_missing_parent_evidence_and_missing_content_refuse():
    observed = observation(SourceEntry(path="dir/hello.py", kind="absent"))
    refused(
        "SOURCE_OBSERVATION_INCOMPLETE",
        intent(observed, change("dir/hello.py", after=content(), operation="add")),
        observed,
    )
    observed = observation(SourceEntry(path="hello.py", kind="file"))
    refused(
        "SOURCE_OBSERVATION_INCOMPLETE",
        intent(observed, change(before=identity(content()), after=content("new"))),
        observed,
    )


def test_safe_nested_add_requires_existing_directories_and_absent_leaf():
    observed = observation(
        SourceEntry(path="dir", kind="directory"),
        SourceEntry(path="dir/sub", kind="directory"),
        SourceEntry(path="dir/sub/new.py", kind="absent"),
        SourceEntry(path=".git", kind="directory"),
    )
    plan = plan_source_changes(
        intent(observed, change("dir/sub/new.py", after=content(), operation="add")), observed
    )
    assert plan.changes[0].path == "dir/sub/new.py"


def test_add_existing_and_modify_delete_missing_are_distinct_conflicts():
    observed, _ = fixture()
    refused(
        "SOURCE_ALREADY_EXISTS",
        intent(observed, change(after=content(), operation="add")),
        observed,
    )
    observed = observation(SourceEntry(path="hello.py", kind="absent"))
    for operation in ("modify", "delete"):
        refused(
            "SOURCE_NOT_FOUND",
            intent(
                observed,
                change(
                    before=identity(content()),
                    after=content("new") if operation == "modify" else None,
                    operation=operation,
                ),
            ),
            observed,
        )


@pytest.mark.parametrize(
    "field,value", [("digest", "d" * 64), ("byte_count", 1), ("mode", "100755")]
)
def test_exact_before_digest_size_and_mode_conflicts(field, value):
    observed, proposal = fixture()
    changed = proposal.changes[0].model_copy(
        update={
            "before": proposal.changes[0].before.model_copy(update={field: value}),
        }
    )
    refused("SOURCE_BEFORE_CONFLICT", proposal.model_copy(update={"changes": (changed,)}), observed)


@pytest.mark.parametrize(
    "field,value",
    [
        ("repository_identity", "github.com/other/jarvis"),
        ("head_sha", "d" * 40),
        ("base_sha", "d" * 40),
        ("observation_digest", "d" * 64),
    ],
)
def test_exact_source_identity_conflicts(field, value):
    observed, proposal = fixture()
    proposal = proposal.model_copy(
        update={"source": proposal.source.model_copy(update={field: value})}
    )
    refused("SOURCE_IDENTITY_CONFLICT", proposal, observed)


@pytest.mark.parametrize(
    "path", ["dist/output.py", "a/__pycache__/out.py", "build/a.py", "output.pyc"]
)
def test_validation_policy_denies_generated_outputs(path):
    observed = observation(
        SourceEntry(path=path, kind="absent"),
        *(
            [SourceEntry(path=path.rsplit("/", 1)[0], kind="directory")]
            if "/" in path and path.count("/") == 1
            else [
                SourceEntry(path="a", kind="directory"),
                SourceEntry(path="a/__pycache__", kind="directory"),
            ]
            if path.count("/") == 2
            else []
        ),
    )
    refused(
        "SOURCE_PATH_DENIED",
        intent(observed, change(path, after=content(), operation="add")),
        observed,
    )


@pytest.mark.parametrize(
    "before,after,operation",
    [
        (None, None, "add"),
        (identity(content()), content("new"), "add"),
        (None, content(), "modify"),
        (identity(content()), None, "modify"),
        (None, None, "delete"),
        (identity(content()), content(), "delete"),
        (identity(content()), content(), "modify"),
    ],
)
def test_operation_shapes_and_noop_are_refused(before, after, operation):
    with pytest.raises(ValidationError):
        change(before=before, after=after, operation=operation)


@pytest.mark.parametrize("text", ["nul\x00value", "\ud800"])
def test_invalid_utf8_and_binary_source_are_refused(text):
    with pytest.raises(ValidationError):
        SourceContent(text=text, digest="a" * 64, byte_count=1, mode="100644")


@pytest.mark.parametrize(
    "update",
    [
        {"digest": "a" * 64},
        {"byte_count": 0},
        {"byte_count": True},
        {"byte_count": "9"},
        {"mode": "120000"},
        {"mode": "160000"},
        {"mode": "100600"},
        {"surprise": "authority"},
    ],
)
def test_content_digests_strict_counts_modes_and_extra_fields(update):
    with pytest.raises(ValidationError):
        SourceContent.model_validate(content().model_dump() | update)


def test_unicode_bytes_crlf_and_final_newline_affect_fingerprints():
    variants = ["é\n", "é\r\n", "é", "e\n"]
    assert len({content(text).digest for text in variants}) == len(variants)
    assert content("é").byte_count == 2


def test_individual_file_byte_limit_is_not_a_character_limit():
    assert content("x" * 65536).byte_count == 65536
    with pytest.raises(ValidationError):
        content("x" * 65537)
    with pytest.raises(ValidationError):
        content("é" * 32769)


def test_file_count_and_aggregate_before_after_limits():
    observed = observation()
    changes = tuple(change(f"file-{i}.py", after=content(), operation="add") for i in range(64))
    assert len(intent(observed, *changes).changes) == 64
    with pytest.raises(ValidationError):
        intent(observed, *changes, change("too-many.py", after=content(), operation="add"))
    with pytest.raises(ValidationError):
        intent(observed)
    large = content("x" * 65536)
    edits = tuple(
        change(f"large-{i}.py", before=identity(large), after=content("y" * 65536))
        for i in range(8)
    )
    assert len(intent(observed, *edits).changes) == 8
    with pytest.raises(ValidationError, match="SOURCE_TOTAL_LIMIT"):
        intent(observed, *edits, change("extra.py", after=content("z"), operation="add"))


def test_observation_entry_and_content_limits():
    entries = tuple(SourceEntry(path=f"file-{i}.py", kind="file") for i in range(4096))
    assert len(observation(*entries).entries) == 4096
    with pytest.raises(ValidationError):
        observation(*entries, SourceEntry(path="extra.py", kind="file"))
    large = content("x" * 65536)
    entries = tuple(SourceEntry(path=f"file-{i}.py", kind="file", content=large) for i in range(16))
    assert observation(*entries)
    with pytest.raises(ValidationError, match="SOURCE_TOTAL_LIMIT"):
        observation(*entries, SourceEntry(path="extra.py", kind="file", content=content("x")))


@pytest.mark.parametrize(
    "field,value",
    [
        ("plan_hash", "d" * 64),
        ("policy_digest", "d" * 64),
        ("change_fingerprint", "d" * 64),
        ("preview", ()),
        ("mutation_supported", True),
        (
            "references",
            PlanReferences(original_plan_hash="d" * 64, authorization_reference="auth-2"),
        ),
    ],
)
def test_entire_plan_and_original_authorization_references_are_immutable(field, value):
    observed, proposal = fixture()
    plan = plan_source_changes(proposal, observed)
    with pytest.raises(ValidationError):
        setattr(plan, field, value)
    with pytest.raises(DomainError) as failure:
        verify_source_plan(plan.model_copy(update={field: value}), observed)
    assert failure.value.code == "SOURCE_PLAN_CHANGED"


def test_tampered_constructed_and_copied_content_revalidated_with_sanitized_failure():
    observed, proposal = fixture()
    bad_content = content().model_copy(update={"text": "secret-private-input"})
    bad_change = proposal.changes[0].model_copy(update={"after": bad_content})
    failure = refused(
        "SOURCE_CONTRACT_INVALID", proposal.model_copy(update={"changes": (bad_change,)}), observed
    )
    assert "secret-private-input" not in str(failure)
    assert failure.__cause__ is None


def test_public_preview_repr_and_failures_do_not_expose_source_secrets():
    secret = "private arbitrary secret with no recognisable token syntax"
    old = content(secret)
    observed = observation(SourceEntry(path="hello.py", kind="file", content=old))
    plan = plan_source_changes(
        intent(observed, change(before=identity(old), after=content(secret + "new"))), observed
    )
    assert secret not in repr(plan)
    assert secret not in str(plan.preview)
    assert "hello.py" not in str(plan.preview)
    # Exact source is retained only in the protected original proposal.
    assert plan.changes[0].after.text == secret + "new"


def test_copied_invalid_types_cannot_emit_serializer_warnings_with_secret_inputs():
    import warnings

    observed, proposal = fixture()
    secret = "private-input-that-must-not-appear-in-a-warning"
    bad_change = proposal.changes[0].model_copy(update={"operation": secret})
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        failure = refused(
            "SOURCE_CONTRACT_INVALID",
            proposal.model_copy(update={"changes": (bad_change,)}),
            observed,
        )
    assert not captured
    assert secret not in str(failure)


def test_pure_planning_never_touches_filesystem_or_environment(monkeypatch):
    observed, proposal = fixture()

    def forbidden(*args, **kwargs):
        raise AssertionError("planner attempted filesystem access")

    import builtins
    import os
    from pathlib import Path

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(Path, "lstat", forbidden)
    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(os, "open", forbidden)
    monkeypatch.setattr(os, "environ", {})
    assert verify_source_plan(plan_source_changes(proposal, observed), observed)


# Durable source edit planning regressions.


def source_projection_state(observed, *changes):
    return CodeState(
        repository_identity=observed.repository_identity,
        head_sha=observed.head_sha,
        base_sha=observed.base_sha,
        changes=changes,
    )


def delta(path, before, after):
    return FileChange(
        path=path,
        before_hash=before.digest if before else None,
        before_mode=before.mode if before else None,
        after_hash=after.digest if after else None,
        after_mode=after.mode if after else None,
    )


@pytest.mark.parametrize(
    "original_operation,new_operation",
    [
        ("add", "modify"),
        ("add", "delete"),
        ("modify", "modify"),
        ("modify", "delete"),
        ("delete", "add"),
    ],
)
def test_projects_existing_deltas_without_losing_unrelated_changes(
    original_operation, new_operation
):
    baseline = None if original_operation == "add" else content("base")
    current = None if original_operation == "delete" else content("current")
    replacement = None if new_operation == "delete" else content("candidate")
    observed = observation(
        SourceEntry(path="a.py", kind="file" if current else "absent", content=current)
    )
    original = source_projection_state(
        observed,
        delta("a.py", baseline, current),
        delta("unrelated.py", content("old"), content("unrelated")),
    )
    proposal = intent(
        observed,
        change(
            "a.py",
            operation=new_operation,
            before=identity(current) if current else None,
            after=replacement,
        ),
    )
    source_plan = plan_source_changes(proposal, observed)
    expected, validation = projection.project_source_state(source_plan, original)
    assert original.changes[-1] in expected.changes
    matching = [entry for entry in expected.changes if entry.path == "a.py"]
    if baseline is None and replacement is None:
        assert not matching
    else:
        assert matching == [delta("a.py", baseline, replacement)]
    assert {gate.gate for gate in validation.gates} >= set(PUBLICATION)
    assert validation.boundary == "publication"


@pytest.mark.parametrize("operation", ["modify", "delete"])
def test_full_revert_removes_existing_delta(operation):
    baseline = content("baseline")
    current = content("current") if operation == "modify" else None
    observed = observation(
        SourceEntry(path="a.py", kind="file" if current else "absent", content=current)
    )
    original = source_projection_state(observed, delta("a.py", baseline, current))
    proposal = intent(
        observed,
        change(
            "a.py",
            operation="modify" if current else "add",
            before=identity(current) if current else None,
            after=baseline,
        ),
    )
    expected, validation = projection.project_source_state(
        plan_source_changes(proposal, observed), original
    )
    assert not expected.changes
    assert {gate.gate for gate in validation.gates} == set(PUBLICATION)


def test_projection_rejects_stale_existing_delta_identity():
    current = content("current")
    observed = observation(SourceEntry(path="a.py", kind="file", content=current))
    original = source_projection_state(observed, delta("a.py", content("base"), content("stale")))
    source_plan = plan_source_changes(
        intent(observed, change("a.py", before=identity(current), after=content("new"))),
        observed,
    )
    with pytest.raises(DomainError) as failure:
        projection.project_source_state(source_plan, original)
    assert failure.value.code == "SOURCE_VALIDATION_BEFORE_CONFLICT"


def test_unrelated_unsafe_existing_deltas_are_not_hidden():
    observed = observation(SourceEntry(path="a.py", kind="absent"))
    original = source_projection_state(observed, delta("dist/generated.py", None, content("build")))
    source_plan = plan_source_changes(
        intent(observed, change("a.py", operation="add", after=content("new"))),
        observed,
    )
    with pytest.raises(DomainError) as failure:
        projection.project_source_state(source_plan, original)
    assert failure.value.code == "SOURCE_VALIDATION_PATH_DENIED"


migration = importlib.import_module("migrations.versions.20261012_source_edit_plans")


@pytest.fixture
def source_planning_prepared(source_plan_workspace):  # noqa: F811
    app, actor, workspace_service, workspace_request, _, _ = source_plan_workspace
    reservation = workspace_service.reserve(actor, workspace_request)
    observed = observation(
        SourceEntry(path="source.py", kind="absent"),
        base_sha=workspace_request.base_sha,
    )
    source_intent = intent(
        observed,
        change("source.py", operation="add", after=content("unpublished_code_payload\n")),
        references=PlanReferences(
            original_plan_hash=reservation.plan.plan_hash,
            authorization_reference=reservation.approval_id,
        ),
    )
    request = PersistEditingPlanRequest(
        workspace_id=reservation.workspace_id,
        expected_workspace_version=reservation.version,
        expected_workspace_plan_hash=reservation.plan.plan_hash,
        command_id="source-command",
        source_intent=source_intent,
    )
    original = CodeState(
        repository_identity=observed.repository_identity,
        head_sha=observed.head_sha,
        base_sha=observed.base_sha,
        changes=(),
    )
    yield app, actor, SourceEditingPlanService(app), request, observed, original


def count_events(app):
    with app.state.repository.session_factory() as session:
        audits = list(
            session.scalars(
                select(AuditEventRow).where(
                    AuditEventRow.event_type == "self_build.source_edit_plan.persisted"
                )
            )
        )
        outbox = list(
            session.scalars(
                select(OutboxEventRow).where(
                    OutboxEventRow.event_type == "self_build.source_edit_plan.persisted"
                )
            )
        )
        rows = session.scalar(select(func.count()).select_from(SourceEditingPlanRow))
        return rows, audits, outbox


def test_source_planning_prepared_exact_plan_persistence_replay_and_protected_read(
    source_planning_prepared,
):
    app, actor, service, request, observed, original = source_planning_prepared
    result = service.persist(actor, request, observed, original)
    assert service.persist(actor, request, observed, original) == result
    assert service.read(actor, result.record_id) == result
    assert result.plan.state == "proposed"
    assert result.plan.evidence_status == "unmeasured"
    assert result.plan.mutation_supported is False
    rows, audit, outbox = count_events(app)
    assert rows == len(audit) == len(outbox) == 1
    assert "unpublished_code_payload" not in str(audit[0].payload)
    assert "source.py" not in str(audit[0].payload)


def test_source_planning_prepared_concurrent_command_is_one_immutable_record(
    source_planning_prepared,
):
    app, actor, service, request, observed, original = source_planning_prepared
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(lambda _: service.persist(actor, request, observed, original), range(2))
        )
    assert results[0] == results[1]
    rows, audit, outbox = count_events(app)
    assert rows == len(audit) == len(outbox) == 1


def test_source_planning_prepared_command_content_conflict_never_replaces_plan(
    source_planning_prepared,
):
    app, actor, service, request, observed, original = source_planning_prepared
    result = service.persist(actor, request, observed, original)
    altered = request.model_copy(
        update={
            "source_intent": request.source_intent.model_copy(
                update={
                    "changes": (change("source.py", operation="add", after=content("different")),),
                }
            )
        }
    )
    with pytest.raises(DomainError) as failure:
        service.persist(actor, altered, observed, original)
    assert failure.value.code == "SOURCE_EDIT_COMMAND_CONFLICT"
    assert service.read(actor, result.record_id) == result
    assert count_events(app)[0] == 1


def test_source_planning_prepared_event_failure_rolls_back_record_and_outbox(
    source_planning_prepared, monkeypatch
):
    app, actor, service, request, observed, original = source_planning_prepared
    original_add = service.repository.leases._add_event

    def fail_after_event(*args, **kwargs):
        original_add(*args, **kwargs)
        raise RuntimeError("injected rollback")

    monkeypatch.setattr(service.repository.leases, "_add_event", fail_after_event)
    with pytest.raises(RuntimeError, match="injected rollback"):
        service.persist(actor, request, observed, original)
    rows, audit, outbox = count_events(app)
    assert rows == len(audit) == len(outbox) == 0


@pytest.mark.parametrize("boundary", ["disabled", "stop", "lease", "task", "workspace"])
def test_live_authority_loss_prevents_source_plan_persistence(source_planning_prepared, boundary):
    from datetime import UTC, datetime, timedelta

    from app.db.models import (
        DevelopmentWorkspaceRow,
        SystemStateRow,
        TaskLeaseRow,
        TaskRow,
    )

    app, actor, service, request, observed, original = source_planning_prepared
    if boundary == "disabled":
        app.state.settings.self_build_enabled = False
    else:
        with app.state.repository.session_factory() as session:
            if boundary == "stop":
                session.get(SystemStateRow, 1).emergency_stop = True
            elif boundary == "lease":
                session.get(TaskLeaseRow, "task-demo").expires_at = datetime.now(UTC) - timedelta(
                    seconds=1
                )
            elif boundary == "task":
                session.get(TaskRow, "task-demo").status = "cancelled"
            else:
                session.get(DevelopmentWorkspaceRow, request.workspace_id).state = "abandoned"
            session.commit()
    with pytest.raises(DomainError):
        service.persist(actor, request, observed, original)
    rows, audit, outbox = count_events(app)
    assert rows == len(audit) == len(outbox) == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("expected_workspace_version", 2),
        ("expected_workspace_plan_hash", "d" * 64),
    ],
)
def test_exact_workspace_provenance_is_required(source_planning_prepared, field, value):
    app, actor, service, request, observed, original = source_planning_prepared
    with pytest.raises(DomainError) as failure:
        service.persist(actor, request.model_copy(update={field: value}), observed, original)
    assert failure.value.code == "SOURCE_EDIT_PROVENANCE_CONFLICT"
    assert count_events(app)[0] == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("original_plan_hash", "d" * 64),
        ("authorization_reference", "foreign-approval"),
    ],
)
def test_model_references_cannot_replace_durable_original_authorization(
    source_planning_prepared, field, value
):
    app, actor, service, request, observed, original = source_planning_prepared
    references = request.source_intent.references.model_copy(update={field: value})
    altered = request.model_copy(
        update={
            "source_intent": request.source_intent.model_copy(update={"references": references})
        }
    )
    with pytest.raises(DomainError) as failure:
        service.persist(actor, altered, observed, original)
    assert failure.value.code == "SOURCE_EDIT_PROVENANCE_CONFLICT"
    assert count_events(app)[0] == 0


def test_source_payload_in_database_exception_is_sanitized(source_planning_prepared, monkeypatch):
    import traceback

    from sqlalchemy.exc import OperationalError

    app, actor, service, request, observed, original = source_planning_prepared
    secret = "private-source-payload-in-a-native-db-error"

    def fail_storage(*args, **kwargs):
        raise OperationalError("insert source plan", {"payload": secret}, RuntimeError(secret))

    monkeypatch.setattr(service.repository.leases, "_add_event", fail_storage)
    with pytest.raises(DomainError) as failure:
        service.persist(actor, request, observed, original)
    assert failure.value.code == "SOURCE_EDIT_STORAGE_FAILED"
    assert secret not in "".join(traceback.format_exception(failure.value))
    assert count_events(app)[0] == 0


def test_immutable_source_plan_history_blocks_downgrade(source_planning_prepared):
    app, actor, service, request, observed, original = source_planning_prepared
    result = service.persist(actor, request, observed, original)
    with pytest.raises(RuntimeError, match="Export source edit plan evidence"):
        with (
            app.state.engine.begin() as connection,
            Operations.context(MigrationContext.configure(connection)),
        ):
            migration.downgrade()
    assert service.read(actor, result.record_id) == result


def test_restart_style_fresh_service_reads_immutable_record(source_planning_prepared):
    app, actor, service, request, observed, original = source_planning_prepared
    result = service.persist(actor, request, observed, original)
    app.state.engine.dispose()
    fresh = SourceEditingPlanService(app)
    assert fresh.read(actor, result.record_id) == result
    assert fresh.persist(actor, request, observed, original) == result
    assert count_events(app)[0] == 1


def test_task_read_permission_alone_does_not_reveal_protected_source(
    source_planning_prepared, monkeypatch
):
    from types import SimpleNamespace

    app, actor, service, request, observed, original = source_planning_prepared
    result = service.persist(actor, request, observed, original)
    original_check = service.workspace.identity.check_permission_resource_access

    def deny_source(actor_id, permission_key, *args, **kwargs):
        if permission_key == "self_build.workspace":
            return SimpleNamespace(allowed=False)
        return original_check(actor_id, permission_key, *args, **kwargs)

    monkeypatch.setattr(service.workspace.identity, "check_permission_resource_access", deny_source)
    with pytest.raises(DomainError) as failure:
        service.read(actor, result.record_id)
    assert failure.value.code == "SOURCE_EDIT_PERMISSION_DENIED"


def test_rehashed_record_cannot_drop_required_publication_gates(source_planning_prepared):
    from app.development_validation.planner import digest

    app, actor, service, request, observed, original = source_planning_prepared
    result = service.persist(actor, request, observed, original)
    with app.state.repository.session_factory() as session:
        row = session.get(SourceEditingPlanRow, result.record_id)
        payload = dict(row.payload)
        plan = dict(payload["plan"])
        validation = dict(plan["validation_plan"])
        validation["gates"] = validation["gates"][:1]
        validation["plan_hash"] = digest(
            {key: value for key, value in validation.items() if key != "plan_hash"}
        )
        plan["validation_plan"] = validation
        plan["plan_hash"] = digest(
            {key: value for key, value in plan.items() if key != "plan_hash"}
        )
        payload["plan"] = plan
        row.payload = payload
        row.plan_hash = plan["plan_hash"]
        session.commit()
    with pytest.raises(DomainError) as failure:
        service.read(actor, result.record_id)
    assert failure.value.code == "SOURCE_EDIT_RECORD_INVALID"


@pytest.mark.parametrize(
    "field,foreign_value",
    [
        ("repository_identity", "github.com/foreign/project"),
        ("base_sha", "d" * 40),
    ],
)
def test_coherently_rehashed_foreign_source_is_not_owned_workspace_evidence(
    source_planning_prepared, field, foreign_value
):
    from app.development_validation.planner import digest
    from app.models.source_change import SourceChangeIntent, SourceObservation
    from app.source_change.planner import observation_identity, plan_source_changes
    from app.source_edit_planning.projection import project_source_state

    app, actor, service, request, observed, original = source_planning_prepared
    result = service.persist(actor, request, observed, original)
    foreign_observation = SourceObservation.model_validate(
        observed.model_dump(mode="json") | {field: foreign_value},
    )
    new_intent = SourceChangeIntent.model_validate(
        request.source_intent.model_dump(mode="json")
        | {"source": observation_identity(foreign_observation).model_dump(mode="json")},
    )
    source_plan = plan_source_changes(new_intent, foreign_observation)
    foreign_original = CodeState.model_validate(
        original.model_dump(mode="json") | {field: foreign_value}
    )
    projected, validation = project_source_state(source_plan, foreign_original)
    payload = result.plan.model_dump(mode="json", exclude={"plan_hash"}) | {
        "source_plan": source_plan.model_dump(mode="json"),
        "observation": foreign_observation.model_dump(mode="json"),
        "original_code_state": foreign_original.model_dump(mode="json"),
        "expected_code_state": projected.model_dump(mode="json"),
        "validation_plan": validation.model_dump(mode="json"),
    }
    new_request = request.model_copy(update={"source_intent": new_intent})
    request_hash = digest(
        {
            "request": new_request.model_dump(mode="json"),
            "source_plan_hash": source_plan.plan_hash,
            "original_code_state": foreign_original.model_dump(mode="json"),
        }
    )
    record_payload = result.model_dump(mode="json") | {
        "request_hash": request_hash,
        "plan": payload | {"plan_hash": digest(payload)},
    }
    with app.state.repository.session_factory() as session:
        row = session.get(SourceEditingPlanRow, result.record_id)
        row.payload = record_payload
        row.request_hash = request_hash
        row.plan_hash = digest(payload)
        session.commit()
    with pytest.raises(DomainError) as failure:
        service.read(actor, result.record_id)
    assert failure.value.code == "SOURCE_EDIT_RECORD_INVALID"


def test_per_workspace_evidence_bound_preserves_exact_replay(source_planning_prepared, monkeypatch):
    from app.source_edit_planning import service as service_module

    app, actor, service, request, observed, original = source_planning_prepared
    monkeypatch.setattr(service_module, "MAX_PLANS_PER_WORKSPACE", 2)
    first = service.persist(actor, request, observed, original)
    service.persist(actor, request.model_copy(update={"command_id": "second"}), observed, original)
    assert service.persist(actor, request, observed, original) == first
    with pytest.raises(DomainError) as failure:
        service.persist(
            actor,
            request.model_copy(update={"command_id": "overflow"}),
            observed,
            original,
        )
    assert failure.value.code == "SOURCE_EDIT_PLAN_LIMIT"
    assert count_events(app)[0] == 2


def test_serialized_record_limit_refuses_without_event(source_planning_prepared, monkeypatch):
    from app.source_edit_planning import service as service_module

    app, actor, service, request, observed, original = source_planning_prepared
    monkeypatch.setattr(service_module, "MAX_RECORD_BYTES", 1)
    with pytest.raises(DomainError) as failure:
        service.persist(actor, request, observed, original)
    assert failure.value.code == "SOURCE_EDIT_RECORD_LIMIT"
    rows, audit, outbox = count_events(app)
    assert rows == len(audit) == len(outbox) == 0


def test_proposal_read_survives_stop_and_workspace_abandonment(source_planning_prepared):
    from app.db.models import DevelopmentWorkspaceRow, SystemStateRow

    app, actor, service, request, observed, original = source_planning_prepared
    result = service.persist(actor, request, observed, original)
    with app.state.repository.session_factory() as session:
        session.get(SystemStateRow, 1).emergency_stop = True
        workspace_row = session.get(DevelopmentWorkspaceRow, request.workspace_id)
        workspace_row.state = "abandoned"
        workspace_row.version += 1
        session.commit()
    assert service.read(actor, result.record_id) == result
    with pytest.raises(DomainError):
        service.persist(actor, request, observed, original)


def test_real_control_plane_restart_keeps_proposal_and_authorization(source_planning_prepared):
    from app.main import create_app

    app, actor, service, request, observed, original = source_planning_prepared
    result = service.persist(actor, request, observed, original)
    database_url = str(app.state.engine.url)
    app.state.engine.dispose()
    restarted = create_app(database_url=database_url)
    try:
        current_actor = restarted.state.agent_runtime_service.authenticate_actor(actor.actor_id)
        fresh_service = SourceEditingPlanService(restarted)
        assert fresh_service.read(current_actor, result.record_id) == result
    finally:
        restarted.state.engine.dispose()
