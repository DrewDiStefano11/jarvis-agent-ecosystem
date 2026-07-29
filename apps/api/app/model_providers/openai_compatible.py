from __future__ import annotations

from enum import StrEnum
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr

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
from app.model_providers.http import is_loopback_endpoint, translate_http_error
from app.model_providers.policy import (
    provider_network_health_allowed,
    require_live_provider_execution,
)
from app.model_providers.security import redact_secrets


class HealthCheckStrategy(StrEnum):
    MODELS = "models"
    ROOT = "root"
    CONFIGURATION = "configuration"


class OpenAICompatibleProvider(ProviderBase):
    provider_type = ProviderType.OPENAI_COMPATIBLE

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
        if not isinstance(name, str) or not name.strip():
            raise ProviderConfigurationError("provider name must not be empty")
        if not isinstance(default_model, str) or not default_model.strip():
            raise ProviderConfigurationError(
                "provider default model must not be empty", provider=name
            )
        if not api_key.get_secret_value():
            raise ProviderConfigurationError("an API key is required", provider=name)
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
            raise ProviderConfigurationError("provider base URL is invalid", provider=name)
        headers = custom_headers or {}
        secret_terms = (
            "api_key",
            "authorization",
            "token",
            "password",
            "secret",
            "credential",
        )
        if any(
            any(term in key.lower().replace("-", "_") for term in secret_terms) for key in headers
        ):
            raise ProviderConfigurationError(
                "custom headers cannot override secret-bearing headers", provider=name
            )
        if any(redact_secrets(value) != value for value in headers.values()):
            raise ProviderConfigurationError(
                "custom header values cannot contain secret patterns", provider=name
            )
        self.name = name
        self.base_url = f"{base_url.rstrip('/')}/"
        self.is_local = is_loopback_endpoint(base_url)
        self.api_key = api_key
        self.default_model = default_model
        self.timeout_seconds = timeout_seconds
        self.capabilities = capabilities
        self.health_strategy = health_strategy
        self.custom_headers = dict(headers)
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
        require_live_provider_execution(
            provider=self.name,
            model=model,
            task_id=request.task_id,
            correlation_id=request.correlation_id,
        )
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
        normalized = _normalize_completion(body, provider=self.name, requested_model=model)
        return ModelExecutionResponse(
            content=normalized["content"],
            provider=self.name,
            model=normalized["model"],
            input_tokens=normalized["input_tokens"],
            output_tokens=normalized["output_tokens"],
            total_tokens=normalized["total_tokens"],
            usage_quality=normalized["usage_quality"],
            latency_ms=(perf_counter() - started) * 1000,
            finish_reason=normalized["finish_reason"],
            task_id=request.task_id,
            correlation_id=request.correlation_id,
            request_id=response.headers.get("x-request-id") or normalized["response_id"],
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
        if self.health_strategy == HealthCheckStrategy.CONFIGURATION:
            return ProviderHealth(
                provider=self.name,
                healthy=True,
                status=HealthStatus.CONFIGURATION_ONLY,
                latency_ms=0,
                detail="configuration validated; remote check disabled",
            )
        started = perf_counter()
        try:
            available = None
            if self.health_strategy == HealthCheckStrategy.MODELS:
                available = self.default_model in await self._list_models()
            else:
                await self._get("")
            return ProviderHealth(
                provider=self.name,
                healthy=True,
                status=HealthStatus.HEALTHY,
                latency_ms=(perf_counter() - started) * 1000,
                model_available=available,
                detail="provider service reachable",
            )
        except ModelProviderError as error:
            return ProviderHealth(
                provider=self.name,
                healthy=False,
                status=HealthStatus.UNAVAILABLE,
                latency_ms=(perf_counter() - started) * 1000,
                error_category=error.category.value,
                detail=error.message,
            )

    async def model_available(self, model: str) -> bool | None:
        if not provider_network_health_allowed():
            return None
        if self.health_strategy != HealthCheckStrategy.MODELS:
            return None
        try:
            return model in await self._list_models()
        except ModelProviderError:
            return None

    async def _get(self, path: str) -> httpx.Response:
        client, owned = self._client_or_new()
        try:
            response = await client.get(
                path, headers=self._headers() if self._client is not None else None
            )
            response.raise_for_status()
            return response
        except httpx.HTTPError as exc:
            raise translate_http_error(exc, provider=self.name, model=self.default_model) from exc
        finally:
            if owned:
                await client.aclose()

    async def _list_models(self) -> set[str]:
        response = await self._get("models")
        try:
            body = response.json()
        except ValueError as exc:
            raise MalformedProviderResponseError(
                "provider models response is malformed JSON",
                provider=self.name,
                model=self.default_model,
            ) from exc
        if not isinstance(body, dict) or not isinstance(body.get("data"), list):
            raise MalformedProviderResponseError(
                "provider models response is malformed",
                provider=self.name,
                model=self.default_model,
            )
        if any(
            not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]
            for item in body["data"]
        ):
            raise MalformedProviderResponseError(
                "provider models response contains an invalid entry",
                provider=self.name,
                model=self.default_model,
            )
        return {item["id"] for item in body["data"]}


def _normalize_completion(body: object, *, provider: str, requested_model: str) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise _malformed(provider, requested_model, "response must be an object")
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise _malformed(provider, requested_model, "response contains no generated choice")
    message = choices[0].get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content:
        raise _malformed(provider, requested_model, "response is missing generated content")
    usage = body.get("usage")
    if usage is not None and not isinstance(usage, dict):
        raise _malformed(provider, requested_model, "provider usage is malformed")
    usage = usage or {}
    input_tokens = _optional_integer(usage.get("prompt_tokens"), provider, requested_model)
    output_tokens = _optional_integer(usage.get("completion_tokens"), provider, requested_model)
    total_tokens = _optional_integer(usage.get("total_tokens"), provider, requested_model)
    returned_model = _optional_string(body.get("model"), provider, requested_model)
    response_id = _optional_string(body.get("id"), provider, requested_model)
    finish_reason = _optional_string(choices[0].get("finish_reason"), provider, requested_model)
    return {
        "content": content,
        "model": returned_model or requested_model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "usage_quality": (
            UsageQuality.EXACT
            if input_tokens is not None and output_tokens is not None
            else UsageQuality.UNKNOWN
        ),
        "finish_reason": finish_reason,
        "response_id": response_id,
    }


def _optional_integer(value: object, provider: str, model: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    raise _malformed(provider, model, "provider token usage is malformed")


def _optional_string(value: object, provider: str, model: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and value:
        return value
    raise _malformed(provider, model, "provider response contains an invalid optional field")


def _malformed(provider: str, model: str, message: str) -> MalformedProviderResponseError:
    return MalformedProviderResponseError(message, provider=provider, model=model)
