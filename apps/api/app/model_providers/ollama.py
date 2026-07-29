from __future__ import annotations

import ipaddress
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.model_providers.base import ProviderBase
from app.model_providers.contracts import (
    BUILTIN_ADAPTER_CAPABILITIES,
    HealthStatus,
    ModelCapability,
    ModelExecutionRequest,
    ModelExecutionResponse,
    ProviderHealth,
    ProviderType,
    UsageQuality,
)
from app.model_providers.errors import (
    MalformedProviderResponseError,
    ModelProviderError,
    ProviderConfigurationError,
)
from app.model_providers.http import translate_http_error
from app.model_providers.policy import (
    provider_network_health_allowed,
    require_live_provider_execution,
)


class OllamaProvider(ProviderBase):
    provider_type = ProviderType.OLLAMA

    def __init__(
        self,
        *,
        name: str = "ollama",
        base_url: str = "http://127.0.0.1:11434",
        default_model: str,
        timeout_seconds: float = 30,
        capabilities: frozenset[ModelCapability] = frozenset(
            {ModelCapability.CHAT, ModelCapability.TEXT_GENERATION}
        ),
        keep_alive: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ProviderConfigurationError("provider name must not be empty")
        if not isinstance(default_model, str) or not default_model.strip():
            raise ProviderConfigurationError(
                "provider default model must not be empty", provider=name
            )
        if not capabilities or not capabilities <= BUILTIN_ADAPTER_CAPABILITIES:
            raise ProviderConfigurationError(
                "built-in adapter capabilities contain an unsupported value", provider=name
            )
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ProviderConfigurationError("Ollama base URL is invalid", provider=name)
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.is_local = is_loopback_endpoint(base_url)
        self.default_model = default_model
        self.timeout_seconds = timeout_seconds
        self.capabilities = capabilities
        self.keep_alive = keep_alive
        self._client = client

    def _client_or_new(self) -> tuple[httpx.AsyncClient, bool]:
        if self._client is not None:
            return self._client, False
        return httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout_seconds), True

    async def execute(self, request: ModelExecutionRequest) -> ModelExecutionResponse:
        model = request.model or self.default_model
        require_live_provider_execution(
            provider=self.name,
            model=model,
            task_id=request.task_id,
            correlation_id=request.correlation_id,
        )
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": message.role.value, "content": message.content}
                for message in request.messages
            ],
            "stream": False,
        }
        options: dict[str, float | int] = {}
        if request.temperature is not None:
            options["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            options["num_predict"] = request.max_output_tokens
        if options:
            payload["options"] = options
        if self.keep_alive is not None:
            payload["keep_alive"] = self.keep_alive
        client, owned = self._client_or_new()
        started = perf_counter()
        try:
            response = await client.post(
                "/api/chat", json=payload, timeout=request.timeout_seconds or self.timeout_seconds
            )
            response.raise_for_status()
            try:
                body = response.json()
            except ValueError as exc:
                raise MalformedProviderResponseError(
                    "Ollama returned malformed JSON", provider=self.name, model=model
                ) from exc
        except ModelProviderError:
            raise
        except httpx.HTTPError as exc:
            raise translate_http_error(
                exc,
                provider=self.name,
                model=model,
                task_id=request.task_id,
                correlation_id=request.correlation_id,
            ) from exc
        finally:
            if owned:
                await client.aclose()
        if not isinstance(body, dict):
            raise MalformedProviderResponseError(
                "Ollama response must be an object", provider=self.name, model=model
            )
        message = body.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content:
            raise MalformedProviderResponseError(
                "Ollama response is missing generated content", provider=self.name, model=model
            )
        returned_model = _optional_string(body.get("model"), self.name, model)
        finish_reason = _optional_string(body.get("done_reason"), self.name, model)
        input_tokens = _optional_integer(body.get("prompt_eval_count"), self.name, model)
        output_tokens = _optional_integer(body.get("eval_count"), self.name, model)
        return ModelExecutionResponse(
            content=content,
            provider=self.name,
            model=returned_model or model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            usage_quality=(
                UsageQuality.EXACT
                if input_tokens is not None and output_tokens is not None
                else UsageQuality.UNKNOWN
            ),
            latency_ms=(perf_counter() - started) * 1000,
            finish_reason=finish_reason,
            task_id=request.task_id,
            correlation_id=request.correlation_id,
        )

    async def health_check(self) -> ProviderHealth:
        if not provider_network_health_allowed():
            return ProviderHealth(
                provider=self.name,
                healthy=True,
                status=HealthStatus.CONFIGURATION_ONLY,
                latency_ms=0,
                detail="network health disabled by current project phase",
            )
        started = perf_counter()
        try:
            names = await self._list_models()
            return ProviderHealth(
                provider=self.name,
                healthy=True,
                status=HealthStatus.HEALTHY,
                latency_ms=(perf_counter() - started) * 1000,
                model_available=self.default_model in names,
                detail="provider service reachable",
            )
        except ModelProviderError as error:
            return _unhealthy(self.name, started, error)

    async def model_available(self, model: str) -> bool | None:
        if not provider_network_health_allowed():
            return None
        try:
            return model in await self._list_models()
        except ModelProviderError:
            return None

    async def _list_models(self) -> set[str]:
        client, owned = self._client_or_new()
        try:
            response = await client.get("/api/tags")
            response.raise_for_status()
            try:
                body = response.json()
            except ValueError as exc:
                raise MalformedProviderResponseError(
                    "Ollama model-list response is malformed JSON",
                    provider=self.name,
                    model=self.default_model,
                ) from exc
            return _model_names(body, provider=self.name, model=self.default_model)
        except ModelProviderError:
            raise
        except httpx.HTTPError as exc:
            raise translate_http_error(exc, provider=self.name, model=self.default_model) from exc
        finally:
            if owned:
                await client.aclose()


def _optional_integer(value: object, provider: str, model: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    raise MalformedProviderResponseError(
        "Ollama token usage is malformed", provider=provider, model=model
    )


def _optional_string(value: object, provider: str, model: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and value:
        return value
    raise MalformedProviderResponseError(
        "Ollama response contains an invalid optional field", provider=provider, model=model
    )


def _model_names(body: object, *, provider: str, model: str) -> set[str]:
    if not isinstance(body, dict) or not isinstance(body.get("models"), list):
        raise MalformedProviderResponseError(
            "Ollama model-list response is malformed", provider=provider, model=model
        )
    models = body["models"]
    if any(
        not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"]
        for item in models
    ):
        raise MalformedProviderResponseError(
            "Ollama model-list contains an invalid entry", provider=provider, model=model
        )
    return {item["name"] for item in models}


def _unhealthy(provider: str, started: float, error: ModelProviderError) -> ProviderHealth:
    return ProviderHealth(
        provider=provider,
        healthy=False,
        status=HealthStatus.UNAVAILABLE,
        latency_ms=(perf_counter() - started) * 1000,
        error_category=error.category.value,
        detail=error.message,
    )


def is_loopback_endpoint(base_url: str) -> bool:
    hostname = urlsplit(base_url).hostname
    if hostname is None:
        return False
    normalized = hostname.rstrip(".").lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False
