from __future__ import annotations

import httpx
import pytest
from pydantic import SecretStr

from app.model_providers.contracts import ModelExecutionRequest, UsageQuality
from app.model_providers.errors import (
    AuthenticationError,
    MalformedProviderResponseError,
    ProviderExecutionDisabledError,
    QuotaExhaustedError,
    RateLimitError,
    RequestTimeoutError,
    TransientProviderError,
)
from app.model_providers.http import translate_http_error
from app.model_providers.ollama import OllamaProvider, is_loopback_endpoint
from app.model_providers.openai_compatible import (
    HealthCheckStrategy,
    OpenAICompatibleProvider,
)


def _allow_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.model_providers.ollama.require_live_provider_execution", lambda **_: None
    )
    monkeypatch.setattr(
        "app.model_providers.openai_compatible.require_live_provider_execution",
        lambda **_: None,
    )


def _allow_health(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.model_providers.ollama.provider_network_health_allowed", lambda: True)
    monkeypatch.setattr(
        "app.model_providers.openai_compatible.provider_network_health_allowed",
        lambda: True,
    )


@pytest.mark.asyncio
async def test_phase_gate_prevents_client_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = OllamaProvider(default_model="m")
    monkeypatch.setattr(provider, "_client_or_new", lambda: pytest.fail("client created"))
    with pytest.raises(ProviderExecutionDisabledError):
        await provider.execute(ModelExecutionRequest(prompt="hello"))
    health = await provider.health_check()
    assert health.status == "configuration_only"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://localhost:11434", True),
        ("http://localhost.:11434", True),
        ("http://127.0.0.1:11434", True),
        ("http://[::1]:11434", True),
        ("http://127.0.0.2:11434", True),
        ("http://192.168.1.2:11434", False),
        ("http://host.docker.internal:11434", False),
        ("http://localhost.example.com:11434", False),
        ("http://ollama:11434", False),
    ],
)
def test_ollama_locality_is_structural(url: str, expected: bool) -> None:
    assert is_loopback_endpoint(url) is expected


@pytest.mark.asyncio
async def test_ollama_success_health_and_availability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "m"}]})
        return httpx.Response(
            200,
            json={
                "model": "m",
                "message": {"content": "answer"},
                "prompt_eval_count": 3,
                "eval_count": 2,
                "done_reason": "stop",
            },
        )

    _allow_execution(monkeypatch)
    _allow_health(monkeypatch)
    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:11434", transport=httpx.MockTransport(handler)
    )
    provider = OllamaProvider(default_model="m", client=client)
    response = await provider.execute(
        ModelExecutionRequest(prompt="hello", temperature=0.2, max_output_tokens=20)
    )
    assert response.content == "answer"
    assert response.total_tokens == 5
    assert response.usage_quality == UsageQuality.EXACT
    assert (await provider.health_check()).healthy is True
    assert await provider.model_available("m") is True
    sent = requests[0].read().decode()
    assert '"temperature":0.2' in sent
    assert '"num_predict":20' in sent
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"message": {"content": ""}},
        {"message": {"content": "ok"}, "eval_count": "two"},
        {"message": {"content": "ok"}, "done_reason": {"raw": "stop"}},
    ],
)
async def test_ollama_rejects_malformed_content(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, object]
) -> None:
    _allow_execution(monkeypatch)
    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:11434",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
    )
    provider = OllamaProvider(default_model="m", client=client)
    with pytest.raises(MalformedProviderResponseError):
        await provider.execute(ModelExecutionRequest(prompt="hello"))
    await client.aclose()


@pytest.mark.asyncio
async def test_openai_compatible_preserves_versioned_and_gemini_prefixes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "id": "response-id",
                "model": "returned-model",
                "choices": [{"message": {"content": "answer"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
            },
        )

    _allow_execution(monkeypatch)
    for base in (
        "https://example.invalid/v1/",
        "https://example.invalid/v1beta/openai/",
    ):
        client = httpx.AsyncClient(base_url=base, transport=httpx.MockTransport(handler))
        provider = OpenAICompatibleProvider(
            name="remote",
            base_url=base,
            api_key=SecretStr("mock-key"),
            default_model="m",
            client=client,
        )
        response = await provider.execute(ModelExecutionRequest(prompt="hello"))
        assert response.model == "returned-model"
        assert response.request_id == "response-id"
        assert response.total_tokens == 5
        assert seen[-1].url.path.endswith("/chat/completions")
        assert seen[-1].headers["authorization"] == "Bearer mock-key"
        await client.aclose()
    assert seen[0].url.path == "/v1/chat/completions"
    assert seen[1].url.path == "/v1beta/openai/chat/completions"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"choices": []},
        {"choices": [{"message": {"content": ""}}]},
        {"choices": [{"message": {"content": "ok"}}], "usage": []},
        {"choices": [{"message": {"content": "ok"}}], "id": 123},
        {"choices": [{"message": {"content": "ok"}, "finish_reason": {}}]},
        {"choices": [{"message": {"content": "ok"}}], "model": ["m"]},
        {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": True},
        },
    ],
)
async def test_openai_compatible_rejects_malformed_fields(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, object]
) -> None:
    _allow_execution(monkeypatch)
    client = httpx.AsyncClient(
        base_url="https://example.invalid/v1/",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
    )
    provider = OpenAICompatibleProvider(
        name="remote",
        base_url="https://example.invalid/v1",
        api_key=SecretStr("mock"),
        default_model="m",
        client=client,
    )
    with pytest.raises(MalformedProviderResponseError):
        await provider.execute(ModelExecutionRequest(prompt="hello"))
    await client.aclose()


@pytest.mark.asyncio
async def test_openai_health_strategies_do_not_generate_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_health(monkeypatch)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"data": [{"id": "m"}]})

    client = httpx.AsyncClient(
        base_url="https://example.invalid/v1/", transport=httpx.MockTransport(handler)
    )
    provider = OpenAICompatibleProvider(
        name="remote",
        base_url="https://example.invalid/v1",
        api_key=SecretStr("mock"),
        default_model="m",
        health_strategy=HealthCheckStrategy.MODELS,
        client=client,
    )
    assert (await provider.health_check()).model_available is True
    assert await provider.model_available("missing") is False
    assert seen == ["/v1/models", "/v1/models"]
    provider.health_strategy = HealthCheckStrategy.ROOT
    assert (await provider.health_check()).healthy is True
    provider.health_strategy = HealthCheckStrategy.CONFIGURATION
    assert (await provider.health_check()).status == "configuration_only"
    assert all("chat/completions" not in path for path in seen)
    await client.aclose()


@pytest.mark.parametrize(
    ("status", "payload", "expected"),
    [
        (401, {}, AuthenticationError),
        (429, {"error": {"type": "rate_limit"}}, RateLimitError),
        (429, {"error": {"code": "insufficient_quota"}}, QuotaExhaustedError),
        (500, {}, TransientProviderError),
    ],
)
def test_http_errors_are_stably_normalized(
    status: int, payload: dict[str, object], expected: type[Exception]
) -> None:
    request = httpx.Request("GET", "https://example.invalid")
    response = httpx.Response(status, request=request, json=payload)
    error = translate_http_error(
        httpx.HTTPStatusError("raw secret token=abc", request=request, response=response),
        provider="remote",
        model="m",
    )
    assert isinstance(error, expected)
    assert "abc" not in str(error)


def test_timeout_is_normalized_without_raw_details() -> None:
    request = httpx.Request("GET", "https://example.invalid")
    error = translate_http_error(
        httpx.ReadTimeout("Bearer abc", request=request), provider="remote", model="m"
    )
    assert isinstance(error, RequestTimeoutError)
    assert "abc" not in str(error)
