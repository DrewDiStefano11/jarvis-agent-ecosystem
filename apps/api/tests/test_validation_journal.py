"""Native ownership, append-only journal and conservative crash recovery tests."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.core.errors import DomainError
from app.db.models import AuditEventRow, OutboxEventRow, SystemStateRow, TaskLeaseRow
from app.development_validation.evidence import ReceiptVerifier
from app.models.agent_runtime import BeginAttemptCommand, StartAttemptCommand
from app.models.validation_journal import JournalRequest
from app.validation_journal.service import ValidationJournalService
from tests.agent_runtime_testkit import ts
from tests.test_validation_evidence import KEY, receipt, state

pytest_plugins = ["tests.test_self_build_workspaces"]


@pytest.fixture
def journal(workspace):
    app, actor, workspace_service, reserve_request, _, _ = workspace
    reserved = workspace_service.reserve(actor, reserve_request)
    runtime = app.state.agent_runtime_service
    current = runtime.read_run_authorized("run-1", actor)
    beginning = runtime.handle_authorized(
        BeginAttemptCommand(
            run_id="run-1",
            command_id="begin-validation",
            expected_run_version=current.version,
            timestamp=ts(3),
            executor_reference=reserve_request.worker_id,
        ),
        actor,
    )
    runtime.handle_authorized(
        StartAttemptCommand(
            run_id="run-1",
            command_id="start-validation",
            expected_run_version=beginning.snapshot.version,
            timestamp=ts(4),
            attempt_id=beginning.snapshot.active_attempt_id,
        ),
        actor,
    )
    operator = app.state.remote_control_service.access.authenticate(
        "Bearer " + "x" * 48, secure_transport=True
    )
    service = ValidationJournalService(
        app,
        state_reader=lambda row, session: state().model_copy(
            update={"base_sha": row.plan_json["base_sha"]}
        ),
        receipt_verifier=ReceiptVerifier({("fixture", "key-1"): KEY}),
    )
    plan = service.preview(actor, reserved.workspace_id)
    approval = service.approve(operator, reserved.workspace_id, plan.execution_plan_hash)
    request = JournalRequest(
        expected_plan_hash=plan.execution_plan_hash,
        approval_id=approval.approval_id,
        worker_id=reserve_request.worker_id,
        lease_token=reserve_request.lease_token,
    )
    return app, actor, operator, service, reserved.workspace_id, request


def entries(app):
    with app.state.repository.session_factory() as session:
        return list(
            session.scalars(
                select(AuditEventRow)
                .where(AuditEventRow.event_type == "development_validation.journal_entry")
                .order_by(AuditEventRow.sequence_number)
            )
        )


def advance(service, actor, workspace_id, request, snapshot, kind, **fields):
    return service.advance(
        actor,
        workspace_id,
        request,
        snapshot.intent.journal_id,
        expected_sequence=snapshot.sequence,
        kind=kind,
        **fields,
    )


def test_intent_restart_and_atomic_outbox(journal):
    app, actor, _, service, workspace_id, request = journal
    initial = service.prepare(actor, workspace_id, request)
    assert initial.state == "prepared" and initial.sequence == 1
    assert service.prepare(actor, workspace_id, request) == initial
    restarted = ValidationJournalService(
        app, state_reader=service.state_reader, receipt_verifier=service.verifier
    )
    assert restarted.read(actor, workspace_id, initial.intent.journal_id) == initial
    assert len(entries(app)) == 1
    with app.state.repository.session_factory() as session:
        audit = entries(app)[0]
        outbox = session.scalar(
            select(OutboxEventRow).where(
                OutboxEventRow.event_type == audit.event_type,
                OutboxEventRow.sequence_number == audit.sequence_number,
            )
        )
        assert outbox and outbox.envelope["payload"]["entry"] == audit.payload["payload"]["entry"]
    assert request.lease_token not in initial.model_dump_json()


def test_crash_before_checkpoint_recovers_same_intent(journal, monkeypatch):
    app, actor, _, service, workspace_id, request = journal
    handle = service.runtime.handle_authorized

    def crash(*args, **kwargs):
        raise RuntimeError("checkpoint crash")

    monkeypatch.setattr(service.runtime, "handle_authorized", crash)
    with pytest.raises(RuntimeError):
        service.prepare(actor, workspace_id, request)
    assert len(entries(app)) == 1
    monkeypatch.setattr(service.runtime, "handle_authorized", handle)
    recovered = service.prepare(actor, workspace_id, request)
    assert recovered.sequence == 1 and len(entries(app)) == 1


def test_interrupted_step_reconciles_uncertain_without_invented_success(journal):
    app, actor, _, service, workspace_id, request = journal
    prepared = service.prepare(actor, workspace_id, request)
    gate = prepared.intent.plan.commands[0].gate
    intended = advance(
        service,
        actor,
        workspace_id,
        request,
        prepared,
        "step_intent",
        gate=gate,
        command_run_id="command-1",
    )
    started = advance(
        service,
        actor,
        workspace_id,
        request,
        intended,
        "step_started",
        gate=gate,
        command_run_id="command-1",
    )
    recovered = service.reconcile(actor, workspace_id, request, started.intent.journal_id)
    assert recovered.steps[0].state == "uncertain" and recovered.steps[0].receipt is None
    assert service.reconcile(actor, workspace_id, request, started.intent.journal_id) == recovered
    assert len(entries(app)) == 4


@pytest.mark.parametrize("gate_local_sequence", [True, False])
def test_measured_journal_all_gates_and_finalization(journal, monkeypatch, gate_local_sequence):
    app, actor, _, service, workspace_id, request = journal
    current = service.prepare(actor, workspace_id, request)
    for index, command in enumerate(current.intent.plan.commands):
        reference = "command-" + str(index)
        current = advance(
            service,
            actor,
            workspace_id,
            request,
            current,
            "step_intent",
            gate=command.gate,
            command_run_id=reference,
        )
        current = advance(
            service,
            actor,
            workspace_id,
            request,
            current,
            "step_started",
            gate=command.gate,
            command_run_id=reference,
        )
        finished = datetime.now(UTC)
        elapsed = round((finished - current.updated_at).total_seconds() * 1000)
        from app.models.validation_execution import ResourceMeasurements

        signed = receipt(
            current.intent.plan,
            command,
            task_id=current.intent.owner.task_id,
            runtime_run_id=current.intent.owner.runtime_run_id,
            attempt_id=current.intent.owner.attempt_id,
            command_run_id=reference,
            sequence=1 if gate_local_sequence else index + 1,
            started_at=current.updated_at,
            finished_at=finished,
            resources=ResourceMeasurements(wall_time_ms=elapsed),
        )
        current = advance(
            service,
            actor,
            workspace_id,
            request,
            current,
            "step_result",
            gate=command.gate,
            command_run_id=reference,
            receipt=signed,
        )
        if index == 0:
            before = len(entries(app))
            with pytest.raises(ValueError, match="identity cannot be reused"):
                advance(
                    service,
                    actor,
                    workspace_id,
                    request,
                    current,
                    "step_intent",
                    gate=current.intent.plan.commands[1].gate,
                    command_run_id=reference,
                )
            assert len(entries(app)) == before
    final = advance(service, actor, workspace_id, request, current, "finalized")
    assert final.state == "finalized" and all(s.state == "measured" for s in final.steps)
    assert service.assess(actor, workspace_id, final.intent.journal_id, now=datetime.now(UTC)).ready
    assert service.reconcile(actor, workspace_id, request, final.intent.journal_id) == final
    with pytest.raises(ValueError):
        advance(service, actor, workspace_id, request, final, "finalized")
    # A coherent finalized journal cannot substitute for the original independent
    # operator audit. Exercise actual stored approval provenance, not a plan hash echo.
    from copy import deepcopy

    with app.state.repository.session_factory() as session:
        original = session.get(AuditEventRow, request.approval_id)
        original_payload = deepcopy(original.payload)
        original_task, original_actor, original_kind = (
            original.task_id,
            original.actor,
            original.event_type,
        )
    for tamper in ("plan", "workspace", "task", "self_approval", "kind"):
        with app.state.repository.session_factory.begin() as session:
            original = session.get(AuditEventRow, request.approval_id)
            payload = deepcopy(original_payload)
            if tamper == "plan":
                payload["payload"]["plan"] = {}
            elif tamper == "workspace":
                payload["payload"]["workspaceId"] = "foreign"
            elif tamper == "task":
                original.task_id = None
            elif tamper == "self_approval":
                original.actor = actor.actor_id
            else:
                original.event_type = "foreign"
            original.payload = payload
        with pytest.raises(DomainError) as failure:
            service.assess(actor, workspace_id, final.intent.journal_id, now=datetime.now(UTC))
        assert failure.value.code == "VALIDATION_APPROVAL_REQUIRED"
        with app.state.repository.session_factory.begin() as session:
            original = session.get(AuditEventRow, request.approval_id)
            original.payload = deepcopy(original_payload)
            original.task_id, original.actor, original.event_type = (
                original_task,
                original_actor,
                original_kind,
            )
    assert service.assess(actor, workspace_id, final.intent.journal_id, now=datetime.now(UTC)).ready
    monkeypatch.setattr(service.runtime, "checkpoints_authorized", lambda *args: [])
    with pytest.raises(DomainError):
        service.assess(actor, workspace_id, final.intent.journal_id, now=datetime.now(UTC))


def test_exact_transition_replay_and_conflict(journal):
    app, actor, _, service, workspace_id, request = journal
    prepared = service.prepare(actor, workspace_id, request)
    gate = prepared.intent.plan.commands[0].gate
    intended = advance(
        service,
        actor,
        workspace_id,
        request,
        prepared,
        "step_intent",
        gate=gate,
        command_run_id="command-1",
    )
    assert (
        advance(
            service,
            actor,
            workspace_id,
            request,
            prepared,
            "step_intent",
            gate=gate,
            command_run_id="command-1",
        )
        == intended
    )
    with pytest.raises(DomainError):
        advance(
            service,
            actor,
            workspace_id,
            request,
            prepared,
            "step_intent",
            gate=gate,
            command_run_id="foreign",
        )
    assert len(entries(app)) == 2


@pytest.mark.parametrize("mutation", ["stop", "lease", "plan", "approval"])
def test_lost_authority_cannot_append(journal, mutation):
    app, actor, _, service, workspace_id, request = journal
    prepared = service.prepare(actor, workspace_id, request)
    if mutation == "stop":
        with app.state.repository.session_factory.begin() as session:
            session.get(SystemStateRow, 1).emergency_stop = True
    elif mutation == "lease":
        request = request.model_copy(update={"lease_token": "foreign"})
    elif mutation == "plan":
        request = request.model_copy(update={"expected_plan_hash": "f" * 64})
    else:
        request = request.model_copy(update={"approval_id": "foreign"})
    with pytest.raises(DomainError):
        advance(
            service,
            actor,
            workspace_id,
            request,
            prepared,
            "step_intent",
            gate=prepared.intent.plan.commands[0].gate,
            command_run_id="command-1",
        )
    assert len(entries(app)) == 1


def test_self_approval_refused_and_history_survives_lease_loss(journal):
    app, actor, _, service, workspace_id, request = journal
    prepared = service.prepare(actor, workspace_id, request)
    with pytest.raises(DomainError):
        service.approve(actor, workspace_id, request.expected_plan_hash)
    with app.state.repository.session_factory.begin() as session:
        session.get(TaskLeaseRow, prepared.intent.owner.task_id).lease_token = "different"
    assert service.read(actor, workspace_id, prepared.intent.journal_id) == prepared
    with pytest.raises(DomainError):
        service.reconcile(actor, workspace_id, request, prepared.intent.journal_id)


def test_lost_checkpoint_ack_reuses_persisted_native_checkpoint(journal, monkeypatch):
    app, actor, _, service, workspace_id, request = journal
    native = service.runtime.handle_authorized

    def lost_ack(*args, **kwargs):
        native(*args, **kwargs)
        raise RuntimeError("lost acknowledgement")

    monkeypatch.setattr(service.runtime, "handle_authorized", lost_ack)
    with pytest.raises(RuntimeError):
        service.prepare(actor, workspace_id, request)
    monkeypatch.setattr(service.runtime, "handle_authorized", native)
    recovered = service.prepare(actor, workspace_id, request)
    assert recovered.sequence == 1 and len(entries(app)) == 1
    assert len(service.runtime.checkpoints_authorized("run-1", actor)) == 1


def test_stale_source_state_cannot_append_or_acknowledge(journal):
    app, actor, _, service, workspace_id, request = journal
    prepared = service.prepare(actor, workspace_id, request)
    previous = service.state_reader
    service.state_reader = lambda row, session: previous(row, session).model_copy(
        update={"head_sha": "f" * 40}
    )
    with pytest.raises(DomainError):
        service.reconcile(actor, workspace_id, request, prepared.intent.journal_id)
    assert len(entries(app)) == 1


def test_disabled_operator_and_expired_approval_block_writes(journal):
    app, actor, _, service, workspace_id, request = journal
    prepared = service.prepare(actor, workspace_id, request)
    remote = app.state.remote_control_service
    del app.state.remote_control_service
    with pytest.raises(DomainError):
        service.reconcile(actor, workspace_id, request, prepared.intent.journal_id)
    app.state.remote_control_service = remote
    with app.state.repository.session_factory.begin() as session:
        event = session.get(AuditEventRow, request.approval_id)
        event.payload = {
            **event.payload,
            "payload": {**event.payload["payload"], "expiresAt": "2020-01-01T00:00:00+00:00"},
        }
    with pytest.raises(DomainError):
        service.reconcile(actor, workspace_id, request, prepared.intent.journal_id)
    assert len(entries(app)) == 1


def test_tampered_journal_digest_and_native_checkpoint_refused(journal, monkeypatch):
    app, actor, _, service, workspace_id, request = journal
    prepared = service.prepare(actor, workspace_id, request)
    checkpoint = service.runtime.checkpoints_authorized("run-1", actor)[0]
    forged = checkpoint.model_copy(update={"integrity_digest": "sha256:" + "f" * 64})
    monkeypatch.setattr(service.runtime, "checkpoints_authorized", lambda *args: [forged])
    with pytest.raises(DomainError):
        service.reconcile(actor, workspace_id, request, prepared.intent.journal_id)
    with app.state.repository.session_factory.begin() as session:
        event = session.get(AuditEventRow, entries(app)[0].id)
        entry = {**event.payload["payload"]["entry"], "entry_digest": "f" * 64}
        event.payload = {**event.payload, "payload": {**event.payload["payload"], "entry": entry}}
    with pytest.raises(ValueError):
        service.read(actor, workspace_id, prepared.intent.journal_id)


def test_checkpoint_is_required_before_any_step_advances(journal, monkeypatch):
    app, actor, _, service, workspace_id, request = journal
    prepared = service.prepare(actor, workspace_id, request)
    monkeypatch.setattr(service.runtime, "checkpoints_authorized", lambda *args: [])
    with pytest.raises(DomainError):
        advance(
            service,
            actor,
            workspace_id,
            request,
            prepared,
            "step_intent",
            gate=prepared.intent.plan.commands[0].gate,
            command_run_id="command-1",
        )
    assert len(entries(app)) == 1


def test_skipped_gate_and_early_finalization_are_rejected(journal):
    app, actor, _, service, workspace_id, request = journal
    prepared = service.prepare(actor, workspace_id, request)
    with pytest.raises(ValueError):
        advance(service, actor, workspace_id, request, prepared, "finalized")
    with pytest.raises(ValueError):
        advance(
            service,
            actor,
            workspace_id,
            request,
            prepared,
            "step_intent",
            gate=prepared.intent.plan.commands[-1].gate,
            command_run_id="command-1",
        )
    assert len(entries(app)) == 1


def test_forged_or_wrong_attempt_receipts_never_persist(journal):
    app, actor, _, service, workspace_id, request = journal
    prepared = service.prepare(actor, workspace_id, request)
    command = prepared.intent.plan.commands[0]
    intended = advance(
        service,
        actor,
        workspace_id,
        request,
        prepared,
        "step_intent",
        gate=command.gate,
        command_run_id="command-1",
    )
    started = advance(
        service,
        actor,
        workspace_id,
        request,
        intended,
        "step_started",
        gate=command.gate,
        command_run_id="command-1",
    )
    from app.models.validation_execution import ResourceMeasurements

    finished = datetime.now(UTC)
    signed = receipt(
        started.intent.plan,
        command,
        task_id=started.intent.owner.task_id,
        runtime_run_id=started.intent.owner.runtime_run_id,
        attempt_id="foreign-attempt",
        command_run_id="command-1",
        sequence=1,
        started_at=started.updated_at,
        finished_at=finished,
        resources=ResourceMeasurements(
            wall_time_ms=round((finished - started.updated_at).total_seconds() * 1000)
        ),
    )
    for candidate in (signed, signed.model_copy(update={"authentication_tag": "f" * 64})):
        with pytest.raises(ValueError):
            advance(
                service,
                actor,
                workspace_id,
                request,
                started,
                "step_result",
                gate=command.gate,
                command_run_id="command-1",
                receipt=candidate,
            )
    assert len(entries(app)) == 3


def test_partial_history_cannot_be_assessed_as_final_result(journal):
    _, actor, _, service, workspace_id, request = journal
    prepared = service.prepare(actor, workspace_id, request)
    with pytest.raises(DomainError):
        service.assess(actor, workspace_id, prepared.intent.journal_id, now=datetime.now(UTC))


def test_journal_append_failure_rolls_back_audit_and_outbox(journal, monkeypatch):
    app, actor, _, service, workspace_id, request = journal
    emit = service._emit

    def fail_after_emit(*args, **kwargs):
        emit(*args, **kwargs)
        raise RuntimeError("transaction crash")

    monkeypatch.setattr(service, "_emit", fail_after_emit)
    with pytest.raises(RuntimeError):
        service.prepare(actor, workspace_id, request)
    assert entries(app) == []
    with app.state.repository.session_factory() as session:
        assert (
            list(
                session.scalars(
                    select(OutboxEventRow).where(
                        OutboxEventRow.event_type == "development_validation.journal_entry"
                    )
                )
            )
            == []
        )
    assert service.runtime.checkpoints_authorized("run-1", actor) == []


def test_incoherent_native_attempt_cannot_prepare_intent(journal):
    from app.db.models import AgentRuntimeAttemptRow
    from app.models.agent_runtime import AgentRunAttempt

    app, actor, _, service, workspace_id, request = journal
    snapshot = service.runtime.read_run_authorized("run-1", actor)
    with app.state.repository.session_factory.begin() as session:
        row = session.get(AgentRuntimeAttemptRow, (snapshot.active_attempt_id, "run-1"))
        parsed = AgentRunAttempt.model_validate_json(row.contract_json)
        payload = parsed.model_dump(mode="json")
        payload["state"] = "paused"
        row.contract_json = AgentRunAttempt.model_validate(payload).model_dump_json()
    with pytest.raises(DomainError) as failure:
        service.prepare(actor, workspace_id, request)
    assert failure.value.code == "VALIDATION_ATTEMPT_REQUIRED" and entries(app) == []


def test_operator_cannot_approve_policy_blocked_source_paths(journal):
    from app.models.development_validation import FileChange

    _, actor, operator, service, workspace_id, _ = journal
    previous = service.state_reader
    change = FileChange(
        path=".env", before_hash=None, before_mode=None, after_hash="c" * 64, after_mode="100644"
    )
    service.state_reader = lambda row, session: previous(row, session).model_copy(
        update={"changes": (change,)}
    )
    plan = service.preview(actor, workspace_id)
    assert plan.validation.blocked_paths == (".env",)
    with pytest.raises(DomainError) as failure:
        service.approve(operator, workspace_id, plan.execution_plan_hash)
    assert failure.value.code == "VALIDATION_SOURCE_SCOPE_BLOCKED"


def test_exact_operator_renewal_preserves_original_intent_and_stamps_current_grant(journal):
    app, actor, operator, service, workspace_id, request = journal
    prepared = service.prepare(actor, workspace_id, request)
    with app.state.repository.session_factory.begin() as session:
        event = session.get(AuditEventRow, request.approval_id)
        event.payload = {
            **event.payload,
            "payload": {**event.payload["payload"], "expiresAt": "2020-01-01T00:00:00+00:00"},
        }
    renewed = service.approve(
        operator, workspace_id, request.expected_plan_hash, valid_for_seconds=3600
    )
    current_request = request.model_copy(update={"approval_id": renewed.approval_id})
    assert service.prepare(actor, workspace_id, current_request) == prepared
    updated = advance(
        service,
        actor,
        workspace_id,
        current_request,
        prepared,
        "step_intent",
        gate=prepared.intent.plan.commands[0].gate,
        command_run_id="command-1",
    )
    assert updated.intent.owner.approval_id == request.approval_id
    assert entries(app)[-1].payload["payload"]["authorizationApprovalId"] == renewed.approval_id
    with pytest.raises(DomainError):
        advance(
            service,
            actor,
            workspace_id,
            request,
            updated,
            "step_started",
            gate=prepared.intent.plan.commands[0].gate,
            command_run_id="command-1",
        )


@pytest.mark.parametrize("operation", ["preview", "approve", "prepare", "advance", "reconcile"])
def test_abandoned_workspace_refuses_validation_authority_but_preserves_history(journal, operation):
    from app.models.self_build import AbandonWorkspaceRequest, ApproveWorkspaceAbandonRequest

    app, actor, operator, service, workspace_id, request = journal
    prepared = service.prepare(actor, workspace_id, request)
    plan = service.workspace.preview_abandon(actor, workspace_id)
    approval = service.workspace.approve_abandon(
        operator,
        ApproveWorkspaceAbandonRequest(
            workspace_id=workspace_id, expected_plan_hash=plan.plan_hash
        ),
    )
    service.workspace.abandon(
        actor,
        workspace_id,
        AbandonWorkspaceRequest(
            expected_version=plan.workspace_version,
            expected_plan_hash=plan.plan_hash,
            approval_id=approval.approval_id,
            worker_id=request.worker_id,
            lease_token=request.lease_token,
        ),
    )
    before = len(entries(app))
    with pytest.raises(DomainError) as failure:
        if operation == "preview":
            service.preview(actor, workspace_id)
        elif operation == "approve":
            service.approve(operator, workspace_id, request.expected_plan_hash)
        elif operation == "prepare":
            service.prepare(actor, workspace_id, request)
        elif operation == "reconcile":
            service.reconcile(actor, workspace_id, request, prepared.intent.journal_id)
        else:
            advance(
                service,
                actor,
                workspace_id,
                request,
                prepared,
                "step_intent",
                gate=prepared.intent.plan.commands[0].gate,
                command_run_id="command-1",
            )
    assert failure.value.code == "VALIDATION_OWNER_INACTIVE"
    assert len(entries(app)) == before
    assert service.read(actor, workspace_id, prepared.intent.journal_id) == prepared


def test_historical_journal_survives_policy_upgrade_without_granting_current_authority(
    journal, monkeypatch
):
    from app.development_validation import evidence
    from app.development_validation.planner import digest
    from app.validation_journal import service as journal_service

    _, actor, _, service, workspace_id, request = journal
    prepared = service.prepare(actor, workspace_id, request)
    original = evidence.build_execution_plan

    def upgraded(*args, **kwargs):
        plan = original(*args, **kwargs)
        commands = (
            plan.commands[0].model_copy(update={"policy_fingerprint": "f" * 64}),
            *plan.commands[1:],
        )
        changed = plan.model_copy(update={"commands": commands})
        return changed.model_copy(
            update={
                "execution_plan_hash": digest(
                    changed.model_dump(mode="json", exclude={"execution_plan_hash"})
                )
            }
        )

    monkeypatch.setattr(evidence, "build_execution_plan", upgraded)
    monkeypatch.setattr(journal_service, "build_execution_plan", upgraded)
    assert service.read(actor, workspace_id, prepared.intent.journal_id) == prepared
    with pytest.raises(DomainError):
        service.prepare(actor, workspace_id, request)
    with pytest.raises(ValueError):
        evidence.verify_plan(prepared.intent.plan)


def finalize_fixture(journal, *, failure_kind=None, diagnostic_codes=(), uncertain=False):
    """Signed fixture measurements test storage only, never claim real execution."""
    from app.models.validation_execution import ResourceMeasurements

    _, actor, _, service, workspace_id, request = journal
    current = service.prepare(actor, workspace_id, request)
    for index, command in enumerate(current.intent.plan.commands):
        reference = "verdict-command-" + str(index)
        for kind in ("step_intent", "step_started"):
            current = advance(
                service,
                actor,
                workspace_id,
                request,
                current,
                kind,
                gate=command.gate,
                command_run_id=reference,
            )
        if uncertain and index == 0:
            current = service.reconcile(actor, workspace_id, request, current.intent.journal_id)
            continue
        finished = datetime.now(UTC)
        failing = index == 0 and failure_kind is not None
        signed = receipt(
            current.intent.plan,
            command,
            task_id=current.intent.owner.task_id,
            runtime_run_id=current.intent.owner.runtime_run_id,
            attempt_id=current.intent.owner.attempt_id,
            command_run_id=reference,
            sequence=1,
            started_at=current.updated_at,
            finished_at=finished,
            resources=ResourceMeasurements(
                wall_time_ms=round((finished - current.updated_at).total_seconds() * 1000)
            ),
            outcome="failed" if failing else "passed",
            exit_code=1 if failing else 0,
            failure_kind=failure_kind if failing else None,
            diagnostic_codes=diagnostic_codes if failing else (),
        )
        current = advance(
            service,
            actor,
            workspace_id,
            request,
            current,
            "step_result",
            gate=command.gate,
            command_run_id=reference,
            receipt=signed,
        )
    return advance(service, actor, workspace_id, request, current, "finalized")


def verdict_events(app):
    with app.state.repository.session_factory() as session:
        return list(
            session.scalars(
                select(AuditEventRow).where(
                    AuditEventRow.event_type == "development_validation.verdict_recorded"
                )
            )
        )


def test_verdict_restart_lost_response_and_atomic_outbox(journal):
    app, actor, operator, service, workspace_id, request = journal
    final = finalize_fixture(journal)
    original_approval_id = request.approval_id
    renewed = service.approve(operator, workspace_id, request.expected_plan_hash)
    request = request.model_copy(update={"approval_id": renewed.approval_id})
    result = service.record_verdict(
        actor,
        workspace_id,
        request,
        final.intent.journal_id,
        expected_journal_digest=final.digest,
    )
    assert result.assessment.ready
    assert result.approval_id == original_approval_id
    assert all(f.disposition == "passed" for f in result.repair.findings)
    assert not result.execution_authorized and not result.source_mutation_authorized
    assert not result.publication_authorized
    assert request.lease_token not in result.model_dump_json()
    restarted = ValidationJournalService(
        app, state_reader=service.state_reader, receipt_verifier=service.verifier
    )
    assert restarted.read_verdict(actor, workspace_id, final.intent.journal_id) == result
    assert (
        restarted.record_verdict(
            actor,
            workspace_id,
            request,
            final.intent.journal_id,
            expected_journal_digest=final.digest,
        )
        == result
    )
    assert len(verdict_events(app)) == 1
    event = verdict_events(app)[0]
    assert event.payload["payload"]["authorizationApprovalId"] == request.approval_id
    with app.state.repository.session_factory() as session:
        outbox = session.scalar(
            select(OutboxEventRow).where(
                OutboxEventRow.event_type == event.event_type,
                OutboxEventRow.sequence_number == event.sequence_number,
            )
        )
        assert outbox.envelope["payload"] == event.payload["payload"]

    # Reconstruct the application/runtime repositories from the same isolated
    # database too: the record must not depend on process-local service state.
    from app.main import create_app

    restored_app = create_app(database_url=app.state.settings.database_url)
    restored_actor = restored_app.state.agent_runtime_service.authenticate_actor(actor.actor_id)
    restored = ValidationJournalService(
        restored_app, state_reader=service.state_reader, receipt_verifier=service.verifier
    )
    assert restored.read_verdict(restored_actor, workspace_id, final.intent.journal_id) == result


@pytest.mark.parametrize(
    "failure_kind,code,recommendation",
    [
        ("test", "assertion_failed", "diagnose_failure"),
        ("migration", "migration_failed", "diagnose_failure"),
        ("lint_type_build", "typecheck_failed", "diagnose_failure"),
        ("infrastructure", "unknown", "diagnose_failure"),
        ("watchdog", "timeout", "request_bounded_retry"),
        ("dependency", "dependency_unavailable", "request_bounded_retry"),
    ],
)
def test_journal_backed_diagnosis_retains_classification_and_exact_source(
    journal, failure_kind, code, recommendation
):
    _, actor, _, service, workspace_id, _ = journal
    final = finalize_fixture(journal, failure_kind=failure_kind, diagnostic_codes=(code,))
    diagnosis = service.diagnose(
        actor, workspace_id, final.intent.journal_id, now=datetime.now(UTC)
    )
    finding = diagnosis.findings[0]
    assert finding.failure_kind == failure_kind
    assert finding.diagnostic_codes == (code,)
    assert finding.recommendation == recommendation
    assert finding.reproduction.code_state_hash == final.intent.plan.validation.code_state_hash
    assert finding.reproduction.command_run_id == final.steps[0].command_run_id


def test_uncertain_final_journal_never_records_a_passing_verdict(journal):
    _, actor, _, service, workspace_id, request = journal
    final = finalize_fixture(journal, uncertain=True)
    result = service.record_verdict(
        actor,
        workspace_id,
        request,
        final.intent.journal_id,
        expected_journal_digest=final.digest,
    )
    assert not result.assessment.ready
    assert result.repair.findings[0].disposition == "missing"
    assert result.repair.findings[0].recommendation == "gather_evidence"


def test_changed_source_blocks_verdict_and_historical_diagnosis_is_stale(journal):
    app, actor, _, service, workspace_id, request = journal
    final = finalize_fixture(journal)
    previous = service.state_reader
    service.state_reader = lambda row, session: previous(row, session).model_copy(
        update={"head_sha": "f" * 40}
    )
    with pytest.raises(DomainError, match="changed"):
        service.record_verdict(
            actor,
            workspace_id,
            request,
            final.intent.journal_id,
            expected_journal_digest=final.digest,
        )
    diagnosis = service.diagnose(
        actor, workspace_id, final.intent.journal_id, now=datetime.now(UTC)
    )
    assert diagnosis.source_changed
    assert all(f.disposition == "stale" for f in diagnosis.findings)
    with pytest.raises(ValueError):
        service.assess(actor, workspace_id, final.intent.journal_id, now=datetime.now(UTC))
    assert verdict_events(app) == []


def test_verdict_rollback_and_checkpoint_gap_are_fail_closed(journal, monkeypatch):
    app, actor, _, service, workspace_id, request = journal
    final = finalize_fixture(journal)
    emit = service._emit

    def crash(*args, **kwargs):
        emit(*args, **kwargs)
        raise RuntimeError("verdict transaction crash")

    monkeypatch.setattr(service, "_emit", crash)
    with pytest.raises(RuntimeError):
        service.record_verdict(
            actor,
            workspace_id,
            request,
            final.intent.journal_id,
            expected_journal_digest=final.digest,
        )
    assert verdict_events(app) == []
    with app.state.repository.session_factory() as session:
        assert not list(
            session.scalars(
                select(OutboxEventRow).where(
                    OutboxEventRow.event_type == "development_validation.verdict_recorded"
                )
            )
        )
    monkeypatch.setattr(service.runtime, "checkpoints_authorized", lambda *args: [])
    with pytest.raises(DomainError) as failure:
        service.diagnose(actor, workspace_id, final.intent.journal_id, now=datetime.now(UTC))
    assert failure.value.code == "VALIDATION_RECOVERY_REQUIRED"


def test_forged_persisted_receipt_cannot_create_verdict(journal, monkeypatch):
    from app.development_validation.planner import digest

    app, actor, _, service, workspace_id, request = journal
    final = finalize_fixture(journal)
    # Rewrite a fixture hash chain to prove authentication is independent of it.
    with app.state.repository.session_factory.begin() as session:
        previous = "0" * 64
        for event in session.scalars(
            select(AuditEventRow)
            .where(AuditEventRow.event_type == "development_validation.journal_entry")
            .order_by(AuditEventRow.sequence_number)
        ):
            payload = dict(event.payload["payload"])
            entry = dict(payload["entry"])
            entry["previous_digest"] = previous
            if entry["kind"] == "step_result":
                entry["receipt"] = {**entry["receipt"], "authentication_tag": "f" * 64}
            entry["entry_digest"] = digest({k: v for k, v in entry.items() if k != "entry_digest"})
            previous = entry["entry_digest"]
            event.payload = {**event.payload, "payload": {**payload, "entry": entry}}
    with pytest.raises(DomainError):
        service.record_verdict(
            actor,
            workspace_id,
            request,
            final.intent.journal_id,
            expected_journal_digest=final.digest,
        )
    assert verdict_events(app) == []
    # Isolate receipt authentication from the independently failing checkpoint
    # check. Even a fixture accepting the rewritten chain cannot accept its tag.
    monkeypatch.setattr(service, "_checkpoint", lambda *args: (object(), "fixture", {}))
    with pytest.raises(ValueError, match="unauthenticated"):
        service.diagnose(actor, workspace_id, final.intent.journal_id, now=datetime.now(UTC))


def next_fixture_attempt(journal):
    from app.models.agent_runtime import (
        FailAttemptCommand,
        RequestRecoveryPlanCommand,
        UnblockAgentRunCommand,
    )

    _, actor, _, service, _, request = journal
    runtime = service.runtime
    for index, command_type in enumerate(
        (
            FailAttemptCommand,
            RequestRecoveryPlanCommand,
            UnblockAgentRunCommand,
            BeginAttemptCommand,
            StartAttemptCommand,
        )
    ):
        current = runtime.read_run_authorized("run-1", actor)
        fields = dict(
            run_id="run-1",
            command_id=f"retry-{current.attempt_count}-{index}",
            expected_run_version=current.version,
            timestamp=datetime.now(UTC),
        )
        if command_type is FailAttemptCommand:
            fields.update(failure_category="dependency", failure_detail="Fixture unavailable")
        if command_type is BeginAttemptCommand:
            fields.update(executor_reference=request.worker_id)
            fields.update(
                resume_from_checkpoint_id=runtime.checkpoints_authorized("run-1", actor)[
                    -1
                ].checkpoint_id
            )
        runtime.handle_authorized(command_type(**fields), actor)


@pytest.mark.parametrize("old_failure", [None, "watchdog"])
def test_current_uncertain_journal_cannot_inherit_previous_attempt_result(journal, old_failure):
    _, actor, _, service, workspace_id, request = journal
    finalize_fixture(journal, failure_kind=old_failure, diagnostic_codes=("timeout",))
    next_fixture_attempt(journal)
    final = finalize_fixture(journal, uncertain=True)
    diagnosis = service.diagnose(
        actor, workspace_id, final.intent.journal_id, now=datetime.now(UTC)
    )
    finding = diagnosis.findings[0]
    assert finding.disposition == "missing" and finding.recommendation == "gather_evidence"
    assert finding.reproduction is None
    assert len(finding.supporting_receipt_digests) == 1
    verdict = service.record_verdict(
        actor,
        workspace_id,
        request,
        final.intent.journal_id,
        expected_journal_digest=final.digest,
    )
    assert verdict.repair.findings[0].disposition == "missing"
    assert not verdict.assessment.ready
    assert finding.gate in verdict.assessment.missing


def test_persisted_failed_attempts_consume_retry_budget_and_old_owner_cannot_record(journal):
    app, actor, _, service, workspace_id, request = journal
    first = finalize_fixture(journal, failure_kind="watchdog", diagnostic_codes=("timeout",))
    for expected_remaining in (1, 0):
        next_fixture_attempt(journal)
        final = finalize_fixture(journal, failure_kind="watchdog", diagnostic_codes=("timeout",))
        diagnosis = service.diagnose(
            actor, workspace_id, final.intent.journal_id, now=datetime.now(UTC)
        )
        finding = diagnosis.findings[0]
        assert finding.retries_remaining == expected_remaining
        assert len(finding.supporting_receipt_digests) == 3 - expected_remaining
        assert finding.recommendation == (
            "request_bounded_retry" if expected_remaining else "stop_retrying"
        )
    with pytest.raises(DomainError) as failure:
        service.record_verdict(
            actor,
            workspace_id,
            request,
            first.intent.journal_id,
            expected_journal_digest=first.digest,
        )
    assert failure.value.code == "VALIDATION_OWNER_MISMATCH"
    assert verdict_events(app) == []


@pytest.mark.parametrize("authority", ["stop", "lease", "approval"])
def test_verdict_requires_live_authority(journal, authority):
    app, actor, _, service, workspace_id, request = journal
    final = finalize_fixture(journal)
    if authority == "stop":
        with app.state.repository.session_factory.begin() as session:
            session.get(SystemStateRow, 1).emergency_stop = True
    elif authority == "lease":
        with app.state.repository.session_factory.begin() as session:
            session.get(TaskLeaseRow, final.intent.owner.task_id).lease_token = "other"
    else:
        request = request.model_copy(update={"approval_id": "missing"})
    with pytest.raises(DomainError):
        service.record_verdict(
            actor,
            workspace_id,
            request,
            final.intent.journal_id,
            expected_journal_digest=final.digest,
        )
    assert verdict_events(app) == []


def test_historical_verdict_never_replaces_fresh_assessment(journal):
    from datetime import timedelta

    _, actor, _, service, workspace_id, request = journal
    final = finalize_fixture(journal)
    recorded = service.record_verdict(
        actor,
        workspace_id,
        request,
        final.intent.journal_id,
        expected_journal_digest=final.digest,
    )
    future = recorded.observed_at + timedelta(seconds=3601)
    assert recorded.assessment.ready
    assert service.read_verdict(actor, workspace_id, final.intent.journal_id) == recorded
    current = service.assess(actor, workspace_id, final.intent.journal_id, now=future)
    assert not current.ready and current.stale
    diagnosis = service.diagnose(actor, workspace_id, final.intent.journal_id, now=future)
    assert all(f.disposition == "stale" for f in diagnosis.findings)


def test_tampered_verdict_cannot_be_returned_as_durable_evidence(journal):
    app, actor, _, service, workspace_id, request = journal
    final = finalize_fixture(journal)
    service.record_verdict(
        actor,
        workspace_id,
        request,
        final.intent.journal_id,
        expected_journal_digest=final.digest,
    )
    with app.state.repository.session_factory.begin() as session:
        event = session.get(AuditEventRow, verdict_events(app)[0].id)
        payload = dict(event.payload["payload"])
        verdict = {**payload["verdict"], "current_code_state_hash": "f" * 64}
        event.payload = {**event.payload, "payload": {**payload, "verdict": verdict}}
    with pytest.raises(DomainError) as failure:
        service.read_verdict(actor, workspace_id, final.intent.journal_id)
    assert failure.value.code == "VALIDATION_VERDICT_INVALID"
