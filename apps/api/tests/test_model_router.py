from __future__ import annotations

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.model_providers.base import ProviderBase
from app.model_providers.budget import ModelPricing, TaskBudget
from app.model_providers.contracts import (
    HealthStatus,
    ModelCapability,
    ModelExecutionRequest,
    ModelExecutionResponse,
    ProviderHealth,
    ProviderType,
)
from app.model_providers.errors import (
    AuthenticationError,
    BudgetExceededError,
    DuplicateProviderError,
    TransientProviderError,
    UnknownProviderError,
)
from app.model_providers.factory import (
    build_default_routing_requirements,
    build_default_task_budget,
    build_model_pricing,
    build_provider_registry,
)
from app.model_providers.ollama import OllamaProvider, is_loopback_endpoint
from app.model_providers.registry import ProviderRegistry
from app.model_providers.retry import RetryExecutor, RetryPolicy
from app.model_providers.router import ModelRouter, RoutingRequirements


class FixtureProvider(ProviderBase):
    provider_type = ProviderType.OLLAMA

    def __init__(
        self,
        name: str,
        *,
        local: bool = True,
        healthy: bool = True,
        capabilities: frozenset[ModelCapability] = frozenset({ModelCapability.CHAT}),
        outcomes: list[Exception | str] | None = None,
        available_models: set[str] | None = None,
        availability_unknown: bool = False,
        health_log: list[str] | None = None,
    ) -> None:
        self.name = name
        self.default_model = f"{name}-model"
        self.is_local = local
        self.capabilities = capabilities
        self.is_healthy = healthy
        self.outcomes = list(outcomes or ["ok"])
        self.available_models = available_models
        self.availability_unknown = availability_unknown
        self.health_log = health_log
        self.calls = 0
        self.health_calls = 0
        self.availability_calls: list[str] = []
        self.executed_models: list[str | None] = []

    async def health_check(self) -> ProviderHealth:
        self.health_calls += 1
        if self.health_log is not None:
            self.health_log.append(self.name)
        return ProviderHealth(
            provider=self.name,
            healthy=self.is_healthy,
            status=HealthStatus.HEALTHY if self.is_healthy else HealthStatus.UNAVAILABLE,
            latency_ms=0,
            model_available=(
                self.default_model in self.available_models
                if self.available_models is not None
                else None
            ),
        )

    async def model_available(self, model: str) -> bool | None:
        self.availability_calls.append(model)
        if self.availability_unknown:
            return None
        if self.available_models is not None:
            return model in self.available_models
        return model != "missing"

    async def execute(self, request: ModelExecutionRequest) -> ModelExecutionResponse:
        self.calls += 1
        self.executed_models.append(request.model)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return ModelExecutionResponse(
            content=outcome,
            provider=self.name,
            model=request.model or self.default_model,
            input_tokens=2,
            output_tokens=1,
            latency_ms=0,
            task_id=request.task_id,
            correlation_id=request.correlation_id,
        )


class RaisingHealthProvider(FixtureProvider):
    async def health_check(self) -> ProviderHealth:
        raise RuntimeError("health-contract-secret-response")


def router(*providers: FixtureProvider, sleep: list[float] | None = None) -> ModelRouter:
    delays = sleep if sleep is not None else []

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    return ModelRouter(
        ProviderRegistry(providers),
        RetryExecutor(
            RetryPolicy(
                maximum_attempts=2,
                initial_backoff_seconds=0.5,
                maximum_backoff_seconds=1,
            ),
            sleep=record_sleep,
        ),
    )


def request() -> ModelExecutionRequest:
    return ModelExecutionRequest(
        prompt="hello",
        task_id="task-1",
        correlation_id="correlation-1",
        required_capability=ModelCapability.CHAT,
    )


@pytest.mark.asyncio
async def test_registry_order_duplicate_unknown_and_capability() -> None:
    first = FixtureProvider("first")
    second = FixtureProvider("second", capabilities=frozenset({ModelCapability.CODE_GENERATION}))
    registry = ProviderRegistry([first, second])
    assert [item.name for item in registry.list()] == ["first", "second"]
    assert registry.get("first") is first
    assert registry.by_capability(ModelCapability.CODE_GENERATION) == [second]
    with pytest.raises(DuplicateProviderError):
        await registry.register(FixtureProvider("first"))
    with pytest.raises(UnknownProviderError):
        registry.get("missing")


@pytest.mark.asyncio
async def test_registry_isolates_health_contract_violation_and_routing_continues() -> None:
    broken = RaisingHealthProvider("broken")
    healthy = FixtureProvider("healthy")
    registry = ProviderRegistry([broken, healthy])

    health = await registry.health()

    assert list(health) == ["broken", "healthy"]
    assert not health["broken"].healthy
    assert health["broken"].error_category == "MalformedProviderResponseError"
    assert "health-contract-secret-response" not in health["broken"].model_dump_json()
    assert health["healthy"].healthy

    result = await ModelRouter(
        registry,
        RetryExecutor(RetryPolicy(maximum_attempts=1)),
    ).execute(
        request=request(),
        requirements=RoutingRequirements(),
        budget=TaskBudget(maximum_requests=1),
    )
    assert result.provider == "healthy"
    assert broken.calls == 0
    assert healthy.calls == 1


@pytest.mark.asyncio
async def test_prefer_local_remote_policy_and_exact_fallback_order() -> None:
    remote = FixtureProvider("remote", local=False)
    local = FixtureProvider(
        "local",
        outcomes=[
            TransientProviderError("temporary", provider="local"),
            TransientProviderError("temporary again", provider="local"),
        ],
    )
    service = router(remote, local)
    result = await service.execute(
        request=request(),
        requirements=RoutingRequirements(
            prefer_local=True,
            allow_remote=True,
            allow_fallback=True,
            maximum_fallbacks=1,
        ),
        budget=TaskBudget(maximum_requests=3),
    )
    assert result.provider == "remote"
    assert result.routing_metadata["attempted_providers"] == ["local", "remote"]
    assert result.routing_metadata["fallback_count"] == 1


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://127.0.0.1:11434", True),
        ("http://localhost:11434", True),
        ("http://[::1]:11434", True),
        ("https://ollama.example.com", False),
        ("http://192.168.1.50:11434", False),
        ("http://localhost.example.com:11434", False),
        ("http://host.docker.internal:11434", False),
        ("http://ollama:11434", False),
    ],
)
def test_ollama_locality_is_structural_and_conservative(url: str, expected: bool) -> None:
    assert is_loopback_endpoint(url) is expected
    assert OllamaProvider(base_url=url, default_model="model").is_local is expected


@pytest.mark.asyncio
async def test_remote_ollama_respects_allow_remote_without_network() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise AssertionError(f"routing contacted remote Ollama: {request.url}")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://ollama.example.com",
    ) as client:
        provider = OllamaProvider(
            base_url="https://ollama.example.com",
            default_model="model",
            client=client,
        )
        service = router(provider)
        assert await service.eligible(request(), RoutingRequirements(allow_remote=False)) == []
        assert await service.eligible(request(), RoutingRequirements(allow_remote=True)) == [
            provider
        ]
    assert calls == 0


@pytest.mark.asyncio
async def test_static_policy_filters_before_deterministic_health_checks() -> None:
    order: list[str] = []
    eligible_first = FixtureProvider("eligible-first", health_log=order)
    eligible_second = FixtureProvider("eligible-second", health_log=order)
    remote = FixtureProvider("remote", local=False, health_log=order)
    denied = FixtureProvider("denied", health_log=order)
    outside = FixtureProvider("outside", health_log=order)
    incapable = FixtureProvider(
        "incapable",
        capabilities=frozenset({ModelCapability.VISION}),
        health_log=order,
    )
    service = router(
        eligible_first,
        remote,
        denied,
        outside,
        incapable,
        eligible_second,
    )
    candidates = await service.eligible(
        request(),
        RoutingRequirements(
            provider_allowlist=frozenset({"eligible-first", "eligible-second", "incapable"}),
            allow_remote=False,
        ),
    )
    assert candidates == [eligible_first, eligible_second]
    assert order == ["eligible-first", "eligible-second"]
    assert remote.health_calls == 0
    assert denied.health_calls == 0
    assert outside.health_calls == 0
    assert incapable.health_calls == 0

    denied_only = FixtureProvider("denied-only")
    permitted = FixtureProvider("permitted")
    candidates = await router(denied_only, permitted).eligible(
        request(),
        RoutingRequirements(provider_denylist=frozenset({"denied-only"})),
    )
    assert candidates == [permitted]
    assert denied_only.health_calls == 0


@pytest.mark.asyncio
async def test_effective_model_precedence_and_availability() -> None:
    preferred = FixtureProvider(
        "provider",
        available_models={"preferred-model", "request-model"},
    )
    service = router(preferred)
    result = await service.execute(
        request=request().model_copy(update={"model": "request-model"}),
        requirements=RoutingRequirements(preferred_model="preferred-model"),
        budget=TaskBudget(),
    )
    assert preferred.availability_calls == ["preferred-model"]
    assert preferred.executed_models == ["preferred-model"]
    assert result.routing_metadata["selected_model"] == "preferred-model"

    request_override = FixtureProvider(
        "request-override",
        available_models={"request-model"},
    )
    result = await router(request_override).execute(
        request=request().model_copy(update={"model": "request-model"}),
        requirements=RoutingRequirements(),
        budget=TaskBudget(),
    )
    assert request_override.availability_calls == ["request-model"]
    assert request_override.executed_models == ["request-model"]
    assert result.routing_metadata["selected_model"] == "request-model"


@pytest.mark.asyncio
async def test_unavailable_effective_model_and_service_are_skipped() -> None:
    unavailable_model = FixtureProvider("model-missing", available_models=set())
    service_down = FixtureProvider(
        "service-down",
        healthy=False,
        available_models={"request-model"},
    )
    unknown = FixtureProvider("unknown", availability_unknown=True)
    candidates = await router(unavailable_model, service_down, unknown).eligible(
        request().model_copy(update={"model": "request-model"}),
        RoutingRequirements(),
    )
    assert candidates == [unknown]
    assert unavailable_model.availability_calls == ["request-model"]
    assert service_down.availability_calls == []
    assert unknown.availability_calls == ["request-model"]


@pytest.mark.asyncio
async def test_default_model_availability_and_fallback_models_are_provider_specific() -> None:
    missing_default = FixtureProvider("missing-default", available_models=set())
    assert await router(missing_default).eligible(request(), RoutingRequirements()) == []

    first = FixtureProvider(
        "first",
        available_models={"first-model"},
        outcomes=[
            TransientProviderError("one", provider="first"),
            TransientProviderError("two", provider="first"),
        ],
    )
    second = FixtureProvider("second", available_models={"second-model"})
    result = await router(first, second).execute(
        request=request(),
        requirements=RoutingRequirements(allow_fallback=True, maximum_fallbacks=1),
        budget=TaskBudget(maximum_requests=3),
    )
    assert first.executed_models == ["first-model", "first-model"]
    assert second.executed_models == ["second-model"]
    assert result.routing_metadata["selected_model"] == "second-model"


@pytest.mark.asyncio
async def test_fallback_provider_requires_its_own_cost_pricing_before_execution() -> None:
    first = FixtureProvider(
        "first",
        outcomes=[
            TransientProviderError("one", provider="first"),
            TransientProviderError("two", provider="first"),
        ],
    )
    unpriced_fallback = FixtureProvider("unpriced-fallback")
    with pytest.raises(BudgetExceededError) as raised:
        await router(first, unpriced_fallback).execute(
            request=request(),
            requirements=RoutingRequirements(allow_fallback=True, maximum_fallbacks=1),
            budget=TaskBudget(maximum_requests=3, maximum_cost_usd=1),
            pricing={
                "first-model": ModelPricing(
                    input_per_million_usd=1,
                    output_per_million_usd=1,
                )
            },
        )
    assert raised.value.provider == "unpriced-fallback"
    assert raised.value.model == "unpriced-fallback-model"
    assert unpriced_fallback.calls == 0


@pytest.mark.asyncio
async def test_retry_then_success_counts_requests_and_backoff() -> None:
    delays: list[float] = []
    provider = FixtureProvider(
        "local",
        outcomes=[TransientProviderError("temporary", provider="local"), "recovered"],
    )
    result = await router(provider, sleep=delays).execute(
        request=request(),
        requirements=RoutingRequirements(),
        budget=TaskBudget(maximum_requests=2),
    )
    assert result.content == "recovered"
    assert provider.calls == 2
    assert delays == [0.5]


@pytest.mark.asyncio
async def test_auth_does_not_retry_or_fallback() -> None:
    first = FixtureProvider(
        "first", outcomes=[AuthenticationError("bad credentials", provider="first")]
    )
    second = FixtureProvider("second")
    with pytest.raises(AuthenticationError):
        await router(first, second).execute(
            request=request(),
            requirements=RoutingRequirements(allow_fallback=True, maximum_fallbacks=1),
            budget=TaskBudget(maximum_requests=3),
        )
    assert first.calls == 1
    assert second.calls == 0


@pytest.mark.asyncio
async def test_remote_disallowed_unhealthy_and_budget_exhaustion() -> None:
    remote = FixtureProvider("remote", local=False)
    unhealthy = FixtureProvider("unhealthy", healthy=False)
    with pytest.raises(UnknownProviderError):
        await router(remote, unhealthy).execute(
            request=request(),
            requirements=RoutingRequirements(allow_remote=False),
            budget=TaskBudget(),
        )
    retrying = FixtureProvider(
        "retrying",
        outcomes=[
            TransientProviderError("one", provider="retrying"),
            TransientProviderError("two", provider="retrying"),
        ],
    )
    with pytest.raises(BudgetExceededError):
        await router(retrying).execute(
            request=request(),
            requirements=RoutingRequirements(),
            budget=TaskBudget(maximum_requests=1),
        )
    assert retrying.calls == 1


def test_routing_validation() -> None:
    with pytest.raises(ValidationError):
        RoutingRequirements(
            provider_allowlist=frozenset({"same"}),
            provider_denylist=frozenset({"same"}),
        )
    with pytest.raises(ValidationError):
        RoutingRequirements(maximum_fallbacks=1)


def test_configuration_defaults_validation_redaction_and_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in (
        "JARVIS_MODEL_OLLAMA_ENABLED",
        "JARVIS_MODEL_OPENAI_COMPATIBLE_ENABLED",
        "JARVIS_MODEL_OPENAI_COMPATIBLE_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    defaults = Settings(_env_file=None)
    assert build_provider_registry(defaults).list() == []
    assert not any("execution" in field for field in Settings.model_fields)

    monkeypatch.setenv("JARVIS_MODEL_OLLAMA_ENABLED", "true")
    assert [item.name for item in build_provider_registry(Settings(_env_file=None)).list()] == [
        "ollama"
    ]
    monkeypatch.setenv("JARVIS_MODEL_OPENAI_COMPATIBLE_ENABLED", "true")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
    secret = "test-secret-never-render"
    monkeypatch.setenv("JARVIS_MODEL_OPENAI_COMPATIBLE_API_KEY", secret)
    configured = Settings(_env_file=None)
    assert secret not in repr(configured)
    assert [item.name for item in build_provider_registry(configured).list()] == [
        "ollama",
        "openai-compatible",
    ]
    monkeypatch.setenv(
        "JARVIS_MODEL_PRICING_JSON",
        '{"model-a":{"input_per_million_usd":1.5,"output_per_million_usd":2}}',
    )
    monkeypatch.setenv("JARVIS_MODEL_DEFAULT_MAXIMUM_REQUESTS", "3")
    monkeypatch.setenv("JARVIS_MODEL_ALLOW_REMOTE", "true")
    configured = Settings(_env_file=None)
    assert build_default_task_budget(configured).maximum_requests == 3
    assert build_default_routing_requirements(configured).allow_remote
    assert build_model_pricing(configured)["model-a"].input_per_million_usd == 1.5


def test_configuration_rejects_invalid_pricing_and_duplicate_priority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JARVIS_MODEL_PRICING_JSON", '{"model-a":{"input_per_million_usd":-1}}')
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
    monkeypatch.setenv("JARVIS_MODEL_PRICING_JSON", "{}")
    monkeypatch.setenv("JARVIS_MODEL_PROVIDER_PRIORITY", "ollama,ollama")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
