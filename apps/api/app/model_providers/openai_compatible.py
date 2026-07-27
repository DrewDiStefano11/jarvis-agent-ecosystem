from __future__ import annotations

from enum import StrEnum
from time import perf_counter
from typing import Any

import httpx
from pydantic import SecretStr

from app.model_providers.base import ProviderBase
from app.model_providers.contracts import (
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


class HealthCheckStrategy(StrEnum):
    MODELS = "models"
    ROOT = "root"
    CONFIGURATION = "configuration"


class OpenAICompatibleProvider(ProviderBase):
    provider_type = ProviderType.OPENAI_COMPATIBLE
    is_local = False

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: SecretStr,
        default_model: str,
        timeout_seconds: float = 30,
        capabilities: frozenset[ModelCapability] = frozenset(
            {ModelCapability.CHAT, ModelCapability.TEXT_GENERATION}
        ),
        health_strategy: HealthCheckStrategy = HealthCheckStrategy.MODELS,
        custom_headers: dict[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key.get_secret_value():
            raise ProviderConfigurationError("an API key is required", provider=name)
        self.name = name
        self.base_url = f"{base_url.rstrip('/')}/"
        self.api_key = api_key
        self.default_model = default_model
        self.timeout_seconds = timeout_seconds
        self.capabilities = capabilities
        self.health_strategy = health_strategy
        self.custom_headers = custom_headers or {}
        self._client = client

    def _headers(self) -> dict[str, str]:
        return {
            **self.custom_headers,
            "Authorization": f"Bearer {self.api_key.get_secret_value()}",
        }

    def _client_or_new(self) -> tuple[httpx.AsyncClient, bool]:
        if self._client is not None:
            return self._client, False
        return (
            httpx.AsyncClient(
                base_url=self.base_url,
                headers=self._headers(),
                timeout=self.timeout_seconds,
            ),
            True,
        )

    async def execute(self, request: ModelExecutionRequest) -> ModelExecutionResponse:
        model = request.model or self.default_model
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {
                    key: value
                    for key, value in {
                        "role": message.role.value,
                        "content": message.content,
                        "name": message.name,
                    }.items()
                    if value is not None
                }
                for message in request.messages
            ],
            "stream": False,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            payload["max_tokens"] = request.max_output_tokens
        client, owned = self._client_or_new()
        started = perf_counter()
        try:
            response = await client.post(
                "chat/completions",
                json=payload,
                headers=self._headers() if self._client is not None else None,
                timeout=request.timeout_seconds or self.timeout_seconds,
            )
            response.raise_for_status()
            try:
                body = response.json()
            except ValueError as exc:
                raise MalformedProviderResponseError(
                    "provider returned malformed JSON", provider=self.name, model=model
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
        try:
            choices = body["choices"]
            content = choices[0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise MalformedProviderResponseError(
                "provider response contains no generated choice", provider=self.name, model=model
            ) from exc
        if not isinstance(content, str) or not content:
            raise MalformedProviderResponseError(
                "provider response is missing generated content", provider=self.name, model=model
            )
        usage = body.get("usage", {})
        if not isinstance(usage, dict):
            raise MalformedProviderResponseError(
                "provider usage is malformed", provider=self.name, model=model
            )
        input_tokens = _integer(usage.get("prompt_tokens"), self.name, model)
        output_tokens = _integer(usage.get("completion_tokens"), self.name, model)
        total_tokens = _integer(usage.get("total_tokens"), self.name, model)
        return ModelExecutionResponse(
            content=content,
            provider=self.name,
            model=str(body.get("model") or model),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            usage_quality=UsageQuality.EXACT if usage else UsageQuality.UNKNOWN,
            latency_ms=(perf_counter() - started) * 1000,
            finish_reason=choices[0].get("finish_reason"),
            task_id=request.task_id,
            correlation_id=request.correlation_id,
            request_id=response.headers.get("x-request-id") or body.get("id"),
        )

    async def health_check(self) -> ProviderHealth:
        started = perf_counter()
        if self.health_strategy == HealthCheckStrategy.CONFIGURATION:
            return ProviderHealth(
                provider=self.name,
                healthy=True,
                status=HealthStatus.CONFIGURATION_ONLY,
                latency_ms=0,
                model_available=None,
                detail="configuration validated; remote check disabled",
            )
        client, owned = self._client_or_new()
        path = "models" if self.health_strategy == HealthCheckStrategy.MODELS else ""
        try:
            response = await client.get(
                path, headers=self._headers() if self._client is not None else None
            )
            response.raise_for_status()
            available = None
            if self.health_strategy == HealthCheckStrategy.MODELS:
                body = response.json()
                if not isinstance(body, dict):
                    raise MalformedProviderResponseError(
                        "provider models response must be an object",
                        provider=self.name,
                        model=self.default_model,
                    )
                data = body.get("data")
                if not isinstance(data, list):
                    raise MalformedProviderResponseError(
                        "provider models response data must be a list",
                        provider=self.name,
                        model=self.default_model,
                    )
                if any(
                    not isinstance(item, dict) or not isinstance(item.get("id"), str)
                    for item in data
                ):
                    raise MalformedProviderResponseError(
                        "provider models response contains an invalid model entry",
                        provider=self.name,
                        model=self.default_model,
                    )
                available = self.default_model in {item["id"] for item in data}
            return ProviderHealth(
                provider=self.name,
                healthy=available is not False,
                status=HealthStatus.HEALTHY if available is not False else HealthStatus.DEGRADED,
                latency_ms=(perf_counter() - started) * 1000,
                model_available=available,
            )
        except MalformedProviderResponseError as error:
            return ProviderHealth(
                provider=self.name,
                healthy=False,
                status=HealthStatus.UNAVAILABLE,
                latency_ms=(perf_counter() - started) * 1000,
                error_category=type(error).__name__,
                detail=error.message,
            )
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            error = translate_http_error(exc, provider=self.name, model=self.default_model)
            return ProviderHealth(
                provider=self.name,
                healthy=False,
                status=HealthStatus.UNAVAILABLE,
                latency_ms=(perf_counter() - started) * 1000,
                error_category=type(error).__name__,
                detail=error.message,
            )
        finally:
            if owned:
                await client.aclose()


def _integer(value: object, provider: str, model: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    raise MalformedProviderResponseError(
        "provider token usage is malformed", provider=provider, model=model
    )
