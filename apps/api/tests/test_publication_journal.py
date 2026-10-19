"""Real SQLite/outbox ledger tests with an explicit fixture-only owner/runtime."""

from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.agent_runtime.authorization import RuntimeActorContext
from app.core.errors import DomainError
from app.db.models import AuditEventRow, OutboxEventRow, TaskRow
from app.github_publication.journal import EVENT, PublicationJournal
from app.main import create_app
from app.models.identity import CreateAgentRequest
from app.models.publication_lifecycle import PublicationPlan, PublicationRequest
from app.self_build.policy import digest
from tests.test_publication_lifecycle import plan as sample_plan


@pytest.fixture
def ledger(tmp_path):
    app = create_app(database_url="sqlite:///" + (tmp_path / "ledger.db").as_posix(), delay_ms=1)
    with TestClient(app):
        identity = app.state.identity_service.create_agent(
            CreateAgentRequest(
                stable_key="publication-fixture",
                display_name="Publication fixture",
                agent_type="worker",
            )
        )
        actor = RuntimeActorContext(actor_id=identity.id, stable_key="publication-fixture")
        repository = app.state.self_build_workspace_service.repository
        with repository.sessions() as session:
            task_id = session.scalar(select(TaskRow.id).limit(1))
        payload = sample_plan().model_dump(mode="json", exclude={"plan_hash"})
        payload.update(task_id=task_id, actor_id=actor.actor_id)
        approved = PublicationPlan(**payload, plan_hash=digest(payload))
        row = NS(
            id=approved.workspace_id,
            task_id=task_id,
            actor_id=actor.actor_id,
            runtime_run_id=approved.runtime_run_id,
        )
        allowed = [True]

        class Runtime:
            def __init__(self):
                self.values = []
                self.lose_ack = False

            def checkpoints_authorized(self, run_id, principal):
                return self.values

            def read_run_authorized(self, run_id, principal):
                return NS(version=1)

            def handle_authorized(self, command, principal, *, execution_fence, commit_guard):
                # Fixture-only runtime, not a production authority or receipt.
                assert execution_fence.task_id == task_id
                assert execution_fence.worker_id == "worker"
                with repository.leases._write() as session:
                    commit_guard(session)
                self.values.append(
                    NS(
                        checkpoint_id=command.checkpoint_id,
                        run_id=command.run_id,
                        attempt_id=command.attempt_id,
                        integrity_digest=command.integrity_digest,
                        resume_cursor=command.resume_cursor,
                        state_reference=command.state_reference,
                        metadata=command.checkpoint_metadata,
                    )
                )
                if self.lose_ack:
                    self.lose_ack = False
                    raise RuntimeError("lost checkpoint acknowledgement")

        runtime = Runtime()
        wrapped = NS(
            sessions=repository.sessions,
            leases=repository.leases,
            lookup=lambda session, workspace: row,
        )
        native = NS(
            repository=wrapped,
            preparation=NS(
                runtime=runtime,
                git=app.state.self_build_git_service,
                workspace=NS(authorize=lambda *args: None),
            ),
        )

        def live(principal, request, expected, session):
            if not allowed[0] or expected != approved:
                raise DomainError("PUBLICATION_REVOKED", "Fixture authority revoked.", 403)
            return row

        service = NS(native=native, live=live)
        request = PublicationRequest(
            command_id="publication-command",
            expected_plan_hash=approved.plan_hash,
            approval_id="approval",
            worker_id="worker",
            lease_token="fixture-lease",
        )
        yield NS(
            journal=PublicationJournal(service),
            actor=actor,
            request=request,
            plan=approved,
            row=row,
            repository=repository,
            runtime=runtime,
            allowed=allowed,
        )


def pins(*, check_authority=True):
    # No native effect uses this fixture. Real retained pins are tested separately.
    return None


def prepared(c):
    return c.journal.append(c.actor, c.request, "publication-operation", c.plan, "prepared", pins)


def test_audit_outbox_and_lost_checkpoint_ack_reconcile_one_exact_entry(ledger):
    c = ledger
    before = prepared(c)
    assert c.journal.checkpoint(c.actor, before)[0] is None
    c.runtime.lose_ack = True
    with pytest.raises(RuntimeError, match="lost checkpoint acknowledgement"):
        c.journal.acknowledge(c.actor, c.request, before, pins)
    assert c.journal.acknowledge(c.actor, c.request, before, pins) == before
    assert c.journal.acknowledge(c.actor, c.request, before, pins) == before
    assert len(c.runtime.values) == 1
    with c.repository.sessions() as session:
        events = list(
            session.scalars(select(AuditEventRow).where(AuditEventRow.event_type == EVENT))
        )
        assert len(events) == 1
        assert session.scalar(select(func.count()).select_from(OutboxEventRow)) > 0
    advanced = c.journal.append(
        c.actor,
        c.request,
        before.operation_id,
        c.plan,
        "push_intent",
        pins,
        expected=before,
        request_digest="a" * 64,
    )
    assert advanced.state == "pushing"
    with pytest.raises(DomainError):
        c.journal.append(
            c.actor,
            c.request,
            before.operation_id,
            c.plan,
            "push_intent",
            pins,
            expected=before,
            request_digest="b" * 64,
        )
    assert c.journal.read(c.actor, c.plan.workspace_id, before.operation_id) == advanced


def test_forged_native_checkpoint_fields_never_acknowledge_audit_state(ledger):
    c = ledger
    value = prepared(c)
    c.journal.acknowledge(c.actor, c.request, value, pins)
    original = c.runtime.values[0]
    for field, foreign in (
        ("run_id", "foreign"),
        ("attempt_id", "foreign"),
        ("integrity_digest", "sha256:" + "f" * 64),
        ("resume_cursor", "completed"),
        ("state_reference", "foreign"),
        ("metadata", {}),
    ):
        changed = NS(**vars(original))
        setattr(changed, field, foreign)
        c.runtime.values[0] = changed
        with pytest.raises(DomainError) as error:
            c.journal.checkpoint(c.actor, value)
        assert error.value.code == "GITHUB_PUBLICATION_JOURNAL_INVALID"
    c.runtime.values[0] = original
    assert c.journal.checkpoint(c.actor, value)[0] == original


def test_revocation_before_native_checkpoint_leaves_inspectable_intent(ledger):
    c = ledger
    value = prepared(c)
    c.allowed[0] = False
    with pytest.raises(DomainError):
        c.journal.acknowledge(c.actor, c.request, value, pins)
    assert not c.runtime.values
    assert c.journal.read(c.actor, c.plan.workspace_id, value.operation_id) == value


def test_source_guard_failure_rolls_back_both_audit_and_outbox(ledger):
    c = ledger
    value = prepared(c)
    with c.repository.sessions() as session:
        count = session.scalar(select(func.count()).select_from(OutboxEventRow))
    calls = [0]

    def changed(*, check_authority=True):
        calls[0] += 1
        if calls[0] == 2:
            raise DomainError("SOURCE_CHANGED", "Fixture source changed.", 409)

    with pytest.raises(DomainError):
        c.journal.append(
            c.actor,
            c.request,
            value.operation_id,
            c.plan,
            "push_intent",
            changed,
            expected=value,
            request_digest="a" * 64,
        )
    assert c.journal.read(c.actor, c.plan.workspace_id, value.operation_id) == value
    with c.repository.sessions() as session:
        assert session.scalar(select(func.count()).select_from(OutboxEventRow)) == count
