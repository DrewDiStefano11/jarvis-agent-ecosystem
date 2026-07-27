from __future__ import annotations

from time import perf_counter
from typing import Any

import httpx

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
from app.model_providers.errors import MalformedProviderResponseError, ModelProviderError
from app.model_providers.http import translate_http_error


class OllamaProvider(ProviderBase):
    provider_type = ProviderType.OLLAMA
    is_local = True

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
        self.name = name
        self.base_url = base_url.rstrip("/")
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
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": message.role.value, "content": message.content}
                for message in request.messages
            ],
            "stream": False,
        }
        options = {}
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
        content = body.get("message", {}).get("content") if isinstance(body, dict) else None
        if not isinstance(content, str) or not content:
            raise MalformedProviderResponseError(
                "Ollama response is missing generated content", provider=self.name, model=model
            )
        input_tokens = _integer(body.get("prompt_eval_count"))
        output_tokens = _integer(body.get("eval_count"))
        return ModelExecutionResponse(
            content=content,
            provider=self.name,
            model=str(body.get("model") or model),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            usage_quality=(
                UsageQuality.EXACT
                if input_tokens is not None or output_tokens is not None
                else UsageQuality.UNKNOWN
            ),
            latency_ms=(perf_counter() - started) * 1000,
            finish_reason=str(body.get("done_reason")) if body.get("done_reason") else None,
            task_id=request.task_id,
            correlation_id=request.correlation_id,
        )

    async def health_check(self) -> ProviderHealth:
        started = perf_counter()
        client, owned = self._client_or_new()
        try:
            response = await client.get("/api/tags")
            response.raise_for_status()
            body = response.json()
            models = _model_names(body, provider=self.name, model=self.default_model)
            available = self.default_model in models
            return ProviderHealth(
                provider=self.name,
                healthy=available,
                status=HealthStatus.HEALTHY if available else HealthStatus.DEGRADED,
                latency_ms=(perf_counter() - started) * 1000,
                model_available=available,
                detail="configured model available"
                if available
                else "configured model unavailable",
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

    async def model_available(self, model: str) -> bool | None:
        client, owned = self._client_or_new()
        try:
            response = await client.get("/api/tags")
            response.raise_for_status()
            names = _model_names(response.json(), provider=self.name, model=model)
            return model in names
        except (httpx.HTTPError, ValueError, TypeError, MalformedProviderResponseError):
            return None
        finally:
            if owned:
                await client.aclose()


def _integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _model_names(body: object, *, provider: str, model: str) -> set[str]:
    if not isinstance(body, dict):
        raise MalformedProviderResponseError(
            "Ollama health response must be an object",
            provider=provider,
            model=model,
        )
    models = body.get("models")
    if not isinstance(models, list):
        raise MalformedProviderResponseError(
            "Ollama health response models must be a list",
            provider=provider,
            model=model,
        )
    if any(not isinstance(item, dict) or not isinstance(item.get("name"), str) for item in models):
        raise MalformedProviderResponseError(
            "Ollama health response contains an invalid model entry",
            provider=provider,
            model=model,
        )
    return {item["name"] for item in models}
