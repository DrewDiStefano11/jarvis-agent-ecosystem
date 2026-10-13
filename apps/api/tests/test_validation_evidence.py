import hmac
import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest
from pydantic import ValidationError

from app.development_validation.evidence import (
    ReceiptVerifier,
    assess_receipts,
    build_execution_plan,
    verify_plan,
)
from app.development_validation.planner import digest
from app.models.development_validation import CodeState, FileChange
from app.models.validation_execution import (
    ExecutionMeasurement,
    ResourceMeasurements,
    SignedReceipt,
)

NOW = datetime(2026, 10, 12, tzinfo=UTC)
KEY = b"fixture-collector-key-32-bytes-minimum"


def state():
    return CodeState(
        repository_identity="github.com/example/jarvis",
        head_sha="a" * 40,
        base_sha="b" * 40,
        changes=(),
    )


def receipt(plan, command=None, **changes):
    command = command or plan.commands[0]
    payload = dict(
        execution_plan_hash=plan.execution_plan_hash,
        validation_plan_hash=plan.validation.plan_hash,
        repository_identity=plan.code_state.repository_identity,
        code_state_hash=plan.validation.code_state_hash,
        command=command,
        task_id="task-1",
        runtime_run_id="run-1",
        attempt_id="attempt-1",
        command_run_id="command-" + command.gate,
        sequence=1,
        started_at=NOW - timedelta(seconds=1),
        finished_at=NOW,
        outcome="passed",
        exit_code=0,
        resources=ResourceMeasurements(wall_time_ms=1000),
        stdout_digest=digest("stdout"),
        stderr_digest=digest("stderr"),
        output_bytes=12,
        output_truncated=False,
    )
    payload.update(changes)
    measurement = ExecutionMeasurement(**payload)
    envelope = dict(
        collector_id="fixture", key_id="key-1", measurement=measurement.model_dump(mode="json")
    )
    tag = hmac.new(
        KEY,
        b"jarvis-validation-receipt-v1\0"
        + json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode(),
        sha256,
    ).hexdigest()
    return SignedReceipt(**envelope, authentication_tag=tag)


def assess(plan, values, **kwargs):
    return assess_receipts(
        plan.code_state,
        plan,
        values,
        ReceiptVerifier({("fixture", "key-1"): KEY}),
        now=NOW,
        expected_execution_plan_hash=plan.execution_plan_hash,
        **kwargs,
    )


def test_authenticated_measurement_and_missing_gate():
    plan = build_execution_plan(state())
    assert assess(plan, [receipt(plan)]).ready
    assert assess(plan, []).missing == ("repository_integrity",)


def test_full_publication_requires_every_exact_suite():
    plan = build_execution_plan(state(), boundary="publication")
    values = [receipt(plan, c, sequence=i + 1) for i, c in enumerate(plan.commands)]
    assert assess(plan, values).ready
    assert not assess(plan, values[:-1]).ready


def test_independently_numbered_gate_streams_can_complete_publication():
    plan = build_execution_plan(state(), boundary="publication")
    values = [receipt(plan, command, sequence=1) for command in plan.commands]
    assert assess(plan, values).ready


def test_unapproved_reduced_browser_plan_cannot_authorize_receipt_assessment():
    approved = build_execution_plan(state(), boundary="publication", browser_required=True)
    reduced = build_execution_plan(state(), boundary="publication", browser_required=False)
    values = [receipt(reduced, command, sequence=1) for command in reduced.commands]
    with pytest.raises(ValueError, match="approved execution plan"):
        assess_receipts(
            state(),
            reduced,
            values,
            ReceiptVerifier({("fixture", "key-1"): KEY}),
            now=NOW,
            expected_execution_plan_hash=approved.execution_plan_hash,
        )


def test_unapproved_narrow_test_mapping_and_boundary_are_refused():
    backend_state = state().model_copy(
        update={
            "changes": (
                FileChange(
                    path="apps/api/app/services/example.py",
                    before_hash=None,
                    before_mode=None,
                    after_hash="c" * 64,
                    after_mode="100644",
                ),
            )
        }
    )
    approved = build_execution_plan(
        backend_state,
        affected_backend_tests=("apps/api/tests/test_api.py", "apps/api/tests/test_runtime.py"),
    )
    reduced = build_execution_plan(
        backend_state, affected_backend_tests=("apps/api/tests/test_api.py",)
    )
    publication = build_execution_plan(backend_state, boundary="publication")
    for original, candidate in ((approved, reduced), (publication, reduced)):
        with pytest.raises(ValueError, match="approved execution plan"):
            assess_receipts(
                backend_state,
                candidate,
                [],
                ReceiptVerifier({}),
                now=NOW,
                expected_execution_plan_hash=original.execution_plan_hash,
            )


def test_receipt_assessment_requires_separate_approval_reference():
    plan = build_execution_plan(state())
    with pytest.raises(TypeError, match="expected_execution_plan_hash"):
        assess_receipts(state(), plan, [receipt(plan)], ReceiptVerifier({}), now=NOW)


def test_approved_browser_scope_assesses_only_after_all_required_gates():
    plan = build_execution_plan(state(), boundary="publication", browser_required=True)
    values = [receipt(plan, command, sequence=1) for command in plan.commands]
    assert assess(plan, values).ready
    assert not assess(
        plan, [r for r in values if r.measurement.command.gate != "browser_acceptance"]
    ).ready


@pytest.mark.parametrize("bound", ["timeout", "memory"])
def test_zero_exit_with_measured_policy_overrun_is_a_recordable_failure(bound):
    plan = build_execution_plan(state())
    command = plan.commands[0]
    duration = command.timeout_seconds * 1000 + 1 if bound == "timeout" else 1000
    signed = receipt(
        plan,
        outcome="failed",
        exit_code=0,
        failure_kind="watchdog",
        started_at=NOW - timedelta(milliseconds=duration),
        finished_at=NOW,
        resources=ResourceMeasurements(
            wall_time_ms=duration,
            peak_memory_bytes=command.memory_limit_bytes + 1 if bound == "memory" else None,
        ),
    )
    assert assess(plan, [signed]).failed == (command.gate,)


def test_same_gate_sequence_conflict_and_cross_gate_command_replay_are_rejected():
    plan = build_execution_plan(state(), boundary="publication")
    first = receipt(plan, plan.commands[0])
    conflict = receipt(plan, plan.commands[0], command_run_id="conflict")
    replay = receipt(plan, plan.commands[1], command_run_id=first.measurement.command_run_id)
    for duplicate in (conflict, replay):
        with pytest.raises(ValueError, match="duplicate execution receipt"):
            assess(plan, [first, duplicate])


@pytest.mark.parametrize("outcome", ["failed", "cancelled", "interrupted", "unmeasured"])
def test_nonpassing_receipts_never_pass(outcome):
    plan = build_execution_plan(state())
    values = dict(outcome=outcome, exit_code=None)
    if outcome == "failed":
        values.update(exit_code=1, failure_kind="unknown")
    assert not assess(plan, [receipt(plan, **values)]).ready


def test_model_copy_forgery_untrusted_collector_and_revocation():
    plan = build_execution_plan(state())
    signed = receipt(plan)
    for forged in [
        signed.model_copy(update={"authentication_tag": "f" * 64}),
        signed.model_copy(update={"collector_id": "model"}),
        signed.model_copy(
            update={"measurement": signed.measurement.model_copy(update={"task_id": "forged"})}
        ),
    ]:
        with pytest.raises(ValueError):
            assess(plan, [forged])
    with pytest.raises(ValueError):
        assess_receipts(
            state(),
            plan,
            [signed],
            ReceiptVerifier({}),
            now=NOW,
            expected_execution_plan_hash=plan.execution_plan_hash,
        )


def test_expired_and_future_receipts():
    plan = build_execution_plan(state())
    signed = receipt(
        plan, started_at=NOW - timedelta(seconds=3602), finished_at=NOW - timedelta(seconds=3601)
    )
    assert assess(plan, [signed]).stale == ("repository_integrity",)
    future = receipt(plan, started_at=NOW, finished_at=NOW + timedelta(seconds=1))
    with pytest.raises(ValueError):
        assess(plan, [future])


@pytest.mark.parametrize(
    "changes",
    [
        {"exit_code": 1},
        {"exit_code": None},
        {"failure_kind": "product"},
        {"output_bytes": 65537},
        {"diagnostic_codes": ("Bearer secret",)},
        {"resources": ResourceMeasurements(wall_time_ms=0)},
        {"finished_at": NOW.replace(tzinfo=None)},
    ],
)
def test_incoherent_or_unsafe_measurements_rejected(changes):
    with pytest.raises(ValidationError):
        receipt(build_execution_plan(state()), **changes)


def test_replays_and_wrong_plan_rejected():
    plan = build_execution_plan(state())
    signed = receipt(plan)
    with pytest.raises(ValueError):
        assess(plan, [signed, signed])
    wrong = receipt(plan, execution_plan_hash="f" * 64)
    with pytest.raises(ValueError):
        assess(plan, [wrong])


def test_source_changes_require_new_plan():
    plan = build_execution_plan(state())
    changed = state().model_copy(
        update={
            "changes": (
                FileChange(
                    path="docs/a.md",
                    before_hash=None,
                    before_mode=None,
                    after_hash="c" * 64,
                    after_mode="100644",
                ),
            )
        }
    )
    with pytest.raises(ValueError):
        assess_receipts(
            changed,
            plan,
            [receipt(plan)],
            ReceiptVerifier({("fixture", "key-1"): KEY}),
            now=NOW,
            expected_execution_plan_hash=plan.execution_plan_hash,
        )
    with pytest.raises(ValueError):
        assess(build_execution_plan(changed), [receipt(plan)])


def test_rehashing_relaxed_command_is_not_authority():
    plan = build_execution_plan(state())
    altered = plan.model_copy(
        update={"commands": (plan.commands[0].model_copy(update={"timeout_seconds": 3600}),)}
    )
    altered = altered.model_copy(
        update={
            "execution_plan_hash": digest(
                altered.model_dump(mode="json", exclude={"execution_plan_hash"})
            )
        }
    )
    with pytest.raises(ValueError):
        verify_plan(altered)


@pytest.mark.parametrize(
    "field", ["repository_identity", "code_state_hash", "validation_plan_hash"]
)
def test_authenticated_but_wrong_lineage_cannot_pass(field):
    plan = build_execution_plan(state())
    value = "github.com/foreign/repo" if field == "repository_identity" else "f" * 64
    with pytest.raises(ValueError):
        assess(plan, [receipt(plan, **{field: value})])


def test_signed_wrong_command_cannot_pass():
    plan = build_execution_plan(state())
    command = plan.commands[0].model_copy(update={"suite_digest": "f" * 64})
    with pytest.raises(ValueError):
        assess(plan, [receipt(plan, command)])


def test_latest_failure_or_interruption_supersedes_pass_in_any_input_order():
    plan = build_execution_plan(state())
    passed = receipt(
        plan, started_at=NOW - timedelta(seconds=3), finished_at=NOW - timedelta(seconds=2)
    )
    for outcome in ("failed", "interrupted"):
        changes = dict(command_run_id="command-2", sequence=2, outcome=outcome, exit_code=None)
        if outcome == "failed":
            changes.update(exit_code=1, failure_kind="test")
        latest = receipt(plan, **changes)
        for values in ([passed, latest], [latest, passed]):
            assert not assess(plan, values).ready


def test_tied_failure_is_conservative():
    plan = build_execution_plan(state())
    failure = receipt(
        plan,
        command_run_id="command-2",
        sequence=2,
        outcome="failed",
        exit_code=1,
        failure_kind="test",
    )
    assert assess(plan, [receipt(plan), failure]).failed == ("repository_integrity",)


@pytest.mark.parametrize("outcome", ["failed", "interrupted", "cancelled", "unmeasured"])
def test_higher_sequence_supersedes_pass_when_clock_regresses(outcome):
    plan = build_execution_plan(state())
    passed = receipt(plan)
    changes = dict(
        command_run_id="command-2",
        sequence=2,
        outcome=outcome,
        exit_code=None,
        started_at=NOW - timedelta(seconds=3),
        finished_at=NOW - timedelta(seconds=2),
    )
    if outcome == "failed":
        changes.update(exit_code=1, failure_kind="test")
    latest = receipt(plan, **changes)
    for values in ([passed, latest], [latest, passed]):
        assert not assess(plan, values).ready


def test_expired_higher_sequence_cannot_fall_back_to_fresh_lower_sequence():
    plan = build_execution_plan(state())
    latest = receipt(
        plan,
        command_run_id="command-2",
        sequence=2,
        started_at=NOW - timedelta(seconds=3602),
        finished_at=NOW - timedelta(seconds=3601),
    )
    for values in ([receipt(plan), latest], [latest, receipt(plan)]):
        result = assess(plan, values)
        assert not result.ready and result.stale == ("repository_integrity",)


def test_sequence_is_scoped_to_run_attempt_and_gate():
    plan = build_execution_plan(state())
    old = receipt(
        plan,
        command_run_id="command-old",
        sequence=99,
        outcome="failed",
        exit_code=1,
        failure_kind="test",
        started_at=NOW - timedelta(seconds=3),
        finished_at=NOW - timedelta(seconds=2),
    )
    fresh = receipt(plan, runtime_run_id="run-2")
    assert assess(plan, [old, fresh]).ready


def test_receipt_bounds_and_trusted_clock_configuration():
    plan = build_execution_plan(state())
    with pytest.raises(ValueError):
        assess(plan, (receipt(plan) for _ in range(513)))
    with pytest.raises(ValueError):
        ReceiptVerifier({("fixture", "key-1"): b"short"})
    with pytest.raises(ValueError):
        assess(plan, [], max_age_seconds=0)
    with pytest.raises(ValueError):
        assess_receipts(
            state(),
            plan,
            [],
            ReceiptVerifier({}),
            now=NOW.replace(tzinfo=None),
            expected_execution_plan_hash=plan.execution_plan_hash,
        )


def test_backend_full_suite_budget_is_bounded_and_matches_measured_baseline():
    plan = build_execution_plan(state(), boundary="publication")
    backend = next(c for c in plan.commands if c.gate == "backend_tests")
    assert backend.timeout_seconds == 3600
    assert all(c.timeout_seconds == 900 for c in plan.commands if c.gate != "backend_tests")


def test_windows_unsigned_crash_exit_and_failed_budget_overrun_remain_recordable():
    plan = build_execution_plan(state())
    crash = receipt(plan, outcome="failed", exit_code=3221225477, failure_kind="unknown")
    assert assess(plan, [crash]).failed == ("repository_integrity",)
    overrun = receipt(
        plan,
        outcome="failed",
        exit_code=1,
        failure_kind="watchdog",
        started_at=NOW - timedelta(hours=2),
        finished_at=NOW,
        resources=ResourceMeasurements(wall_time_ms=7200000, peak_memory_bytes=16 * 1024**3),
    )
    assert not assess(plan, [overrun]).ready
    with pytest.raises(ValidationError):
        receipt(
            plan,
            started_at=NOW - timedelta(hours=2),
            finished_at=NOW,
            resources=ResourceMeasurements(wall_time_ms=7200000),
        )


@pytest.mark.parametrize("identity", ["invalid-repo", "github.com/example/" + "x" * 101])
def test_measurement_repository_identity_is_canonical_and_bounded(identity):
    with pytest.raises(ValidationError):
        receipt(build_execution_plan(state()), repository_identity=identity)
