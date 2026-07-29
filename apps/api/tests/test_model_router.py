from __future__ import annotations

from collections.abc import Awaitable, Callable
from decimal import Decimal

import pytest

from app.core.config import Settings
from app.model_providers.budget import BudgetTracker, ModelPricing, TaskBudget
from app.model_providers.contracts import (
    HealthStatus,
    ModelCapability,
    ModelExecutionRequest,
    ModelExecutionResponse,
    ProviderHealth,
    ProviderSummary,
    ProviderType,
)
from app.model_providers.errors import (
    AuthenticationError,
    BudgetExceededError,
    DuplicateProviderError,
    ProviderUnavailableError,
    QuotaExhaustedError,
    RequestTimeoutError,
    UnknownProviderError,
)
from app.model_providers.factory import (
    build_default_routing_requirements,
    build_default_task_budget,
    build_model_pricing,
    build_provider_registry,
)
from app.model_providers.registry import ProviderRegistry
from app.model_providers.retry import RetryExecutor, RetryPolicy
from app.model_providers.router import ModelRouter, RoutingRequirements


class FakeProvider:
    provider_type = ProviderType.OLLAMA

    def __init__(
        self,
        name: str,
        *,
        local: bool = True,
        model: str | None = None,
        capabilities: frozenset[ModelCapability] | None = None,
        results: list[ModelExecutionResponse | Exception] | None = None,
        available: bool | None = True,
    ) -> None:
        self.name = name
        self.is_local = local
        self.default_model = model or f"{name}-model"
        self.capabilities = capabilities or frozenset({ModelCapability.CHAT})
        self.results = list(results or [])
        self.available = available
        self.health_calls = 0
        self.availability_calls: list[str] = []
        self.execute_calls: list[str] = []

    async def health_check(self) -> ProviderHealth:
        self.health_calls += 1
        return ProviderHealth(
            provider=self.name,
            healthy=True,
            status=HealthStatus.HEALTHY,
            latency_ms=0,
        )

    async def model_available(self, model: str) -> bool | None:
        self.availability_calls.append(model)
        return self.available

    async def execute(self, request: ModelExecutionRequest) -> ModelExecutionResponse:
        assert request.model is not None
        self.execute_calls.append(request.model)
        result = self.results.pop(0) if self.results else _response(self.name, request.model)
        if isinstance(result, Exception):
            raise result
        return result

    def safe_summary(self) -> ProviderSummary:
        return ProviderSummary(
            name=self.name,
            provider_type=self.provider_type,
            is_local=self.is_local,
            capabilities=sorted(self.capabilities, key=lambda item: item.value),
            default_model=self.default_model,
        )


def _response(
    provider: str,
    model: str,
    *,
    input_tokens: int | None = 1,
    output_tokens: int | None = 1,
    total_tokens: int | None = 2,
) -> ModelExecutionResponse:
    return ModelExecutionResponse(
        content="answer",
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        latency_ms=1,
    )


def _router(
    *providers: FakeProvider,
    attempts: int = 1,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> ModelRouter:
    async def no_sleep(_: float) -> None:
        return None

    return ModelRouter(
        ProviderRegistry(providers),
        RetryExecutor(
            RetryPolicy(maximum_attempts=attempts),
            sleep=sleep or no_sleep,
        ),
    )


@pytest.mark.asyncio
async def test_static_policy_filters_before_health_and_availability() -> None:
    local = FakeProvider("local")
    remote = FakeProvider("remote", local=False)
    denied = FakeProvider("denied")
    router = _router(local, remote, denied)
    eligible = await router.eligible(
        ModelExecutionRequest(prompt="hello"),
        RoutingRequirements(allow_remote=False, provider_denylist={"denied"}),
    )
    assert [provider.name for provider in eligible] == ["local"]
    assert local.health_calls == 1
    assert remote.health_calls == denied.health_calls == 0
    assert local.availability_calls == ["local-model"]


@pytest.mark.asyncio
async def test_routing_order_selection_and_model_precedence() -> None:
    remote = FakeProvider("remote", local=False)
    local = FakeProvider("local")
    router = _router(remote, local)
    candidates = await router.eligible(
        ModelExecutionRequest(prompt="hello", model="request-model"),
        RoutingRequirements(
            allow_remote=True,
            prefer_local=True,
            preferred_model="preferred-model",
            provider_allowlist={"remote", "local"},
        ),
    )
    assert [item.name for item in candidates] == ["local", "remote"]
    assert local.availability_calls == ["preferred-model"]
    assert remote.availability_calls == ["preferred-model"]


@pytest.mark.asyncio
async def test_requested_provider_is_not_bypassed_without_fallback() -> None:
    requested = FakeProvider("requested", available=False)
    other = FakeProvider("other")
    router = _router(requested, other)
    with pytest.raises(UnknownProviderError):
        await router.eligible(
            ModelExecutionRequest(prompt="hello"),
            RoutingRequirements(requested_provider="requested"),
        )
    assert other.health_calls == 0


@pytest.mark.asyncio
async def test_filtered_requested_provider_consumes_the_primary_slot() -> None:
    requested = FakeProvider("requested", available=False)
    first_fallback = FakeProvider("first-fallback")
    second_fallback = FakeProvider("second-fallback")
    router = _router(requested, first_fallback, second_fallback)
    with pytest.raises(UnknownProviderError):
        await router.execute(
            request=ModelExecutionRequest(prompt="hello"),
            requirements=RoutingRequirements(
                requested_provider="requested",
                allow_fallback=True,
                maximum_fallbacks=0,
            ),
            budget=TaskBudget(maximum_requests=1),
        )
    result = await router.execute(
        request=ModelExecutionRequest(prompt="hello"),
        requirements=RoutingRequirements(
            requested_provider="requested",
            allow_fallback=True,
            maximum_fallbacks=1,
        ),
        budget=TaskBudget(maximum_requests=1),
    )
    assert result.provider == "first-fallback"
    assert result.routing_metadata["fallback_count"] == 1
    assert second_fallback.execute_calls == []


@pytest.mark.asyncio
async def test_retry_then_success_counts_every_attempt() -> None:
    provider = FakeProvider(
        "local",
        results=[ProviderUnavailableError("temporary"), _response("local", "local-model")],
    )
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)

    result = await _router(provider, attempts=2, sleep=sleep).execute(
        request=ModelExecutionRequest(prompt="hello"),
        requirements=RoutingRequirements(),
        budget=TaskBudget(maximum_requests=2),
    )
    assert result.content == "answer"
    assert len(provider.execute_calls) == 2
    assert delays == [0.25]


@pytest.mark.asyncio
async def test_cost_cap_stops_retry_after_ambiguous_failure() -> None:
    provider = FakeProvider(
        "local",
        results=[
            RequestTimeoutError("ambiguous", provider="local"),
            _response("local", "local-model"),
        ],
    )
    with pytest.raises(BudgetExceededError) as raised:
        await _router(provider, attempts=2).execute(
            request=ModelExecutionRequest(prompt="hello"),
            requirements=RoutingRequirements(),
            budget=TaskBudget(maximum_requests=2, maximum_cost_usd=Decimal("1")),
            pricing={
                "local-model": ModelPricing(
                    input_per_million_usd=1,
                    output_per_million_usd=1,
                )
            },
        )
    assert raised.value.metadata == {"reason": "ambiguous_attempt_usage"}
    assert len(provider.execute_calls) == 1


@pytest.mark.asyncio
async def test_cost_cap_ambiguous_failure_cannot_fall_back() -> None:
    first = FakeProvider("first", results=[RequestTimeoutError("ambiguous", provider="first")])
    second = FakeProvider("second")
    pricing = {
        name: ModelPricing(input_per_million_usd=1, output_per_million_usd=1)
        for name in ("first-model", "second-model")
    }
    with pytest.raises(BudgetExceededError):
        await _router(first, second).execute(
            request=ModelExecutionRequest(prompt="hello"),
            requirements=RoutingRequirements(
                allow_fallback=True,
                maximum_fallbacks=1,
            ),
            budget=TaskBudget(maximum_requests=2, maximum_cost_usd=Decimal("1")),
            pricing=pricing,
        )
    assert second.execute_calls == []


@pytest.mark.asyncio
async def test_exact_bounded_fallback_uses_each_provider_default_model() -> None:
    first = FakeProvider(
        "first",
        model="first-default",
        results=[ProviderUnavailableError("temporary", provider="first")],
    )
    second = FakeProvider("second", model="second-default")
    result = await _router(first, second).execute(
        request=ModelExecutionRequest(prompt="hello"),
        requirements=RoutingRequirements(allow_fallback=True, maximum_fallbacks=1),
        budget=TaskBudget(maximum_requests=2),
    )
    assert first.execute_calls == ["first-default"]
    assert second.execute_calls == ["second-default"]
    assert result.routing_metadata == {
        "selected_provider": "second",
        "selected_model": "second-default",
        "is_local": True,
        "fallback_count": 1,
        "attempted_providers": ["first", "second"],
        "failure_categories": ["provider_unavailable"],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        AuthenticationError("no", provider="first"),
        BudgetExceededError("no", provider="first"),
    ],
)
async def test_auth_and_budget_never_fall_back(error: Exception) -> None:
    first = FakeProvider("first", results=[error])
    second = FakeProvider("second")
    with pytest.raises(type(error)):
        await _router(first, second).execute(
            request=ModelExecutionRequest(prompt="hello"),
            requirements=RoutingRequirements(allow_fallback=True, maximum_fallbacks=1),
            budget=TaskBudget(maximum_requests=2),
        )
    assert second.execute_calls == []


@pytest.mark.asyncio
async def test_hard_quota_fallback_requires_explicit_permission() -> None:
    first = FakeProvider("first", results=[QuotaExhaustedError("quota", provider="first")])
    second = FakeProvider("second")
    with pytest.raises(QuotaExhaustedError):
        await _router(first, second).execute(
            request=ModelExecutionRequest(prompt="hello"),
            requirements=RoutingRequirements(allow_fallback=True, maximum_fallbacks=1),
            budget=TaskBudget(maximum_requests=2),
        )
    first.results = [QuotaExhaustedError("quota", provider="first")]
    result = await _router(first, second).execute(
        request=ModelExecutionRequest(prompt="hello"),
        requirements=RoutingRequirements(
            allow_fallback=True,
            allow_quota_fallback=True,
            maximum_fallbacks=1,
        ),
        budget=TaskBudget(maximum_requests=2),
    )
    assert result.provider == "second"


def test_registry_order_duplicates_unknown_and_capabilities() -> None:
    first = FakeProvider("first")
    second = FakeProvider("second", capabilities=frozenset({ModelCapability.REASONING}))
    registry = ProviderRegistry([first, second])
    assert [item.name for item in registry.list()] == ["first", "second"]
    assert registry.by_capability(ModelCapability.REASONING) == [second]
    with pytest.raises(DuplicateProviderError):
        ProviderRegistry([first, first])
    with pytest.raises(UnknownProviderError):
        registry.get("missing")


@pytest.mark.asyncio
async def test_registry_registration_is_concurrency_safe() -> None:
    registry = ProviderRegistry()
    provider = FakeProvider("same")
    results = await __import__("asyncio").gather(
        registry.register(provider), registry.register(provider), return_exceptions=True
    )
    assert sum(isinstance(item, DuplicateProviderError) for item in results) == 1


def test_budget_counts_failed_attempts_and_preflights_output() -> None:
    tracker = BudgetTracker(TaskBudget(maximum_requests=1, maximum_output_tokens=2))
    request = ModelExecutionRequest(prompt="hello", model="m", max_output_tokens=2)
    tracker.before_attempt(request)
    with pytest.raises(BudgetExceededError):
        tracker.before_attempt(request)
    with pytest.raises(BudgetExceededError):
        BudgetTracker(TaskBudget(maximum_requests=1, maximum_output_tokens=1)).before_attempt(
            request
        )


def test_budget_exact_cost_zero_usage_and_limits() -> None:
    tracker = BudgetTracker(
        TaskBudget(maximum_requests=1, maximum_cost_usd=Decimal("1")),
        {
            "m": ModelPricing(
                input_per_million_usd=Decimal("2"),
                output_per_million_usd=Decimal("4"),
            )
        },
    )
    request = ModelExecutionRequest(prompt="hello", model="m")
    tracker.before_attempt(request)
    assert tracker.record(_response("p", "m", input_tokens=0, output_tokens=0, total_tokens=0)) == 0
    assert tracker.usage.cost_usd == Decimal("0")


@pytest.mark.parametrize(
    ("budget", "pricing", "response"),
    [
        (
            TaskBudget(maximum_requests=1, maximum_cost_usd=Decimal("1")),
            {},
            _response("p", "m"),
        ),
        (
            TaskBudget(maximum_requests=1, maximum_cost_usd=Decimal("1")),
            {"m": ModelPricing(input_per_million_usd=1, output_per_million_usd=1)},
            _response("p", "alias"),
        ),
        (
            TaskBudget(maximum_requests=1, maximum_cost_usd=Decimal("1")),
            {"m": ModelPricing(input_per_million_usd=1, output_per_million_usd=1)},
            _response("p", "m", input_tokens=None, output_tokens=None, total_tokens=None),
        ),
        (
            TaskBudget(maximum_requests=1, maximum_total_tokens=1),
            {},
            _response("p", "m", input_tokens=None, output_tokens=None, total_tokens=None),
        ),
    ],
)
def test_cost_and_token_budgets_fail_closed(
    budget: TaskBudget,
    pricing: dict[str, ModelPricing],
    response: ModelExecutionResponse,
) -> None:
    tracker = BudgetTracker(budget, pricing)
    request = ModelExecutionRequest(prompt="hello", model="m")
    if budget.maximum_cost_usd is not None and "m" not in pricing:
        with pytest.raises(BudgetExceededError):
            tracker.before_attempt(request)
        return
    tracker.before_attempt(request)
    with pytest.raises(BudgetExceededError):
        tracker.record(response)


def test_unknown_usage_is_allowed_without_relevant_limits_or_cost_cap() -> None:
    tracker = BudgetTracker(TaskBudget(maximum_requests=1))
    tracker.before_attempt(ModelExecutionRequest(prompt="hello", model="unpriced"))
    assert (
        tracker.record(
            _response(
                "p",
                "unpriced",
                input_tokens=None,
                output_tokens=None,
                total_tokens=None,
            )
        )
        is None
    )


def test_factory_defaults_pricing_and_stable_priority() -> None:
    settings = Settings(
        _env_file=None,
        JARVIS_MODEL_OLLAMA_ENABLED=True,
        JARVIS_MODEL_OPENAI_COMPATIBLE_ENABLED=True,
        JARVIS_MODEL_OPENAI_COMPATIBLE_API_KEY="mock",
        JARVIS_MODEL_PROVIDER_PRIORITY="openai-compatible,ollama",
        JARVIS_MODEL_DEFAULT_MAXIMUM_REQUESTS=3,
        JARVIS_MODEL_DEFAULT_MAXIMUM_INPUT_TOKENS=10,
        JARVIS_MODEL_PRICING_JSON=('{"m":{"input_per_million_usd":1,"output_per_million_usd":2}}'),
    )
    assert [item.name for item in build_provider_registry(settings).list()] == [
        "openai-compatible",
        "ollama",
    ]
    assert build_default_task_budget(settings).maximum_input_tokens == 10
    assert build_default_routing_requirements(settings).allow_remote is False
    assert build_model_pricing(settings)["m"].output_per_million_usd == Decimal("2.0")
