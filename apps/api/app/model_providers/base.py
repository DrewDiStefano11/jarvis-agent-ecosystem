from __future__ import annotations

from app.model_providers.contracts import (
    ModelCapability,
    ProviderSummary,
    ProviderType,
)


class ProviderBase:
    name: str
    provider_type: ProviderType
    is_local: bool
    capabilities: frozenset[ModelCapability]
    default_model: str

    def safe_summary(self) -> ProviderSummary:
        return ProviderSummary(
            name=self.name,
            provider_type=self.provider_type,
            is_local=self.is_local,
            capabilities=sorted(self.capabilities, key=str),
            default_model=self.default_model,
        )

    async def model_available(self, model: str) -> bool | None:
        return None

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(name={self.name!r}, default_model={self.default_model!r}, "
            f"is_local={self.is_local!r})"
        )
