"""Hosted routing over native task/runtime state; all inference is intercepted."""

from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.decomposition.service import DecompositionService
from app.model_providers.budget import TaskBudget
from app.model_providers.contracts import ModelCapability, ModelExecutionRequest
from app.model_providers.errors import RateLimitError
from tests.test_context_integration import context_body
from tests.test_coordination import claim_fixture, install_coordinator_router
from tests.test_coordination_verification import critic_router
from tests.test_free_ai_capacity import allowance
from tests.test_model_router import FakeProvider
from tests.test_task_decomposition import setup

pytest_plugins = ["tests.test_coordination"]


def hosted_settings():
    # Synthetic account evidence belongs only to this intercepted test fixture.
    return Settings(
        _env_file=None,
        JARVIS_MODEL_EXECUTION_MODE="free_only",
        JARVIS_MODEL_VERIFIED_CLOUD_ENABLED=True,
        JARVIS_MODEL_PREFER_LOCAL=False,
        JARVIS_MODEL_FREE_ALLOWANCES_JSON="[" + allowance().model_dump_json() + "]",
        JARVIS_MODEL_OLLAMA_ENABLED=False,
        JARVIS_MODEL_OLLAMA_TOOL_ENABLED=False,
    )


def execution_settings(mode):
    if mode == "local":
        return Settings(_env_file=None, JARVIS_MODEL_EXECUTION_MODE="local_only")
    settings = hosted_settings()
    settings.model_prefer_local = mode == "hybrid"
    return settings


@pytest.mark.parametrize("activated", [False, True])
def test_planning_routing_requires_validated_explicit_cloud_activation(activated):
    from app.model_providers.router import planning_requirements

    settings = hosted_settings() if activated else Settings(_env_file=None)
    result = planning_requirements(settings, requested_provider="groq", preferred_model="model")
    assert result.allow_remote is activated
    assert result.prefer_local is not activated
    assert result.requested_provider == "groq" and result.preferred_model == "model"
    assert not result.allow_fallback and not result.allow_quota_fallback


def test_planning_routing_does_not_accept_lookalike_activation():
    from app.model_providers.router import planning_requirements

    result = planning_requirements(SimpleNamespace(verified_cloud_execution_allowed=True))
    assert not result.allow_remote and result.prefer_local


@pytest.mark.parametrize("mode", ["local", "cloud", "hybrid"])
async def test_decomposition_preserves_local_cloud_and_hybrid_policy(app, mode):
    task_id, assembly_id, router = setup(app)
    observed = []
    original = router.execute

    async def execute(**kwargs):
        observed.append(kwargs["requirements"])
        guard = kwargs.pop("before_dispatch", None)
        if guard is not None:
            await guard("local-fake", "fixture-model")
        return await original(**kwargs)

    router.execute = execute
    service = DecompositionService(
        app.state.repository, app.state.identity_service, router, settings=execution_settings(mode)
    )
    result = await service.prepare(task_id, assembly_id)
    assert result.status == "ready"
    assert len(observed) == 1
    assert observed[0].allow_remote is (mode != "local")
    assert observed[0].prefer_local is (mode != "cloud")


@pytest.mark.parametrize("prepared", [{"verify": True}], indirect=True)
@pytest.mark.parametrize("mode", ["local", "cloud", "hybrid"])
async def test_specialist_critic_and_synthesis_retain_native_checkpoints(app, prepared, mode):
    coordinator, actor, _, record, fence = claim_fixture(app, prepared)
    coordinator.settings = execution_settings(mode)
    coordinator.task_leases = app.state.task_leases
    router = critic_router(app, coordinator)
    observed = []
    original = router.execute

    async def execute(**kwargs):
        observed.append((kwargs["request"].output_schema.name, kwargs["requirements"]))
        guard = kwargs.pop("before_dispatch", None)
        if guard is not None:
            await guard("local-fake", "fixture-model")
        return await original(**kwargs)

    router.execute = execute
    for _ in range(5):
        record = await coordinator.run_once(record.id, fence, actor)
    assert record.status == "completed", record.model_dump(mode="json")
    assert record.modelDispatchCount == 7
    assert {name for name, _ in observed} == {
        "SpecialistResult",
        "SynthesisResult",
        "independent_node_verdict",
    }
    assert all(policy.allow_remote is (mode != "local") for _, policy in observed)
    assert all(policy.prefer_local is (mode != "cloud") for _, policy in observed)
    assert all(not policy.allow_fallback for _, policy in observed)


@pytest.mark.parametrize("mode", ["local", "cloud", "hybrid"])
async def test_team_assignment_uses_the_same_explicit_routing_policy(app, mode):
    from app.team_selection.service import TeamSelectionService

    task_id, _, router = setup(app)
    observed = []
    original = router.execute

    async def execute(**kwargs):
        observed.append(kwargs["requirements"])
        guard = kwargs.pop("before_dispatch", None)
        if guard is not None:
            await guard("local-fake", "fixture-model")
        return await original(**kwargs)

    router.execute = execute
    service = TeamSelectionService(
        app.state.repository, app.state.identity_service, router, settings=execution_settings(mode)
    )
    required, _, _ = await service._infer_capabilities(
        app.state.repository.get_task_durable(task_id)
    )
    assert required == ["research.market"]
    assert len(observed) == 1 and observed[0].allow_remote is (mode != "local")
    assert observed[0].prefer_local is (mode != "cloud")


@pytest.mark.parametrize("capability", [ModelCapability.CHAT, ModelCapability.CODE_GENERATION])
@pytest.mark.parametrize(
    "mode,explicit,expected",
    [
        ("local", None, "ollama"),
        ("cloud", None, "groq"),
        ("hybrid", None, "ollama"),
        ("hybrid", "groq", "groq"),
    ],
)
async def test_both_available_routes_chat_and_generation_without_losing_local_support(
    mode, explicit, expected, capability
):
    from app.model_providers.registry import ProviderRegistry
    from app.model_providers.retry import RetryExecutor, RetryPolicy
    from app.model_providers.router import ModelRouter, planning_requirements

    capabilities = frozenset({ModelCapability.CHAT, ModelCapability.CODE_GENERATION})
    local = FakeProvider("ollama", capabilities=capabilities)
    cloud = FakeProvider("groq", local=False, capabilities=capabilities)
    router = ModelRouter(
        ProviderRegistry([cloud, local]), RetryExecutor(RetryPolicy(maximum_attempts=1))
    )
    policy = planning_requirements(execution_settings(mode), requested_provider=explicit)
    policy.required_capability = capability
    result = await router.execute(
        request=ModelExecutionRequest(prompt="Fixture request", required_capability=capability),
        requirements=policy,
        budget=TaskBudget(maximum_requests=1),
    )
    assert result.provider == expected
    assert sum(len(provider.execute_calls) for provider in (local, cloud)) == 1
    if mode == "local":
        assert cloud.health_calls == 0 and not cloud.execute_calls


async def test_nonretryable_groq_failure_cannot_schedule_another_coordinator_request(app, prepared):
    coordinator, actor, _, record, fence = claim_fixture(app, prepared)
    router = install_coordinator_router(app, coordinator)
    calls = []

    async def execute(**kwargs):
        calls.append(kwargs["request"].output_schema.name)
        failure = RateLimitError(
            "Intercepted provider cooldown", provider="groq", retry_after_seconds=60
        )
        failure.retryable = False
        raise failure

    router.execute = execute
    record = await coordinator.run_once(record.id, fence, actor)
    assert record.status == "failed" and not record.failures[-1].retryable
    await coordinator.run_once(record.id, fence, actor)
    assert len(calls) == 1


@pytest.mark.parametrize("change", ["stop", "actor"])
async def test_native_revocation_during_provider_health_prevents_dispatch(app, prepared, change):
    from app.db.models import IdentityAgentRow, SystemStateRow
    from app.model_providers.registry import ProviderRegistry
    from app.model_providers.retry import RetryExecutor, RetryPolicy
    from app.model_providers.router import ModelRouter

    coordinator, actor, _, record, fence = claim_fixture(app, prepared)
    coordinator.settings = hosted_settings()

    class RevokingProvider(FakeProvider):
        async def health_check(self):
            health = await super().health_check()
            with app.state.task_leases._write() as session:
                if change == "stop":
                    session.get(SystemStateRow, 1).emergency_stop = True
                else:
                    session.get(IdentityAgentRow, actor.actor_id).is_enabled = False
            return health

    provider = RevokingProvider("local-fake", model="fixture-model")
    coordinator.router = ModelRouter(
        ProviderRegistry([provider]), RetryExecutor(RetryPolicy(maximum_attempts=1))
    )
    result = await coordinator.run_once(record.id, fence, actor)
    assert provider.health_calls == 1, result.model_dump(mode="json")
    assert provider.execute_calls == [], result.model_dump(mode="json")
    assert result.status == "blocked"


async def test_cloud_activation_revoked_during_health_prevents_dispatch(app, prepared):
    from app.core.errors import DomainError
    from app.model_providers.registry import ProviderRegistry
    from app.model_providers.retry import RetryExecutor, RetryPolicy
    from app.model_providers.router import ModelRouter, planning_requirements

    coordinator, actor, _, record, fence = claim_fixture(app, prepared)
    coordinator.settings = hosted_settings()

    class RevokingProvider(FakeProvider):
        async def health_check(self):
            health = await super().health_check()
            coordinator.settings.model_verified_cloud_enabled = False
            return health

    provider = RevokingProvider("groq", local=False)
    coordinator.router = ModelRouter(
        ProviderRegistry([provider]), RetryExecutor(RetryPolicy(maximum_attempts=1))
    )
    policy = planning_requirements(coordinator.settings)
    options = coordinator.dispatch_options(record.id, fence, actor)
    with pytest.raises(DomainError):
        await coordinator.router.execute(
            request=ModelExecutionRequest(prompt="Intercepted fixture"),
            requirements=policy,
            budget=TaskBudget(maximum_requests=1),
            **options,
        )
    assert provider.health_calls == 1 and provider.execute_calls == []


@pytest.mark.parametrize("stage", ["team", "decomposition"])
async def test_planning_activation_revoked_during_health_sends_no_request(app, stage):
    from app.model_providers.errors import ModelProviderError
    from app.model_providers.registry import ProviderRegistry
    from app.model_providers.retry import RetryExecutor, RetryPolicy
    from app.model_providers.router import ModelRouter
    from app.team_selection.service import TeamSelectionService

    task_id, assembly_id, _ = setup(app)
    settings = hosted_settings()

    class RevokingProvider(FakeProvider):
        async def health_check(self):
            health = await super().health_check()
            settings.model_verified_cloud_enabled = False
            return health

    provider = RevokingProvider("groq", local=False)
    router = ModelRouter(
        ProviderRegistry([provider]), RetryExecutor(RetryPolicy(maximum_attempts=1))
    )
    if stage == "decomposition":
        service = DecompositionService(
            app.state.repository, app.state.identity_service, router, settings=settings
        )
        await service.prepare(task_id, assembly_id)
    else:
        service = TeamSelectionService(
            app.state.repository, app.state.identity_service, router, settings=settings
        )
        try:
            await service._infer_capabilities(app.state.repository.get_task_durable(task_id))
        except (ModelProviderError, ValueError):
            pass
    assert provider.health_calls >= 1 and provider.execute_calls == []


@pytest.mark.parametrize("limit", [512, 4096])
async def test_decomposition_output_budget_can_be_bounded_without_changing_local_default(
    app, limit
):
    task_id, assembly_id, router = setup(app)
    settings = Settings(
        _env_file=None,
        JARVIS_MODEL_EXECUTION_MODE="local_only",
        JARVIS_DECOMPOSITION_MAXIMUM_OUTPUT_TOKENS=limit,
    )
    budgets = []
    original = router.execute

    async def execute(**kwargs):
        budgets.append(kwargs["budget"])
        return await original(**kwargs)

    router.execute = execute
    service = DecompositionService(
        app.state.repository, app.state.identity_service, router, settings=settings
    )
    assert (await service.prepare(task_id, assembly_id)).status == "ready"
    assert router.decomposition_requests[0].max_output_tokens == limit
    assert budgets[0].maximum_output_tokens == 2 * limit


@pytest.mark.parametrize("stage", ["team", "decomposition"])
@pytest.mark.parametrize("change", ["stop", "task"])
async def test_planning_native_changes_during_health_prevent_model_dispatch(app, stage, change):
    from app.core.errors import DomainError
    from app.db.models import SystemStateRow, TaskRow
    from app.model_providers.registry import ProviderRegistry
    from app.model_providers.retry import RetryExecutor, RetryPolicy
    from app.model_providers.router import ModelRouter
    from app.team_selection.service import TeamSelectionService

    task_id, assembly_id, _ = setup(app)

    class ChangingProvider(FakeProvider):
        async def health_check(self):
            health = await super().health_check()
            with app.state.task_leases._write() as session:
                if change == "stop":
                    session.get(SystemStateRow, 1).emergency_stop = True
                else:
                    row = session.get(TaskRow, task_id)
                    row.payload = {**row.payload, "title": "Changed objective during selection"}
            return health

    provider = ChangingProvider("groq", local=False)
    router = ModelRouter(
        ProviderRegistry([provider]), RetryExecutor(RetryPolicy(maximum_attempts=1))
    )
    try:
        if stage == "decomposition":
            await DecompositionService(
                app.state.repository, app.state.identity_service, router, settings=hosted_settings()
            ).prepare(task_id, assembly_id)
        else:
            await TeamSelectionService(
                app.state.repository, app.state.identity_service, router, settings=hosted_settings()
            )._infer_capabilities(app.state.repository.get_task_durable(task_id))
    except DomainError:
        pass
    assert provider.health_calls >= 1 and provider.execute_calls == []


@pytest.mark.parametrize("stage", ["team", "decomposition"])
async def test_interrupted_cloud_capable_planning_cannot_reissue_the_same_request(app, stage):
    from app.core.errors import DomainError
    from app.team_selection.service import TeamSelectionService

    task_id, assembly_id, router = setup(app)
    task = app.state.repository.get_task_durable(task_id)
    calls = []

    class SimulatedProcessLoss(BaseException):
        pass

    async def interrupted(**kwargs):
        guard = kwargs.pop("before_dispatch")
        await guard("local-fake", "fixture-model")
        calls.append(True)
        raise SimulatedProcessLoss("intercepted unknown dispatch outcome")

    router.execute = interrupted

    async def run():
        if stage == "decomposition":
            return await DecompositionService(
                app.state.repository, app.state.identity_service, router, settings=hosted_settings()
            ).prepare(task_id, assembly_id)
        return await TeamSelectionService(
            app.state.repository, app.state.identity_service, router, settings=hosted_settings()
        )._infer_capabilities(task)

    with pytest.raises(SimulatedProcessLoss):
        await run()
    with pytest.raises(DomainError) as refused:
        await run()
    assert refused.value.code == "PLANNING_DISPATCH_ALREADY_STARTED" and calls == [True]


async def test_concurrent_cloud_planning_preserves_first_result_without_duplicate_inference(app):
    import asyncio

    from app.core.errors import DomainError

    task_id, assembly_id, router = setup(app)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    original = router.execute

    async def held(**kwargs):
        guard = kwargs.pop("before_dispatch")
        await guard("local-fake", "fixture-model")
        calls.append(True)
        entered.set()
        await release.wait()
        return await original(**kwargs)

    router.execute = held

    def service():
        return DecompositionService(
            app.state.repository, app.state.identity_service, router, settings=hosted_settings()
        )

    first = asyncio.create_task(service().prepare(task_id, assembly_id))
    await asyncio.wait_for(entered.wait(), 5)
    try:
        with pytest.raises(DomainError):
            await asyncio.wait_for(service().prepare(task_id, assembly_id), 5)
    finally:
        release.set()
    result = await first
    assert result.status == "ready" and calls == [True]
    assert await service().prepare(task_id, assembly_id) == result


async def test_known_invalid_cloud_decomposition_repairs_once_with_two_durable_intents(app):
    import json

    from sqlalchemy import func, select

    from app.db.models import AuditEventRow
    from app.model_providers.contracts import ModelExecutionResponse
    from app.model_providers.registry import ProviderRegistry
    from app.model_providers.retry import RetryExecutor, RetryPolicy
    from app.model_providers.router import ModelRouter
    from tests.test_task_decomposition import proposal

    task_id, assembly_id, _ = setup(app)
    provider = FakeProvider(
        "groq",
        local=False,
        results=[
            ModelExecutionResponse(
                content="invalid",
                provider="groq",
                model="groq-model",
                latency_ms=1,
                input_tokens=1,
                output_tokens=1,
                total_tokens=2,
            ),
            ModelExecutionResponse(
                content=json.dumps(proposal()),
                provider="groq",
                model="groq-model",
                latency_ms=1,
                input_tokens=1,
                output_tokens=1,
                total_tokens=2,
            ),
        ],
    )
    router = ModelRouter(
        ProviderRegistry([provider]), RetryExecutor(RetryPolicy(maximum_attempts=3))
    )
    service = DecompositionService(
        app.state.repository, app.state.identity_service, router, settings=hosted_settings()
    )
    result = await service.prepare(task_id, assembly_id)
    assert result.status == "ready" and len(provider.execute_calls) == 2
    with app.state.repository.session_factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEventRow)
                .where(
                    AuditEventRow.event_type == "planning_model.dispatch_intent",
                    AuditEventRow.task_id == task_id,
                )
            )
            == 2
        )


async def test_stale_team_reselection_cannot_adopt_a_newer_native_selection(app):
    from app.core.errors import DomainError
    from app.db.models import TaskRow
    from app.team_selection.service import TeamSelectionService

    task_id, _, router = setup(app)
    stale = app.state.repository.get_task_durable(task_id)
    calls = []
    original = router.execute

    async def execute(**kwargs):
        guard = kwargs.pop("before_dispatch")
        await guard("local-fake", "fixture-model")
        calls.append(True)
        return await original(**kwargs)

    router.execute = execute

    def service():
        return TeamSelectionService(
            app.state.repository, app.state.identity_service, router, settings=hosted_settings()
        )

    await service()._infer_capabilities(stale)
    with app.state.task_leases._write() as session:
        row = session.get(TaskRow, task_id)
        row.payload = {
            **row.payload,
            "teamSelection": {
                **row.payload["teamSelection"],
                "selectionId": "sel-newer-native-selection",
            },
        }
    with pytest.raises(DomainError) as refused:
        await service()._infer_capabilities(stale)
    assert refused.value.code == "PLANNING_INPUT_CHANGED" and calls == [True]


@pytest.mark.parametrize("change", ["task", "stop"])
async def test_cloud_team_commit_rechecks_native_inputs_in_the_outbox_transaction(
    app, change, monkeypatch
):
    from app.core.errors import DomainError
    from app.db.models import SystemStateRow, TaskRow
    from app.team_selection.service import TeamSelectionService

    task_id, _, router = setup(app)
    task = app.state.repository.get_task_durable(task_id)
    original = router.execute

    async def execute(**kwargs):
        guard = kwargs.pop("before_dispatch")
        await guard("local-fake", "fixture-model")
        return await original(**kwargs)

    router.execute = execute
    emit = app.state.broker.emit

    async def change_at_commit(*args, **kwargs):
        with app.state.task_leases._write() as session:
            if change == "task":
                row = session.get(TaskRow, task_id)
                row.payload = {**row.payload, "title": "Changed after model response"}
            else:
                session.get(SystemStateRow, 1).emergency_stop = True
        return await emit(*args, **kwargs)

    monkeypatch.setattr(app.state.broker, "emit", change_at_commit)
    service = TeamSelectionService(
        app.state.repository,
        app.state.identity_service,
        router,
        event_broker=app.state.broker,
        settings=hosted_settings(),
    )
    with pytest.raises(DomainError):
        await service.assign_team(task, force=True)
    assert (
        task.teamSelection.selectionId
        == app.state.repository.get_task_durable(task_id).teamSelection.selectionId
    )
    if change == "task":
        assert (
            app.state.repository.get_task_durable(task_id).title == "Changed after model response"
        )


def test_context_cannot_persist_a_team_rejected_by_its_commit_guard(app, monkeypatch):
    from fastapi.testclient import TestClient

    from app.db.models import TaskRow

    task_id, _, router = setup(app)
    with app.state.task_leases._write() as session:
        row = session.get(TaskRow, task_id)
        row.payload = {**row.payload, "teamSelection": None}
    app.state.repository.reload()
    app.state.settings.model_execution_mode = "free_only"
    app.state.settings.model_verified_cloud_enabled = True
    original, emit = router.execute, app.state.broker.emit

    async def execute(**kwargs):
        guard = kwargs.pop("before_dispatch")
        await guard("local-fake", "fixture-model")
        return await original(**kwargs)

    router.execute = execute

    async def start_before_commit(*args, **kwargs):
        if args[0] == "team_selection.completed":
            with app.state.task_leases._write() as session:
                row = session.get(TaskRow, task_id)
                row.payload = {
                    **row.payload,
                    "status": "in_progress",
                    "startedAt": "2026-11-02T12:00:00Z",
                }
        return await emit(*args, **kwargs)

    monkeypatch.setattr(app.state.broker, "emit", start_before_commit)
    with TestClient(app) as client:
        response = client.post("/api/context/assemblies", json=context_body(task_id=task_id))
    assert response.status_code == 201, response.text
    task = app.state.repository.get_task_durable(task_id)
    assert task.teamSelection is None and task.status == "in_progress"
