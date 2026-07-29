from __future__ import annotations

import httpx
import pytest
from pydantic import SecretStr

from app.model_providers.contracts import ModelExecutionRequest, UsageQuality
from app.model_providers.errors import (
    AuthenticationError,
    MalformedProviderResponseError,
    ProviderConfigurationError,
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


def test_provider_urls_and_custom_headers_reject_embedded_secrets() -> None:
    with pytest.raises(ProviderConfigurationError):
        OllamaProvider(default_model="m", base_url="http://user:password@localhost:11434")
    with pytest.raises(ProviderConfigurationError):
        OpenAICompatibleProvider(
            name="remote",
            base_url="https://example.invalid/v1",
            api_key=SecretStr("mock"),
            default_model="m",
            custom_headers={"X-Custom": "Bearer hidden"},
        )


@pytest.mark.parametrize(
    "header_name",
    [
        "X-API-Key",
        "Proxy-Authorization",
        "X-Access-Token",
        "Vendor-Secret",
    ],
)
def test_openai_compatible_rejects_secret_bearing_custom_header_names(
    header_name: str,
) -> None:
    rejected_value = "plain-" + "secret-" + "value"
    with pytest.raises(ProviderConfigurationError) as raised:
        OpenAICompatibleProvider(
            name="remote",
            base_url="https://example.invalid/v1",
            api_key=SecretStr("mock"),
            default_model="m",
            custom_headers={header_name: rejected_value},
        )
    assert raised.value.message == "custom headers cannot override secret-bearing headers"
    assert raised.value.metadata == {}


def test_openai_compatible_accepts_ordinary_custom_headers() -> None:
    provider = OpenAICompatibleProvider(
        name="remote",
        base_url="https://example.invalid/v1",
        api_key=SecretStr("mock"),
        default_model="m",
        custom_headers={
            "X-Organization-ID": "organization-1",
            "X-Request-Source": "jarvis-tests",
        },
    )
    assert provider.custom_headers == {
        "X-Organization-ID": "organization-1",
        "X-Request-Source": "jarvis-tests",
    }


@pytest.mark.parametrize(
    ("provider_type", "name", "default_model"),
    [
        ("ollama", "", "m"),
        ("ollama", "   ", "m"),
        ("ollama", "local", ""),
        ("ollama", "local", "   "),
        ("openai", "", "m"),
        ("openai", "   ", "m"),
        ("openai", "remote", ""),
        ("openai", "remote", "   "),
    ],
)
def test_builtin_provider_construction_rejects_empty_identifiers(
    provider_type: str,
    name: str,
    default_model: str,
) -> None:
    with pytest.raises(ProviderConfigurationError):
        if provider_type == "ollama":
            OllamaProvider(name=name, default_model=default_model)
        else:
            OpenAICompatibleProvider(
                name=name,
                base_url="https://example.invalid/v1",
                api_key=SecretStr("mock"),
                default_model=default_model,
            )


def test_builtin_provider_construction_accepts_valid_identifiers() -> None:
    ollama = OllamaProvider(name="local", default_model="local-model")
    remote = OpenAICompatibleProvider(
        name="remote",
        base_url="https://example.invalid/v1",
        api_key=SecretStr("mock"),
        default_model="remote-model",
    )
    assert ollama.safe_summary().name == "local"
    assert ollama.safe_summary().default_model == "local-model"
    assert remote.safe_summary().name == "remote"
    assert remote.safe_summary().default_model == "remote-model"


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
    ("usage_present", "usage", "expected_quality", "expected_counts"),
    [
        (
            True,
            {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
            UsageQuality.EXACT,
            (2, 3, 5),
        ),
        (
            True,
            {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 1},
            UsageQuality.EXACT,
            (2, 3, 5),
        ),
        (True, {"prompt_tokens": 2}, UsageQuality.UNKNOWN, (2, None, None)),
        (True, {"completion_tokens": 3}, UsageQuality.UNKNOWN, (None, 3, None)),
        (True, {"total_tokens": 5}, UsageQuality.UNKNOWN, (None, None, 5)),
        (True, {}, UsageQuality.UNKNOWN, (None, None, None)),
        (False, {}, UsageQuality.UNKNOWN, (None, None, None)),
    ],
)
async def test_openai_compatible_usage_quality_requires_complete_components(
    monkeypatch: pytest.MonkeyPatch,
    usage_present: bool,
    usage: dict[str, int],
    expected_quality: UsageQuality,
    expected_counts: tuple[int | None, int | None, int | None],
) -> None:
    _allow_execution(monkeypatch)
    body: dict[str, object] = {
        "model": "m",
        "choices": [{"message": {"content": "answer"}, "finish_reason": "stop"}],
    }
    if usage_present:
        body["usage"] = usage
    client = httpx.AsyncClient(
        base_url="https://example.invalid/v1/",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body)),
    )
    provider = OpenAICompatibleProvider(
        name="remote",
        base_url="https://example.invalid/v1",
        api_key=SecretStr("mock"),
        default_model="m",
        client=client,
    )
    response = await provider.execute(ModelExecutionRequest(prompt="hello"))
    assert response.usage_quality == expected_quality
    assert (
        response.input_tokens,
        response.output_tokens,
        response.total_tokens,
    ) == expected_counts
    await client.aclose()


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
