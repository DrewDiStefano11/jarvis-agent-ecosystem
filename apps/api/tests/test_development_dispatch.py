"""Real native admission/dispatch recovery; deterministic responses are not coding proof."""

import json
from datetime import timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.core.errors import DomainError
from app.db.models import AuditEventRow, OutboxEventRow, SystemStateRow, TaskRow
from app.development_agent.dispatch import DevelopmentDispatchService
from app.development_agent.repository import DevelopmentAdmissionRow, DevelopmentDispatchRow
from app.model_providers.contracts import ModelExecutionResponse
from app.models.development_dispatch import DevelopmentLimits
from app.models.identity import AssignPermissionRequest
from app.self_build.policy import digest
from tests.test_development_agent import proposal

pytest_plugins = ["tests.test_development_agent"]


def limits(**changes):
    return DevelopmentLimits(
        provider="openai-development",
        model="coding-model",
        completion_criteria=("The accessor returns the configured value.",),
        **changes,
    )


def admit(development, **changes):
    app, actor, operator, authority, _, _, _, context = development
    service = DevelopmentDispatchService(app)
    policy = limits(**changes)
    plan = service.preview(actor, context.workspace_id, authority, context.selection, policy)
    root = service.admit(
        operator, actor, context.workspace_id, authority, context.selection, policy, plan.plan_hash
    )
    return service, root


def test_request_dependency_upgrade_invalidates_persisted_admission(development, monkeypatch):
    from pathlib import Path

    from app.development_agent import dispatch

    _, actor, _, authority, _, _, _, context = development
    service, admitted = admit(development)
    original_digest = dispatch.POLICY_DIGEST
    original_read = Path.read_text
    source_root = Path(dispatch.__file__).resolve().parents[1]
    for name in (
        "development_agent/context.py",
        "development_agent/provider.py",
        "development_agent/execution.py",
        "development_agent/config.py",
        "development_agent/review.py",
        "models/development_review.py",
        "development_agent/lineages.py",
        "development_agent/corrections.py",
        "models/development_lineage.py",
        "models/development_agent.py",
        "models/context.py",
        "models/domain.py",
        "context/assembler.py",
        "context/security.py",
        "context/budget.py",
        "model_providers/tool_calls.py",
        "model_providers/policy.py",
        "source_edit_planning/service.py",
        "source_mutation/service.py",
    ):
        changed_path = source_root / name

        def upgraded(path, *args, _changed=changed_path, **kwargs):
            text = original_read(path, *args, **kwargs)
            return text + "\n# simulated deployment change\n" if path == _changed else text

        monkeypatch.setattr(Path, "read_text", upgraded)
        changed_digest = dispatch.policy_digest()
        assert changed_digest != original_digest, name
        monkeypatch.setattr(dispatch, "POLICY_DIGEST", changed_digest)
        with service.repository.sessions() as session:
            row = service.repository.lookup(session, context.workspace_id)
            with pytest.raises(DomainError) as error:
                service.root(session, actor, row, authority)
        assert error.value.code == "DEVELOPMENT_ADMISSION_CONFLICT"
    monkeypatch.setattr(dispatch, "POLICY_DIGEST", original_digest)
    with service.repository.sessions() as session:
        row = service.repository.lookup(session, context.workspace_id)
        assert service.root(session, actor, row, authority)[0] == admitted


def test_live_assembler_limits_are_frozen_in_admission(development, monkeypatch):
    from app.models.development_dispatch import AdmissionPlan

    app, actor, _, authority, _, _, _, context = development
    service, admitted = admit(development)
    assembler = app.state.context_assembler
    assert AdmissionPlan.model_json_schema()["properties"]["assembly_limits"]
    assert admitted.plan.assembly_limits.maximum_tokens == assembler.maximum_tokens
    assert (
        admitted.plan.assembly_limits.maximum_total_characters == assembler.maximum_total_characters
    )
    for name in type(admitted.plan.assembly_limits).model_fields:
        original = getattr(assembler, name)
        changed = not original if type(original) is bool else original + 1
        monkeypatch.setattr(assembler, name, changed)
        # A new service models restart against the same persisted root with new settings.
        restarted_service = DevelopmentDispatchService(app)
        with restarted_service.repository.sessions() as session:
            row = restarted_service.repository.lookup(session, context.workspace_id)
            with pytest.raises(DomainError) as error:
                restarted_service.root(session, actor, row, authority)
        assert error.value.code == "DEVELOPMENT_ADMISSION_CONFLICT", name
        monkeypatch.setattr(assembler, name, original)
    with service.repository.sessions() as session:
        row = service.repository.lookup(session, context.workspace_id)
        assert service.root(session, actor, row, authority)[0] == admitted


def test_assembler_change_during_request_rolls_back_dispatch(development, monkeypatch):
    app, actor, _, authority, _, _, _, context = development
    service, _ = admit(development)
    assembler = app.state.context_assembler
    assemble = assembler.assemble

    def replace_limits(*args, **kwargs):
        result = assemble(*args, **kwargs)
        assembler.maximum_total_characters += 1
        return result

    monkeypatch.setattr(assembler, "assemble", replace_limits)
    with pytest.raises(DomainError) as error:
        service.reserve(actor, context.workspace_id, authority, context.selection, "changed-policy")
    assert error.value.code == "DEVELOPMENT_ADMISSION_CONFLICT"
    with service.repository.sessions() as session:
        assert list(session.scalars(select(DevelopmentDispatchRow))) == []
        assert (
            list(
                session.scalars(
                    select(AuditEventRow).where(
                        AuditEventRow.event_type == "development.dispatch.reserved"
                    )
                )
            )
            == []
        )
        assert (
            list(
                session.scalars(
                    select(OutboxEventRow).where(
                        OutboxEventRow.event_type == "development.dispatch.reserved"
                    )
                )
            )
            == []
        )


def result(record, **changes):
    data = dict(
        content=proposal(record.reservation.context).model_dump_json(),
        provider="openai-development",
        model="coding-model",
        input_tokens=80,
        output_tokens=90,
        total_tokens=170,
        usage_quality="exact",
        latency_ms=5,
        finish_reason="stop",
        task_id=record.reservation.context.task_id,
        correlation_id=record.reservation.dispatch_id,
        request_id="resp_native-test",
    )
    data.update(changes)
    return ModelExecutionResponse(**data)


@pytest.mark.parametrize(
    "changes",
    [
        {"maximum_dispatches": True},
        {"maximum_dispatches": 9},
        {"output_tokens_per_dispatch": 16385},
        {"maximum_seconds": 3601},
        {"maximum_reserved_output_tokens": 4000},
        {"completion_criteria": ()},
    ],
)
def test_limits_are_strict_and_bounded(changes):
    data = limits().model_dump()
    data.update(changes)
    with pytest.raises(ValidationError):
        DevelopmentLimits(**data)


def test_native_dispatch_response_restart_and_one_root_budget(development):
    app, actor, operator, authority, _, target, _, context = development
    service, root = admit(development)
    args = (actor, context.workspace_id, authority, context.selection)
    record, fresh = service.reserve(*args, "iteration-1")
    assert fresh and record.reservation.ordinal == 1
    replay, fresh = service.reserve(*args, "iteration-1")
    assert not fresh and replay == record
    assert "value = 1" not in repr(record)
    assert record.reservation.request.metadata["context_hash"] == context.context_hash
    assert record.reservation.request.output_schema.name == "development_proposal_v1"
    assert not record.reservation.request.tools
    assert record.reservation.context_manifest["includedSources"]
    response = result(record)
    received = service.receive(
        actor, context.workspace_id, authority, record.reservation.dispatch_id, response
    )
    assert received.response == response
    assert (
        service.receive(
            actor, context.workspace_id, authority, record.reservation.dispatch_id, response
        )
        == received
    )

    from app.main import create_app

    restarted = create_app(database_url=app.state.settings.database_url)
    try:
        # Reapply the same trusted operator configuration; credentials and machine
        # paths are not model evidence or development payloads.
        for key in (
            "self_build_enabled",
            "self_build_repositories_json",
            "self_build_git_executable",
            "self_build_git_sha256",
        ):
            setattr(restarted.state.settings, key, getattr(app.state.settings, key))
        from types import SimpleNamespace

        from pydantic import SecretStr

        from app.remote_control.access import RemoteControlAccess

        restarted.state.remote_control_service = SimpleNamespace(
            access=RemoteControlAccess(
                restarted.state.identity_service,
                operator.actor_id,
                SecretStr("x" * 48),
            )
        )
        recovered = DevelopmentDispatchService(restarted)
        assert (
            recovered.read(actor, context.workspace_id, authority, record.reservation.dispatch_id)
            == received
        )
        second, fresh = recovered.reserve(*args, "iteration-2")
        assert fresh and second.reservation.ordinal == 2
        recovered.receive(
            actor, context.workspace_id, authority, second.reservation.dispatch_id, result(second)
        )
        with pytest.raises(DomainError) as error:
            recovered.reserve(*args, "iteration-3")
        assert error.value.code == "DEVELOPMENT_BUDGET_EXHAUSTED"
        with recovered.repository.sessions() as session:
            assert session.get(DevelopmentAdmissionRow, context.task_id).payload == root.model_dump(
                mode="json"
            )
            assert len(session.scalars(select(DevelopmentDispatchRow)).all()) == 2
    finally:
        restarted.state.engine.dispose()
    with service.repository.sessions() as session:
        audits = session.scalars(
            select(AuditEventRow).where(AuditEventRow.event_type.like("development.%"))
        ).all()
        outbox = session.scalars(
            select(OutboxEventRow).where(OutboxEventRow.event_type.like("development.%"))
        ).all()
        assert {event.sequence_number for event in audits} == {
            event.sequence_number for event in outbox
        }
        payload = json.dumps([event.payload for event in audits])
        assert "value = 1" not in payload and "value = 2" not in payload
        assert authority.lease_token not in payload and str(target) not in payload
    checkpoints = service.runtime.checkpoints_authorized(context.runtime_run_id, actor)
    assert (
        len([cp for cp in checkpoints if cp.metadata.get("schemaName") == "development-dispatch-1"])
        == 4
    )
    assert (target / "source.py").read_bytes() == b"value = 1\n"


def test_all_checkpoint_crash_gaps_reuse_records_and_uncertain_calls_never_repeat(
    development, monkeypatch
):
    app, actor, operator, authority, _, _, _, context = development
    service = DevelopmentDispatchService(app)
    policy = limits()
    plan = service.preview(actor, context.workspace_id, authority, context.selection, policy)
    acknowledge = service.acknowledge

    def crash(*args, **kwargs):
        raise RuntimeError("simulated checkpoint gap")

    monkeypatch.setattr(service, "acknowledge", crash)
    with pytest.raises(RuntimeError):
        service.admit(
            operator,
            actor,
            context.workspace_id,
            authority,
            context.selection,
            policy,
            plan.plan_hash,
        )
    monkeypatch.setattr(service, "acknowledge", acknowledge)
    root = service.admit(
        operator, actor, context.workspace_id, authority, context.selection, policy, plan.plan_hash
    )
    assert root.approval_id

    args = (actor, context.workspace_id, authority, context.selection)
    monkeypatch.setattr(service, "acknowledge", crash)
    with pytest.raises(RuntimeError):
        service.reserve(*args, "uncertain-1")
    monkeypatch.setattr(service, "acknowledge", acknowledge)
    record, fresh = service.reserve(*args, "uncertain-1")
    assert not fresh
    with pytest.raises(DomainError) as error:
        service.reserve(*args, "uncertain-2")
    assert error.value.code == "DEVELOPMENT_DISPATCH_OUTCOME_UNKNOWN"

    # A caller that retained the actual result can persist it; no model replay.
    monkeypatch.setattr(service, "acknowledge", crash)
    response = result(record)
    with pytest.raises(RuntimeError):
        service.receive(
            actor, context.workspace_id, authority, record.reservation.dispatch_id, response
        )
    monkeypatch.setattr(service, "acknowledge", acknowledge)
    received = service.receive(
        actor, context.workspace_id, authority, record.reservation.dispatch_id, response
    )
    assert received.response == response
    assert not service.reserve(*args, "uncertain-1")[1]
    assert service.reserve(*args, "next-authorized-iteration")[0].reservation.ordinal == 2


def test_admission_is_independent_exact_and_cannot_reset_budget_or_objective(development):
    app, actor, operator, authority, _, _, _, context = development
    service = DevelopmentDispatchService(app)
    policy = limits()
    plan = service.preview(actor, context.workspace_id, authority, context.selection, policy)
    for principal, expected in ((actor, plan.plan_hash), (operator, "0" * 64)):
        with pytest.raises(DomainError):
            service.admit(
                principal,
                actor,
                context.workspace_id,
                authority,
                context.selection,
                policy,
                expected,
            )
    with service.repository.sessions() as session:
        assert session.get(DevelopmentAdmissionRow, context.task_id) is None
    service.admit(
        operator, actor, context.workspace_id, authority, context.selection, policy, plan.plan_hash
    )
    changed = limits(maximum_dispatches=8)
    changed_plan = service.preview(
        actor, context.workspace_id, authority, context.selection, changed
    )
    with pytest.raises(DomainError) as error:
        service.admit(
            operator,
            actor,
            context.workspace_id,
            authority,
            context.selection,
            changed,
            changed_plan.plan_hash,
        )
    assert error.value.code == "DEVELOPMENT_ADMISSION_CONFLICT"
    with service.repository.leases._write() as session:
        session.get(TaskRow, context.task_id).original_request = "A replacement objective"
    with pytest.raises(DomainError) as error:
        service.reserve(
            actor, context.workspace_id, authority, context.selection, "changed-objective"
        )
    assert error.value.code == "DEVELOPMENT_ADMISSION_CONFLICT"


def test_changed_context_keeps_root_allowance_and_pending_response_refuses(development):
    _, actor, _, authority, _, target, reader, context = development
    service, _ = admit(development, maximum_dispatches=3, maximum_reserved_output_tokens=12288)
    args = (actor, context.workspace_id, authority, context.selection)
    first, _ = service.reserve(*args, "first")
    service.receive(
        actor, context.workspace_id, authority, first.reservation.dispatch_id, result(first)
    )
    (target / "source.py").write_bytes(b"value = 3\n")
    changed = reader.inspect(actor, context.workspace_id, authority, context.selection)
    assert changed.context_hash != context.context_hash
    second, fresh = service.reserve(*args, "second")
    assert fresh and second.reservation.ordinal == 2
    response = result(second)
    service.receive(
        actor, context.workspace_id, authority, second.reservation.dispatch_id, response
    )
    from app.development_agent.proposals import DevelopmentProposalService
    from app.models.development_agent import DevelopmentProposal

    with pytest.raises(DomainError) as error:
        DevelopmentProposalService(service.app).persist(
            actor,
            context.workspace_id,
            authority,
            context.selection,
            "stale-source-plan",
            DevelopmentProposal.model_validate_json(response.content),
        )
    assert error.value.code == "DEVELOPMENT_BEFORE_CONFLICT"
    third, fresh = service.reserve(*args, "third-repair")
    assert fresh and third.reservation.ordinal == 3
    service.receive(
        actor, context.workspace_id, authority, third.reservation.dispatch_id, result(third)
    )
    with pytest.raises(DomainError) as error:
        service.reserve(*args, "fourth")
    assert error.value.code == "DEVELOPMENT_BUDGET_EXHAUSTED"
    with pytest.raises(DomainError) as error:
        service.reserve(*args, "first")
    assert error.value.code == "DEVELOPMENT_CONTEXT_STALE"


@pytest.mark.parametrize("boundary", ["permission", "stop"])
def test_late_response_is_fenced_by_revoked_permission_and_stop(development, boundary):
    app, actor, _, authority, permission, _, _, context = development
    service, _ = admit(development)
    record, _ = service.reserve(actor, context.workspace_id, authority, context.selection, "late")
    if boundary == "permission":
        app.state.identity_service.assign_permission(
            actor.actor_id, AssignPermissionRequest(effect="deny", **permission)
        )
    else:
        with service.repository.leases._write() as session:
            session.get(SystemStateRow, 1).emergency_stop = True
    with pytest.raises(DomainError):
        service.receive(
            actor, context.workspace_id, authority, record.reservation.dispatch_id, result(record)
        )
    with service.repository.sessions() as session:
        stored = session.get(DevelopmentDispatchRow, record.reservation.dispatch_id)
        assert stored.payload["response"] is None


def test_tampered_protected_request_refuses_even_with_recomputed_projection_hash(development):
    _, actor, _, authority, _, _, _, context = development
    service, _ = admit(development)
    record, _ = service.reserve(actor, context.workspace_id, authority, context.selection, "tamper")
    with service.repository.leases._write() as session:
        stored = session.get(DevelopmentDispatchRow, record.reservation.dispatch_id)
        payload = json.loads(json.dumps(stored.payload))
        payload["reservation"]["request"]["messages"][0]["content"] = "Replacement policy"
        payload["reservation"]["request_digest"] = digest(payload["reservation"]["request"])
        stored.payload = payload
        stored.integrity_digest = digest(payload)
    with pytest.raises(DomainError) as error:
        service.read(actor, context.workspace_id, authority, record.reservation.dispatch_id)
    assert error.value.code == "DEVELOPMENT_AUDIT_INVALID"


def test_dispatch_deadline_and_original_lease_are_enforced(development, monkeypatch):
    _, actor, _, authority, _, _, _, context = development
    service, root = admit(development)
    with pytest.raises(DomainError):
        service.reserve(
            actor,
            context.workspace_id,
            authority.model_copy(update={"lease_token": "foreign"}),
            context.selection,
            "foreign",
        )

    import app.development_agent.dispatch as dispatch

    class FutureDateTime:
        @staticmethod
        def now(tz):
            return root.admitted_at + timedelta(seconds=root.plan.limits.maximum_seconds + 1)

    monkeypatch.setattr(dispatch, "datetime", FutureDateTime)
    with pytest.raises(DomainError) as error:
        service.reserve(actor, context.workspace_id, authority, context.selection, "deadline")
    assert error.value.code == "DEVELOPMENT_BUDGET_EXHAUSTED"


def test_budget_deadline_is_rechecked_at_commit_and_missing_root_checkpoint_refuses(
    development, monkeypatch
):
    _, actor, _, authority, _, _, _, context = development
    service, root = admit(development)
    import app.development_agent.dispatch as dispatch

    original_datetime = dispatch.datetime
    original_builder = dispatch.request_builder.build_request

    class FutureDateTime:
        @staticmethod
        def now(tz):
            return root.admitted_at + timedelta(seconds=root.plan.limits.maximum_seconds + 1)

    def delayed_request(*args, **kwargs):
        built = original_builder(*args, **kwargs)
        monkeypatch.setattr(dispatch, "datetime", FutureDateTime)
        return built

    monkeypatch.setattr(dispatch.request_builder, "build_request", delayed_request)
    with pytest.raises(DomainError) as error:
        service.reserve(
            actor, context.workspace_id, authority, context.selection, "expired-during-build"
        )
    assert error.value.code == "DEVELOPMENT_BUDGET_EXHAUSTED"
    with service.repository.sessions() as session:
        assert not session.scalars(select(DevelopmentDispatchRow)).all()
    monkeypatch.setattr(dispatch, "datetime", original_datetime)
    monkeypatch.setattr(dispatch.request_builder, "build_request", original_builder)
    record, _ = service.reserve(actor, context.workspace_id, authority, context.selection, "valid")

    checkpoints = service.runtime.checkpoints_authorized
    monkeypatch.setattr(
        service.runtime,
        "checkpoints_authorized",
        lambda run, principal: [
            cp
            for cp in checkpoints(run, principal)
            if cp.metadata.get("schemaName") != "development-admission-1"
        ],
    )
    with pytest.raises(DomainError) as error:
        service.receive(
            actor, context.workspace_id, authority, record.reservation.dispatch_id, result(record)
        )
    assert error.value.code == "DEVELOPMENT_CHECKPOINT_REQUIRED"
    monkeypatch.setattr(service.runtime, "checkpoints_authorized", checkpoints)

    original_event = service.creation.git.event

    def delayed_event(session, row, principal, event_type, payload):
        event = original_event(session, row, principal, event_type, payload)
        if event_type == "development.dispatch.received":
            monkeypatch.setattr(dispatch, "datetime", FutureDateTime)
        return event

    monkeypatch.setattr(service.creation.git, "event", delayed_event)
    with pytest.raises(DomainError) as error:
        service.receive(
            actor, context.workspace_id, authority, record.reservation.dispatch_id, result(record)
        )
    assert error.value.code == "DEVELOPMENT_BUDGET_EXHAUSTED"
    with service.repository.sessions() as session:
        assert (
            session.get(DevelopmentDispatchRow, record.reservation.dispatch_id).payload["response"]
            is None
        )
