from __future__ import annotations

import httpx
import pytest
from pydantic import SecretStr

import app.model_providers.ollama as ollama_module
import app.model_providers.openai_compatible as openai_module
from app.model_providers.budget import BudgetTracker, TaskBudget
from app.model_providers.contracts import HealthStatus, ModelCapability, ModelExecutionRequest
from app.model_providers.errors import (
    AuthenticationError,
    InvalidModelRequestError,
    MalformedProviderResponseError,
    ModelUnavailableError,
    ProviderConfigurationError,
    ProviderExecutionDisabledError,
    ProviderUnavailableError,
    QuotaExhaustedError,
    RateLimitError,
    RequestTimeoutError,
    TransientProviderError,
)
from app.model_providers.ollama import OllamaProvider
from app.model_providers.openai_compatible import (
    HealthCheckStrategy,
    OpenAICompatibleProvider,
)
from app.model_providers.registry import ProviderRegistry
from app.model_providers.retry import RetryExecutor, RetryPolicy
from app.model_providers.router import ModelRouter, RoutingRequirements


@pytest.fixture(autouse=True)
def allow_mocked_adapter_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def allow_execution(**kwargs: object) -> None:
        return None

    monkeypatch.setattr(ollama_module, "require_live_provider_execution", allow_execution)
    monkeypatch.setattr(openai_module, "require_live_provider_execution", allow_execution)
    monkeypatch.setattr(ollama_module, "provider_network_health_allowed", lambda: True)
    monkeypatch.setattr(openai_module, "provider_network_health_allowed", lambda: True)


def mock_client(
    handler: httpx.MockTransport, base_url: str = "https://provider.test/v1"
) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler, base_url=base_url)


@pytest.mark.parametrize(
    "capability",
    [
        ModelCapability.TOOL_CALLING,
        ModelCapability.STRUCTURED_OUTPUT,
        ModelCapability.VISION,
    ],
)
def test_builtin_adapter_construction_rejects_unsupported_capabilities(
    capability: ModelCapability,
) -> None:
    with pytest.raises(ProviderConfigurationError):
        OllamaProvider(default_model="model", capabilities=frozenset({capability}))
    with pytest.raises(ProviderConfigurationError):
        OpenAICompatibleProvider(
            name="remote",
            base_url="https://provider.test/v1",
            api_key=SecretStr("fixture-only"),
            default_model="model",
            capabilities=frozenset({ModelCapability.CHAT, capability}),
        )


@pytest.mark.parametrize(
    "capabilities",
    [
        frozenset({ModelCapability.CHAT}),
        frozenset({ModelCapability.TEXT_GENERATION}),
        frozenset({ModelCapability.CODE_GENERATION}),
        frozenset({ModelCapability.CODE_EDITING}),
        frozenset({ModelCapability.REASONING}),
        frozenset(
            {
                ModelCapability.CHAT,
                ModelCapability.TEXT_GENERATION,
                ModelCapability.CODE_GENERATION,
                ModelCapability.CODE_EDITING,
                ModelCapability.REASONING,
            }
        ),
    ],
)
def test_builtin_adapter_construction_accepts_supported_capabilities(
    capabilities: frozenset[ModelCapability],
) -> None:
    assert (
        OllamaProvider(default_model="model", capabilities=capabilities).capabilities
        == capabilities
    )
    assert (
        OpenAICompatibleProvider(
            name="remote",
            base_url="https://provider.test/v1",
            api_key=SecretStr("fixture-only"),
            default_model="model",
            capabilities=capabilities,
        ).capabilities
        == capabilities
    )


@pytest.mark.asyncio
async def test_phase_policy_blocks_execution_and_remote_health_before_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.model_providers.policy import (
        provider_network_health_allowed,
        require_live_provider_execution,
    )

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise AssertionError(f"phase gate allowed network request: {request.url}")

    monkeypatch.setattr(
        openai_module, "require_live_provider_execution", require_live_provider_execution
    )
    monkeypatch.setattr(
        openai_module, "provider_network_health_allowed", provider_network_health_allowed
    )
    monkeypatch.setattr(
        ollama_module, "require_live_provider_execution", require_live_provider_execution
    )
    monkeypatch.setattr(
        ollama_module, "provider_network_health_allowed", provider_network_health_allowed
    )
    async with mock_client(httpx.MockTransport(handler)) as client:
        remote = OpenAICompatibleProvider(
            name="remote",
            base_url="https://provider.test/v1",
            api_key=SecretStr("fixture-only"),
            default_model="model-a",
            client=client,
        )
        with pytest.raises(ProviderExecutionDisabledError):
            await remote.execute(ModelExecutionRequest(prompt="hello"))
        remote_health = await remote.health_check()
        availability = await remote.model_available("model-a")
        local = OllamaProvider(default_model="qwen:4b", client=client)
        with pytest.raises(ProviderExecutionDisabledError):
            await local.execute(ModelExecutionRequest(prompt="hello"))
        local_health = await local.health_check()

    assert calls == 0
    assert remote_health.status == HealthStatus.CONFIGURATION_ONLY
    assert local_health.status == HealthStatus.CONFIGURATION_ONLY
    assert availability is None


@pytest.mark.asyncio
async def test_ollama_success_and_health_normalize_usage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/tags"):
            return httpx.Response(200, json={"models": [{"name": "qwen:4b"}]})
        return httpx.Response(
            200,
            json={
                "model": "qwen:4b",
                "message": {"content": "local answer"},
                "prompt_eval_count": 4,
                "eval_count": 3,
                "done_reason": "stop",
            },
        )

    async with mock_client(httpx.MockTransport(handler)) as client:
        provider = OllamaProvider(default_model="qwen:4b", client=client)
        health = await provider.health_check()
        result = await provider.execute(ModelExecutionRequest(prompt="hello"))
    assert health.healthy and health.model_available
    assert result.content == "local answer"
    assert result.total_tokens == 7


@pytest.mark.asyncio
async def test_ollama_missing_model_and_malformed_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/tags"):
            return httpx.Response(200, json={"models": []})
        return httpx.Response(200, json={"message": {}})

    async with mock_client(httpx.MockTransport(handler)) as client:
        provider = OllamaProvider(default_model="missing", client=client)
        health = await provider.health_check()
        assert health.healthy
        assert health.model_available is False
        with pytest.raises(MalformedProviderResponseError):
            await provider.execute(ModelExecutionRequest(prompt="hello"))


@pytest.mark.asyncio
async def test_openai_compatible_success_and_secret_header() -> None:
    secret = "super-secret-test-key"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {secret}"
        return httpx.Response(
            200,
            headers={"x-request-id": "remote-1"},
            json={
                "model": "model-a",
                "choices": [{"message": {"content": "remote answer"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
            },
        )

    async with mock_client(httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            name="remote",
            base_url="https://provider.test/v1",
            api_key=SecretStr(secret),
            default_model="model-a",
            client=client,
        )
        result = await provider.execute(ModelExecutionRequest(prompt="hello"))
    assert result.total_tokens == 7
    assert result.request_id == "remote-1"
    assert secret not in repr(provider)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("id", 42, "invalid_response_id"),
        ("id", {"nested": "response-content-secret"}, "invalid_response_id"),
        ("id", True, "invalid_response_id"),
        ("finish_reason", 7, "invalid_finish_reason"),
        ("finish_reason", ["stop"], "invalid_finish_reason"),
        ("finish_reason", {"value": "stop"}, "invalid_finish_reason"),
        ("finish_reason", False, "invalid_finish_reason"),
        ("model", ["model-a"], "invalid_response_model"),
    ],
)
async def test_openai_normalizes_malformed_optional_response_fields(
    field: str,
    value: object,
    reason: str,
) -> None:
    secret = "response-content-secret"

    def handler(request: httpx.Request) -> httpx.Response:
        body: dict[str, object] = {
            "id": "request-1",
            "model": "model-a",
            "choices": [
                {
                    "message": {"content": secret},
                    "finish_reason": "stop",
                }
            ],
        }
        if field == "finish_reason":
            choices = body["choices"]
            assert isinstance(choices, list)
            choice = choices[0]
            assert isinstance(choice, dict)
            choice[field] = value
        else:
            body[field] = value
        return httpx.Response(200, json=body)

    async with mock_client(httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            name="remote",
            base_url="https://provider.test/v1",
            api_key=SecretStr("api-key-secret"),
            default_model="model-a",
            client=client,
        )
        with pytest.raises(MalformedProviderResponseError) as raised:
            await provider.execute(
                ModelExecutionRequest(
                    prompt="hello",
                    task_id="task-1",
                    correlation_id="correlation-1",
                )
            )

    details = raised.value.safe_details()
    assert details["provider"] == "remote"
    assert details["model"] == "model-a"
    assert details["task_id"] == "task-1"
    assert details["correlation_id"] == "correlation-1"
    assert details["metadata"]["reason"] == reason
    assert secret not in str(details)
    assert "api-key-secret" not in str(details)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response_id", "finish_reason"),
    [
        (None, None),
        ("request-1", "length"),
    ],
)
async def test_openai_preserves_valid_optional_response_fields(
    response_id: str | None,
    finish_reason: str | None,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": response_id,
                "model": "model-a",
                "choices": [
                    {
                        "message": {"content": "answer"},
                        "finish_reason": finish_reason,
                    }
                ],
            },
        )

    async with mock_client(httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            name="remote",
            base_url="https://provider.test/v1",
            api_key=SecretStr("fixture-only"),
            default_model="model-a",
            client=client,
        )
        result = await provider.execute(ModelExecutionRequest(prompt="hello"))

    assert result.request_id == response_id
    assert result.finish_reason == finish_reason


@pytest.mark.asyncio
async def test_router_falls_back_after_normalized_malformed_optional_field() -> None:
    calls = {"malformed": 0, "fallback": 0}

    def malformed_handler(request: httpx.Request) -> httpx.Response:
        calls["malformed"] += 1
        return httpx.Response(
            200,
            json={
                "id": 42,
                "model": "first-model",
                "choices": [{"message": {"content": "unsafe response content"}}],
            },
        )

    def fallback_handler(request: httpx.Request) -> httpx.Response:
        calls["fallback"] += 1
        return httpx.Response(
            200,
            json={
                "id": "fallback-request",
                "model": "second-model",
                "choices": [{"message": {"content": "safe fallback"}}],
            },
        )

    async with (
        mock_client(httpx.MockTransport(malformed_handler)) as malformed_client,
        mock_client(httpx.MockTransport(fallback_handler)) as fallback_client,
    ):
        malformed = OpenAICompatibleProvider(
            name="malformed",
            base_url="https://provider.test/v1",
            api_key=SecretStr("fixture-only"),
            default_model="first-model",
            health_strategy=HealthCheckStrategy.CONFIGURATION,
            client=malformed_client,
        )
        fallback = OpenAICompatibleProvider(
            name="fallback",
            base_url="https://provider.test/v1",
            api_key=SecretStr("fixture-only"),
            default_model="second-model",
            health_strategy=HealthCheckStrategy.CONFIGURATION,
            client=fallback_client,
        )
        result = await ModelRouter(
            ProviderRegistry([malformed, fallback]),
            RetryExecutor(RetryPolicy(maximum_attempts=2)),
        ).execute(
            request=ModelExecutionRequest(prompt="hello"),
            requirements=RoutingRequirements(
                allow_remote=True,
                allow_fallback=True,
                maximum_fallbacks=1,
            ),
            budget=TaskBudget(maximum_requests=3),
        )

    assert result.provider == "fallback"
    assert result.routing_metadata["failure_categories"] == [
        MalformedProviderResponseError.__name__
    ]
    assert calls == {"malformed": 1, "fallback": 1}


@pytest.mark.asyncio
async def test_openai_preserves_versioned_base_url_for_execution_and_health() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "model-a"}]})
        return httpx.Response(
            200,
            json={
                "model": "model-a",
                "choices": [{"message": {"content": "answer"}}],
            },
        )

    async with mock_client(httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            name="remote",
            base_url="https://provider.test/v1",
            api_key=SecretStr("fixture-only"),
            default_model="model-a",
            health_strategy=HealthCheckStrategy.MODELS,
            client=client,
        )
        assert (await provider.health_check()).healthy
        await provider.execute(ModelExecutionRequest(prompt="hello"))

    assert seen == [
        "https://provider.test/v1/models",
        "https://provider.test/v1/chat/completions",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        [],
        {"data": "not-a-list"},
        {"data": ["health-response-secret"]},
    ],
)
async def test_openai_health_normalizes_malformed_valid_json(body: object) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    async with mock_client(httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            name="remote",
            base_url="https://provider.test/v1",
            api_key=SecretStr("api-key-health-secret"),
            default_model="model-a",
            client=client,
        )
        health = await provider.health_check()

    assert not health.healthy
    assert health.error_category == MalformedProviderResponseError.__name__
    diagnostics = health.model_dump_json()
    assert "health-response-secret" not in diagnostics
    assert "api-key-health-secret" not in diagnostics


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        [],
        {"models": "not-a-list"},
        {"models": ["health-response-secret"]},
    ],
)
async def test_ollama_health_normalizes_malformed_valid_json(body: object) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    async with mock_client(httpx.MockTransport(handler)) as client:
        provider = OllamaProvider(default_model="qwen:4b", client=client)
        health = await provider.health_check()

    assert not health.healthy
    assert health.error_category == MalformedProviderResponseError.__name__
    assert "health-response-secret" not in health.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [[], {"models": "not-a-list"}])
async def test_ollama_model_available_returns_unknown_for_malformed_json(
    body: object,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    async with mock_client(httpx.MockTransport(handler)) as client:
        provider = OllamaProvider(default_model="qwen:4b", client=client)
        assert await provider.model_available("qwen:4b") is None


@pytest.mark.asyncio
async def test_openai_preserves_gemini_base_path_prefix() -> None:
    expected = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == expected
        return httpx.Response(
            200,
            json={
                "model": "gemini-model",
                "choices": [{"message": {"content": "answer"}}],
            },
        )

    base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
    async with mock_client(httpx.MockTransport(handler), base_url) as client:
        provider = OpenAICompatibleProvider(
            name="gemini",
            base_url=f"{base_url}/",
            api_key=SecretStr("fixture-only"),
            default_model="gemini-model",
            client=client,
        )
        await provider.execute(ModelExecutionRequest(prompt="hello"))


@pytest.mark.asyncio
async def test_owned_openai_client_normalizes_base_url_trailing_slash() -> None:
    provider = OpenAICompatibleProvider(
        name="remote",
        base_url="https://provider.test/v1",
        api_key=SecretStr("fixture-only"),
        default_model="model-a",
    )
    client, owned = provider._client_or_new()
    try:
        assert owned
        assert str(client.base_url) == "https://provider.test/v1/"
        assert (
            str(client.build_request("POST", "chat/completions").url)
            == "https://provider.test/v1/chat/completions"
        )
    finally:
        await client.aclose()


def gemini_quota_payload(quota_id: object, quota_metric: object) -> dict:
    return {
        "error": {
            "code": 429,
            "message": "Quota exhausted.",
            "status": "RESOURCE_EXHAUSTED",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                    "violations": [
                        {
                            "quotaId": quota_id,
                            "quotaMetric": quota_metric,
                            "quotaDimensions": {"model": "gemini-model", "location": "global"},
                        }
                    ],
                },
                {
                    "@type": "type.googleapis.com/google.rpc.RetryInfo",
                    "retryDelay": "40s",
                },
            ],
        }
    }


@pytest.mark.asyncio
async def test_gemini_minute_quota_is_retryable_from_nested_quota_id() -> None:
    payload = gemini_quota_payload(
        "GenerateRequestsPerMinutePerProjectPerModel-FreeTier",
        "generativelanguage.googleapis.com/generate_requests_per_model_per_minute",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "40"}, json=payload)

    async with mock_client(httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            name="gemini",
            base_url="https://provider.test/v1",
            api_key=SecretStr("fixture-only"),
            default_model="gemini-model",
            client=client,
        )
        with pytest.raises(RateLimitError) as raised:
            await provider.execute(ModelExecutionRequest(prompt="hello"))
    assert raised.value.retryable
    assert raised.value.retry_after_seconds == 40


@pytest.mark.asyncio
async def test_gemini_daily_quota_is_nonretryable_and_attempted_once() -> None:
    secret = "daily-quota-fixture-key"
    calls = 0
    payload = gemini_quota_payload(
        "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
        "generativelanguage.googleapis.com/generate_requests_per_model_per_day",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"retry-after": "40"}, json=payload)

    async def fail_if_slept(delay: float) -> None:
        raise AssertionError(f"hard quota attempted retry sleep: {delay}")

    async with mock_client(httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            name="gemini",
            base_url="https://provider.test/v1",
            api_key=SecretStr(secret),
            default_model="gemini-model",
            client=client,
        )
        request = ModelExecutionRequest(prompt="hello")
        with pytest.raises(QuotaExhaustedError) as raised:
            await RetryExecutor(
                RetryPolicy(maximum_attempts=3),
                sleep=fail_if_slept,
            ).execute(
                lambda: provider.execute(request),
                request=request,
                budget=BudgetTracker(TaskBudget(maximum_requests=3)),
            )
    assert calls == 1
    assert not raised.value.retryable
    serialized = str(raised.value.safe_details())
    assert secret not in serialized
    assert "GenerateRequestsPerDayPerProjectPerModel-FreeTier" not in serialized


@pytest.mark.asyncio
async def test_malformed_gemini_quota_details_do_not_crash_classification() -> None:
    payload = gemini_quota_payload(
        {"unexpected": "mapping"},
        ["unexpected", "sequence"],
    )
    payload["error"]["details"].extend([None, "bad detail", {"violations": "bad"}])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json=payload)

    async with mock_client(httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            name="gemini",
            base_url="https://provider.test/v1",
            api_key=SecretStr("fixture-only"),
            default_model="gemini-model",
            client=client,
        )
        with pytest.raises(QuotaExhaustedError) as raised:
            await provider.execute(ModelExecutionRequest(prompt="hello"))
    assert not raised.value.retryable


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "payload", "error_type"),
    [
        (401, {"error": {"message": "bad key"}}, AuthenticationError),
        (
            429,
            {
                "error": {
                    "status": "RESOURCE_EXHAUSTED",
                    "message": "per-minute quota reached",
                }
            },
            RateLimitError,
        ),
        (
            429,
            {"error": {"status": "RESOURCE_EXHAUSTED", "message": "per-day quota exhausted"}},
            QuotaExhaustedError,
        ),
        (500, {"error": {"message": "temporary"}}, TransientProviderError),
    ],
)
async def test_openai_error_translation(
    status: int, payload: dict, error_type: type[Exception]
) -> None:
    secret = "never-leak-this-key"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers={"retry-after": "2"}, json=payload)

    async with mock_client(httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            name="remote",
            base_url="https://provider.test/v1",
            api_key=SecretStr(secret),
            default_model="model-a",
            client=client,
        )
        with pytest.raises(error_type) as raised:
            await provider.execute(ModelExecutionRequest(prompt="hello"))
    assert secret not in str(raised.value)
    if error_type is RateLimitError:
        assert raised.value.retry_after_seconds == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (400, InvalidModelRequestError),
        (403, AuthenticationError),
        (404, ModelUnavailableError),
        (408, RequestTimeoutError),
        (422, InvalidModelRequestError),
    ],
)
async def test_openai_additional_status_translation(
    status: int, error_type: type[Exception]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": "safe fixture failure"}})

    async with mock_client(httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            name="remote",
            base_url="https://provider.test/v1",
            api_key=SecretStr("fixture-only"),
            default_model="model-a",
            client=client,
        )
        with pytest.raises(error_type):
            await provider.execute(ModelExecutionRequest(prompt="hello"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "error_type"),
    [
        (httpx.ConnectError("connection refused"), ProviderUnavailableError),
        (httpx.ReadTimeout("timed out"), RequestTimeoutError),
    ],
)
async def test_openai_network_translation(
    failure: httpx.HTTPError, error_type: type[Exception]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise failure

    async with mock_client(httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            name="remote",
            base_url="https://provider.test/v1",
            api_key=SecretStr("fixture-only"),
            default_model="model-a",
            client=client,
        )
        with pytest.raises(error_type):
            await provider.execute(ModelExecutionRequest(prompt="hello"))


@pytest.mark.asyncio
async def test_openai_malformed_choices_and_usage() -> None:
    responses = iter(
        [
            httpx.Response(200, json={"choices": []}),
            httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "answer"}}],
                    "usage": {"prompt_tokens": "five"},
                },
            ),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return next(responses)

    async with mock_client(httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            name="remote",
            base_url="https://provider.test/v1",
            api_key=SecretStr("fixture-only"),
            default_model="model-a",
            client=client,
        )
        with pytest.raises(MalformedProviderResponseError):
            await provider.execute(ModelExecutionRequest(prompt="hello"))
        with pytest.raises(MalformedProviderResponseError):
            await provider.execute(ModelExecutionRequest(prompt="hello"))
