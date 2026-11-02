"""Verified nonbillable text proposals; fixtures are not live inference evidence."""

from types import SimpleNamespace

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.core.errors import DomainError
from app.development_agent.config import DevelopmentProviderSettings, build_development_router
from app.development_agent.execution import DevelopmentExecutor
from app.model_providers.contracts import ModelCapability
from app.model_providers.free_endpoints import compatible_endpoint
from app.model_providers.openai_compatible import HealthCheckStrategy, OpenAICompatibleProvider
from app.model_providers.registry import ProviderRegistry
from app.model_providers.retry import RetryExecutor, RetryPolicy
from app.model_providers.router import ModelRouter
from app.models.development_dispatch import DevelopmentLimits
from tests.test_free_ai_capacity import allowance

pytest_plugins = ["tests.test_free_ai_capacity", "tests.test_development_agent"]
CAPABILITIES = frozenset({ModelCapability.CODE_GENERATION, ModelCapability.STRUCTURED_OUTPUT})


def configuration(**updates):
    values = dict(enabled=True, provider="verified", model="model", hosted_provider="groq")
    values.update(updates)
    return DevelopmentProviderSettings(**values)


def application_settings(grant, *, activated=True):
    import json

    return Settings(
        _env_file=None,
        JARVIS_MODEL_EXECUTION_MODE="free_only",
        JARVIS_MODEL_VERIFIED_CLOUD_ENABLED=activated,
        JARVIS_MODEL_FREE_ALLOWANCES_JSON=json.dumps([grant.model_dump(mode="json")]),
    )


def planning_router(capacity, grant, *, capabilities=CAPABILITIES, client=None):
    from tests.test_free_ai_capacity import LegacyQuotaProbe

    capacity.configure([grant])
    provider = OpenAICompatibleProvider(
        name=grant.provider,
        base_url=compatible_endpoint(grant),
        api_key=SecretStr("test-key"),
        default_model=sorted(grant.models)[0],
        capabilities=capabilities,
        health_strategy=HealthCheckStrategy.CONFIGURATION,
        execution_mode="free_only",
        free_allowance=grant,
        free_capacity=capacity,
        groq_capacity=LegacyQuotaProbe(capacity),
        client=client,
    )
    return ModelRouter(ProviderRegistry([provider]), RetryExecutor(RetryPolicy(maximum_attempts=3)))


def test_hosted_router_is_separate_and_requires_explicit_current_activation(capacity, monkeypatch):
    import httpx

    grant = allowance(capabilities=CAPABILITIES)
    existing = planning_router(capacity, grant)
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda *a, **kw: pytest.fail("factory created a client")
    )
    settings = configuration()
    router = build_development_router(settings, existing, application_settings(grant))
    assert router is not None and router is not existing
    assert router.retry.policy.maximum_attempts == 1
    assert router.registry.get("groq") is not existing.registry.get("groq")
    assert router.registry.get("groq").free_capacity is capacity
    assert (
        build_development_router(settings, existing, application_settings(grant, activated=False))
        is None
    )
    assert build_development_router(settings, existing) is None


def test_hosted_route_rejects_undeclared_coding_capability(capacity):
    grant = allowance()
    existing = planning_router(capacity, grant)
    assert build_development_router(configuration(), existing, application_settings(grant)) is None


@pytest.mark.parametrize("model", ["vendor/coding-model:free", "@cf/vendor/coding-model"])
def test_namespaced_models_require_explicit_hosted_configuration_and_admission(model):
    assert configuration(model=model).model == model
    limits = DevelopmentLimits(
        provider="verified-provider",
        model=model,
        completion_criteria=("Done.",),
        hosted_route_digest="a" * 64,
    )
    assert limits.model == model
    with pytest.raises(ValidationError):
        DevelopmentProviderSettings(enabled=True, provider="local", model=model)
    with pytest.raises(ValidationError):
        DevelopmentLimits(provider="ollama", model=model, completion_criteria=("Done.",))


def test_hosted_route_is_bound_to_independently_approved_account_and_model(capacity):
    from app.development_agent.config import hosted_route_digest

    grant = allowance(capabilities=CAPABILITIES)
    app_settings = application_settings(grant)
    settings = configuration()
    router = build_development_router(settings, planning_router(capacity, grant), app_settings)
    service = object.__new__(DevelopmentExecutor)
    service.settings, service.router = settings, router
    service.app = SimpleNamespace(state=SimpleNamespace(settings=app_settings))
    limits = DevelopmentLimits(
        provider="groq",
        model="model",
        completion_criteria=("Return a bounded proposal.",),
        hosted_route_digest=hosted_route_digest(router.registry.get("groq")),
    )
    root = SimpleNamespace(plan=SimpleNamespace(limits=limits))
    route = service.route(root)
    assert route.allow_remote is True
    assert route.allow_fallback is False
    assert route.provider_allowlist == frozenset({"groq"})
    changed = limits.model_copy(update={"hosted_route_digest": "f" * 64})
    with pytest.raises(DomainError) as error:
        service.route(SimpleNamespace(plan=SimpleNamespace(limits=changed)))
    assert error.value.code == "DEVELOPMENT_PROVIDER_CHANGED"
    app_settings.model_verified_cloud_enabled = False
    with pytest.raises(DomainError) as error:
        service.route(root)
    assert error.value.code == "DEVELOPMENT_EXECUTION_DISABLED"


def test_historical_limits_keep_their_original_serialized_shape():
    limits = DevelopmentLimits(provider="ollama", model="model", completion_criteria=("Done.",))
    assert "hosted_route_digest" not in limits.model_dump(mode="json")
    with pytest.raises(ValidationError):
        DevelopmentLimits(
            provider="groq",
            model="model",
            completion_criteria=("Done.",),
            local_endpoint="http://127.0.0.1:11434",
            hosted_route_digest="a" * 64,
        )


@pytest.mark.parametrize("capabilities", [[], ["tool_calling"], ["vision"], ["shell"]])
def test_allowances_cannot_authorize_tools_or_unimplemented_hosted_capabilities(capabilities):
    with pytest.raises(ValidationError):
        allowance(capabilities=capabilities)


def test_current_allowance_replacement_disables_previously_built_router(capacity):
    import json

    from app.development_agent.config import development_router_enabled

    grant = allowance(capabilities=CAPABILITIES)
    app_settings = application_settings(grant)
    settings = configuration()
    router = build_development_router(settings, planning_router(capacity, grant), app_settings)
    assert development_router_enabled(settings, router, app_settings)
    replacement = grant.model_copy(update={"account_id": "different-verified-account"})
    app_settings.model_free_allowances_json = json.dumps([replacement.model_dump(mode="json")])
    assert not development_router_enabled(settings, router, app_settings)


def test_credential_change_disables_previously_built_router(capacity):
    from app.development_agent.config import development_router_enabled

    grant = allowance(capabilities=CAPABILITIES)
    app_settings = application_settings(grant)
    settings = configuration()
    router = build_development_router(settings, planning_router(capacity, grant), app_settings)
    router.registry.get("groq").api_key = SecretStr("different-unverified-key")
    assert not development_router_enabled(settings, router, app_settings)


def test_factory_does_not_invent_coding_capabilities(capacity, monkeypatch):
    from app.model_providers.factory import build_provider_registry

    monkeypatch.setenv("TEST_FREE_KEY", "test-key")
    for grant in (allowance(), allowance(capabilities=CAPABILITIES)):
        registry = build_provider_registry(application_settings(grant), capacity.sessions)
        assert registry.get("groq").capabilities == frozenset(
            ModelCapability(item) for item in grant.capabilities
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("revoke", [None, "activation", "credential", "endpoint"])
async def test_real_router_reserves_shared_quota_and_rechecks_activation_after_health(
    capacity, revoke
):
    import httpx

    from app.development_agent.config import development_router_enabled, development_routing
    from app.model_providers.budget import TaskBudget
    from app.model_providers.contracts import ModelExecutionRequest

    grant = allowance(capabilities=CAPABILITIES)
    capacity.configure([grant])
    app_settings = application_settings(grant)
    settings = configuration()
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "model": "model",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "proposal"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            },
        )

    async with httpx.AsyncClient(
        base_url=compatible_endpoint(grant), transport=httpx.MockTransport(respond), trust_env=False
    ) as client:
        router = build_development_router(
            settings, planning_router(capacity, grant, client=client), app_settings
        )
        provider = router.registry.get("groq")
        original_health = provider.health_check

        async def health():
            result = await original_health()
            if revoke == "activation":
                app_settings.model_verified_cloud_enabled = False
            elif revoke == "credential":
                provider.api_key = SecretStr("different-unverified-key")
            elif revoke == "endpoint":
                client.base_url = "https://example.invalid/v1/"
            return result

        provider.health_check = health

        async def before_dispatch(_provider, _model):
            if not development_router_enabled(settings, router, app_settings):
                raise DomainError("DEVELOPMENT_EXECUTION_DISABLED", "revoked", 403)

        request = ModelExecutionRequest(
            prompt="Propose code as text.", required_capability=ModelCapability.CODE_GENERATION
        )
        if revoke:
            with pytest.raises(DomainError):
                await router.execute(
                    request=request,
                    requirements=development_routing(settings),
                    budget=TaskBudget(maximum_requests=1),
                    before_dispatch=before_dispatch,
                )
        else:
            result = await router.execute(
                request=request,
                requirements=development_routing(settings),
                budget=TaskBudget(maximum_requests=1),
                before_dispatch=before_dispatch,
            )
            assert result.content == "proposal"
            assert result.estimated_cost_usd == 0
    assert len(calls) == (0 if revoke else 1)
    assert capacity.snapshot()[0]["remaining_requests"] == (5 if revoke else 4)


@pytest.mark.asyncio
async def test_hosted_proposal_uses_native_admission_and_replays_without_inference(development):
    import json

    import httpx

    from app.development_agent.config import hosted_route_digest
    from app.development_agent.dispatch import DevelopmentDispatchService
    from app.model_providers.free_capacity import FreeCapacityRepository
    from tests.test_development_agent import proposal

    app, actor, operator, authority, _, target, _, context = development
    grant = allowance(capabilities=CAPABILITIES)
    capacity = FreeCapacityRepository(app.state.repository.session_factory)
    capacity.configure([grant])
    current = application_settings(grant)
    for field in (
        "model_execution_mode",
        "model_verified_cloud_enabled",
        "model_free_allowances_json",
    ):
        setattr(app.state.settings, field, getattr(current, field))
    settings = configuration()
    calls = []

    def respond(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "model",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": proposal(context).model_dump_json(),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
            },
        )

    async with httpx.AsyncClient(
        base_url=compatible_endpoint(grant), transport=httpx.MockTransport(respond), trust_env=False
    ) as client:
        router = build_development_router(
            settings, planning_router(capacity, grant, client=client), app.state.settings
        )
        service = DevelopmentExecutor(app, settings, router)
        limits = DevelopmentLimits(
            provider="groq",
            model="model",
            completion_criteria=("The accessor returns the configured value.",),
            hosted_route_digest=hosted_route_digest(router.registry.get("groq")),
        )
        dispatches = DevelopmentDispatchService(app)
        preview = dispatches.preview(
            actor, context.workspace_id, authority, context.selection, limits
        )
        dispatches.admit(
            operator,
            actor,
            context.workspace_id,
            authority,
            context.selection,
            limits,
            preview.plan_hash,
        )
        args = (
            actor,
            context.workspace_id,
            authority,
            context.selection,
            "hosted-proposal-fixture",
        )
        result = await service.execute(*args)
        assert result.state == "awaiting_source_approval"
        assert await service.execute(*args) == result
    assert len(calls) == 1 and "tools" not in calls[0]
    assert authority.lease_token not in json.dumps(calls)
    assert "test-key" not in json.dumps(calls)
    assert capacity.snapshot()[0]["remaining_requests"] == 4
    assert (target / "source.py").read_bytes() == b"value = 1\n"
    assert not (target / "new.py").exists()
