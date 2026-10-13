"""Real native pipeline with mocked provider transport; not real-model quality proof."""

import asyncio
import json
import re
from hashlib import sha256

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.core.errors import DomainError
from app.db.models import AuditEventRow, SystemStateRow
from app.development_agent.config import DevelopmentProviderSettings
from app.development_agent.execution import DevelopmentExecutor
from app.development_agent.provider import OpenAIProposalProvider
from app.development_agent.repository import DevelopmentDispatchRow
from app.model_providers.registry import ProviderRegistry
from app.model_providers.retry import RetryExecutor, RetryPolicy
from app.model_providers.router import ModelRouter
from app.models.identity import AssignPermissionRequest, CreatePermissionRequest
from app.models.source_mutation import ApplySourceMutation, ApproveSourceMutation
from app.source_edit_planning.repository import SourceEditingPlanRow
from tests.test_development_dispatch import admit

pytest_plugins = ["tests.test_development_agent"]

KEY = "unit-private-execution-key"


def output(request):
    body = json.loads(request.content)
    text = body["input"][-1]["content"]
    context_hash = re.search(r"exact context_hash is ([a-f0-9]{64})", text)[1]
    proposal = dict(
        schema_version="1.0",
        context_hash=context_hash,
        summary="Expose the current value from a reusable module.",
        completion_criteria=["The accessor returns the current configured value."],
        files=[
            dict(
                operation="modify",
                path="source.py",
                before_digest=sha256(b"value = 1\n").hexdigest(),
                text="value = 2\n",
            ),
            dict(
                operation="add",
                path="new.py",
                before_digest=None,
                text="from source import value\n\ndef current_value():\n    return value\n",
            ),
        ],
    )
    return dict(
        id="resp_execution-test",
        model="coding-model",
        status="completed",
        output=[
            dict(
                type="message",
                role="assistant",
                status="completed",
                content=[dict(type="output_text", text=json.dumps(proposal))],
            )
        ],
        usage=dict(input_tokens=100, output_tokens=200, total_tokens=300),
    )


def executor(app, client, *, enabled=True):
    settings = DevelopmentProviderSettings(
        enabled=enabled,
        provider="openai",
        model="coding-model",
        openai_api_key=SecretStr(KEY),
        timeout_seconds=3,
    )
    assert isinstance(client._transport, httpx.MockTransport)
    engine = OpenAIProposalProvider(
        api_key=SecretStr(KEY),
        default_model="coding-model",
        enabled=True,
        timeout_seconds=3,
    )
    engine._client = client
    router = ModelRouter(ProviderRegistry([engine]), RetryExecutor(RetryPolicy(maximum_attempts=1)))

    class FixtureExecutor(DevelopmentExecutor):
        def enabled(self):
            if not self.settings.enabled:
                raise DomainError("DEVELOPMENT_EXECUTION_DISABLED", "Disabled", 409)

        def route(self, root):
            requirements = super().route(root)
            requirements.allow_remote = True  # MockTransport only; no network transport.
            return requirements

    service = FixtureExecutor(app, settings, router)
    service.monitor_interval_seconds = 0.01
    return service


def grant_editing(app, actor, operator):
    permission = app.state.identity_service.create_definition(
        "permission",
        CreatePermissionRequest(
            stable_key="self_build.workspace.source_edit",
            display_name="Edit reviewed development source",
            resource_type="task",
            action="source_edit",
        ),
    )
    for principal in (actor, operator):
        app.state.identity_service.assign_permission(
            principal.actor_id,
            AssignPermissionRequest(
                permission_id=permission.id,
                effect="allow",
                resource_type="task",
                resource_id="task-demo",
            ),
        )


@pytest.mark.asyncio
async def test_wire_dispatch_plan_real_mutation_restart_and_replay_do_not_recall_model(development):
    app, actor, operator, authority, _, target, _, context = development
    admit(development)
    calls = []

    async def transport(request):
        calls.append(request)
        return httpx.Response(200, json=output(request))

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), trust_env=False
    ) as client:
        service = executor(app, client)
        args = (actor, context.workspace_id, authority, context.selection, "feature")
        result = await service.execute(*args)
        assert result.state == "awaiting_source_approval" and not result.current_source_measured
        assert await service.execute(*args) == result
        assert len(calls) == 1 and not (target / "new.py").exists()
        stored, editing = service.dispatches.editing(
            actor, context.workspace_id, authority, result.dispatch_id
        )
        assert stored.response.total_tokens == 300
        assert editing.record_id == result.editing_record_id
        grant_editing(app, actor, operator)
        plan = service.mutations.preview(actor, editing.record_id)
        approval = service.mutations.approve(
            operator,
            ApproveSourceMutation(
                editing_record_id=editing.record_id,
                expected_plan_hash=plan.plan_hash,
            ),
        )
        request = ApplySourceMutation(
            editing_record_id=editing.record_id,
            expected_plan_hash=plan.plan_hash,
            approval_id=approval.approval_id,
            worker_id=authority.worker_id,
            lease_token=authority.lease_token,
        )
        with pytest.raises(DomainError):
            service.apply_approved(
                actor,
                context.workspace_id,
                authority,
                result.dispatch_id,
                request.model_copy(update={"approval_id": "foreign"}),
            )
        assert (target / "source.py").read_bytes() == b"value = 1\n"
        mutation = service.apply_approved(
            actor, context.workspace_id, authority, result.dispatch_id, request
        )
        assert (
            mutation.state == "complete" and (target / "source.py").read_bytes() == b"value = 2\n"
        )
        handoff = service.handoff(
            actor, context.workspace_id, authority, result.dispatch_id, request
        )
        assert handoff.source_history.record == mutation
        assert (
            handoff.native_source.source_inspection.code_state == editing.plan.expected_code_state
        )
        assert handoff.requested_validation == editing.plan.validation_plan
        assert not handoff.validation_passed and not handoff.publication_ready
        assert authority.lease_token not in handoff.model_dump_json()
        replay = await service.execute(*args)
        assert replay.state == "mutation_recorded" and len(calls) == 1

        from types import SimpleNamespace

        from app.main import create_app
        from app.remote_control.access import RemoteControlAccess

        restarted = create_app(database_url=app.state.settings.database_url)
        try:
            for key in (
                "self_build_enabled",
                "self_build_repositories_json",
                "self_build_git_executable",
                "self_build_git_sha256",
            ):
                setattr(restarted.state.settings, key, getattr(app.state.settings, key))
            restarted.state.remote_control_service = SimpleNamespace(
                access=RemoteControlAccess(
                    restarted.state.identity_service,
                    operator.actor_id,
                    SecretStr("x" * 48),
                )
            )
            recovered = executor(restarted, client)
            assert (await recovered.execute(*args)).state == "mutation_recorded"
            assert len(calls) == 1 and (target / "new.py").read_bytes().startswith(
                b"from source import value"
            )
        finally:
            restarted.state.engine.dispose()
    with app.state.repository.session_factory() as session:
        audit = session.scalars(
            select(AuditEventRow).where(AuditEventRow.event_type.like("development.%"))
        ).all()
        assert KEY not in json.dumps([event.payload for event in audit])


@pytest.mark.asyncio
async def test_binding_checkpoint_crash_recovers_the_exact_plan_without_provider_recall(
    development, monkeypatch
):
    app, actor, _, authority, _, _, _, context = development
    admit(development)
    calls = []

    def transport(request):
        calls.append(request)
        return httpx.Response(200, json=output(request))

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), trust_env=False
    ) as client:
        service = executor(app, client)
        acknowledge = service.dispatches.acknowledge

        def crash(*args, **kwargs):
            if args[3].editing_binding is not None:
                raise RuntimeError("simulated binding checkpoint gap")
            return acknowledge(*args, **kwargs)

        monkeypatch.setattr(service.dispatches, "acknowledge", crash)
        args = (actor, context.workspace_id, authority, context.selection, "checkpoint-gap")
        with pytest.raises(RuntimeError):
            await service.execute(*args)
        monkeypatch.setattr(service.dispatches, "acknowledge", acknowledge)
        result = await service.execute(*args)
        assert result.editing_record_id and len(calls) == 1
        with service.dispatches.repository.sessions() as session:
            assert len(session.scalars(select(SourceEditingPlanRow)).all()) == 1


@pytest.mark.asyncio
async def test_provider_failure_is_sanitized_durable_and_keeps_the_consumed_slot(development):
    app, actor, _, authority, _, target, _, context = development
    admit(development)
    calls = []

    def transport(request):
        calls.append(request)
        return httpx.Response(
            429, json={"error": {"type": "insufficient_quota", "message": KEY + " private-body"}}
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), trust_env=False
    ) as client:
        service = executor(app, client)
        args = (actor, context.workspace_id, authority, context.selection, "quota")
        with pytest.raises(DomainError) as failure:
            await service.execute(*args)
        assert failure.value.code == "DEVELOPMENT_PROVIDER_FAILED"
        assert failure.value.__cause__ is None and failure.value.__context__ is None
        for command in ("quota", "next"):
            with pytest.raises(DomainError) as error:
                await service.execute(*args[:-1], command)
            assert error.value.code == "DEVELOPMENT_DISPATCH_OUTCOME_UNKNOWN"
        assert len(calls) == 1 and (target / "source.py").read_bytes() == b"value = 1\n"
        with service.dispatches.repository.sessions() as session:
            row = session.scalar(select(DevelopmentDispatchRow))
            assert row.payload["failure"]["category"] == "hard_quota_exhausted"
            assert KEY not in json.dumps(row.payload) and "private-body" not in json.dumps(
                row.payload
            )
            assert not session.scalars(select(SourceEditingPlanRow)).all()


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["stop", "caller_cancel"])
async def test_inflight_cancellation_closes_transport_and_prevents_late_source_plan(
    development, boundary
):
    app, actor, _, authority, _, target, _, context = development
    admit(development)
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def transport(request):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), trust_env=False
    ) as client:
        service = executor(app, client)
        task = asyncio.create_task(
            service.execute(actor, context.workspace_id, authority, context.selection, "cancel")
        )
        await started.wait()
        if boundary == "stop":
            with service.dispatches.repository.leases._write() as session:
                session.get(SystemStateRow, 1).emergency_stop = True
            with pytest.raises(DomainError):
                await task
        else:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert cancelled.is_set()
        with service.dispatches.repository.sessions() as session:
            assert not session.scalars(select(SourceEditingPlanRow)).all()
            stored = session.scalar(select(DevelopmentDispatchRow))
            assert stored.payload["response"] is None
        assert (target / "source.py").read_bytes() == b"value = 1\n"


@pytest.mark.asyncio
async def test_disabled_execution_precedes_reservation_and_transport(development):
    app, actor, _, authority, _, _, _, context = development
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: pytest.fail("network"))
    ) as client:
        service = executor(app, client, enabled=False)
        with pytest.raises(DomainError) as error:
            await service.execute(
                actor, context.workspace_id, authority, context.selection, "disabled"
            )
        assert error.value.code == "DEVELOPMENT_EXECUTION_DISABLED"
        with service.dispatches.repository.sessions() as session:
            assert not session.scalars(select(DevelopmentDispatchRow)).all()


@pytest.mark.asyncio
async def test_source_conflict_preserves_known_response_and_repairs_within_same_budget(development):
    app, actor, _, authority, _, target, _, context = development
    admit(development)
    calls = []

    def transport(request):
        calls.append(request)
        reply = output(request)
        if len(calls) == 1:
            (target / "source.py").write_bytes(b"value = 3\n")
        else:
            proposal = json.loads(reply["output"][0]["content"][0]["text"])
            proposal["files"][0].update(
                before_digest=sha256(b"value = 3\n").hexdigest(), text="value = 4\n"
            )
            reply["output"][0]["content"][0]["text"] = json.dumps(proposal)
        return httpx.Response(200, json=reply)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), trust_env=False
    ) as client:
        service = executor(app, client)
        args = (actor, context.workspace_id, authority, context.selection)
        with pytest.raises(DomainError) as error:
            await service.execute(*args, "stale")
        assert error.value.code == "DEVELOPMENT_CONTEXT_STALE"
        with service.dispatches.repository.sessions() as session:
            record = session.scalar(select(DevelopmentDispatchRow))
            assert record.payload["response"]["total_tokens"] == 300
            assert (
                record.payload["failure"] is None
                and not session.scalars(select(SourceEditingPlanRow)).all()
            )
        repaired = await service.execute(*args, "repair")
        assert repaired.ordinal == 2 and len(calls) == 2
        with pytest.raises(DomainError) as error:
            await service.execute(*args, "exhausted")
        assert error.value.code == "DEVELOPMENT_BUDGET_EXHAUSTED"
        assert len(calls) == 2 and (target / "source.py").read_bytes() == b"value = 3\n"


@pytest.mark.asyncio
async def test_production_executor_refuses_even_an_enabled_injected_router():
    from types import SimpleNamespace

    class Probe(DevelopmentExecutor):
        def __init__(self):
            self.settings = SimpleNamespace(enabled=True)
            self.router = object()
            self.dispatches = None  # Admission/reservation must never be reached.

    with pytest.raises(DomainError) as failure:
        await Probe().execute(None, "workspace", None, None, "command")
    assert failure.value.code == "DEVELOPMENT_EXECUTION_DISABLED"


@pytest.mark.asyncio
async def test_binding_deadline_at_commit_rolls_back_without_discarding_known_response(
    development, monkeypatch
):
    from datetime import UTC, timedelta

    from app.development_agent import dispatch as dispatch_module

    app, actor, _, authority, _, _, _, context = development
    _, root = admit(development)
    future = root.admitted_at + timedelta(seconds=root.plan.limits.maximum_seconds + 1)

    class ExpiredClock:
        @staticmethod
        def now(tz=UTC):
            return future

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=output(request))),
        trust_env=False,
    ) as client:
        service = executor(app, client)
        event = service.dispatches.creation.git.event

        def expire(session, row, principal, kind, payload):
            result = event(session, row, principal, kind, payload)
            if kind == "development.dispatch.planned":
                monkeypatch.setattr(dispatch_module, "datetime", ExpiredClock)
            return result

        monkeypatch.setattr(service.dispatches.creation.git, "event", expire)
        with pytest.raises(DomainError) as failure:
            await service.execute(actor, context.workspace_id, authority, context.selection, "late")
        assert failure.value.code == "DEVELOPMENT_BUDGET_EXHAUSTED"
        with app.state.repository.session_factory() as session:
            row = session.scalar(select(DevelopmentDispatchRow))
            assert row.payload["response"] is not None and row.payload["editing_binding"] is None
            assert len(session.scalars(select(SourceEditingPlanRow)).all()) == 1
            assert not session.scalars(
                select(AuditEventRow).where(
                    AuditEventRow.event_type == "development.dispatch.planned"
                )
            ).all()
