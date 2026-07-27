from __future__ import annotations

import asyncio
from collections.abc import Iterable

from app.model_providers.contracts import (
    HealthStatus,
    ModelCapability,
    ModelProvider,
    ProviderHealth,
    ProviderSummary,
)
from app.model_providers.errors import (
    DuplicateProviderError,
    MalformedProviderResponseError,
    UnknownProviderError,
)


class ProviderRegistry:
    def __init__(self, providers: Iterable[ModelProvider] = ()) -> None:
        self._providers: dict[str, ModelProvider] = {}
        self._lock = asyncio.Lock()
        for provider in providers:
            self._register(provider)

    def _register(self, provider: ModelProvider) -> None:
        if provider.name in self._providers:
            raise DuplicateProviderError(
                f"provider {provider.name!r} is already registered", provider=provider.name
            )
        self._providers[provider.name] = provider

    async def register(self, provider: ModelProvider) -> None:
        async with self._lock:
            self._register(provider)

    async def unregister(self, name: str) -> ModelProvider:
        async with self._lock:
            return self._providers.pop(name, None) or self.get(name)

    def get(self, name: str) -> ModelProvider:
        try:
            return self._providers[name]
        except KeyError as exc:
            raise UnknownProviderError(f"unknown provider: {name}", provider=name) from exc

    def list(self) -> list[ModelProvider]:
        return list(self._providers.values())

    def summaries(self) -> list[ProviderSummary]:
        return [provider.safe_summary() for provider in self.list()]

    def by_capability(self, capability: ModelCapability) -> list[ModelProvider]:
        return [provider for provider in self.list() if capability in provider.capabilities]

    async def health(self) -> dict[str, ProviderHealth]:
        results = await asyncio.gather(
            *(self._safe_health_check(provider) for provider in self.list())
        )
        return {result.provider: result for result in results}

    @staticmethod
    async def _safe_health_check(provider: ModelProvider) -> ProviderHealth:
        try:
            result = await provider.health_check()
            if not isinstance(result, ProviderHealth) or result.provider != provider.name:
                raise TypeError("provider returned an invalid normalized health result")
            return result
        except Exception:
            return ProviderHealth(
                provider=provider.name,
                healthy=False,
                status=HealthStatus.UNAVAILABLE,
                latency_ms=0,
                error_category=MalformedProviderResponseError.__name__,
                detail="provider health check violated the normalized health contract",
            )

    async def healthy(self) -> list[ModelProvider]:
        health = await self.health()
        return [provider for provider in self.list() if health[provider.name].healthy]
