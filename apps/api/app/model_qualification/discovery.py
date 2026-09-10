"""Installed-local model discovery through the existing provider architecture.

Discovery is **observational only**. It uses the repository's provider
registry, health checks, and the provider ``list_models`` / ``model_available``
APIs that already exist (PR #45/#64). It never:

- downloads or pulls a model,
- starts or restarts a model service,
- contacts a remote/cloud provider,
- falls back to a different model when the requested one is missing,
- exposes a local service to anything but loopback.

An unavailable provider yields ``status="provider_unhealthy"`` and a missing
model yields ``status="model_unavailable"``; neither is ever reported as a
quality result.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.autonomy.events import scrub_text
from app.core.config import Settings
from app.model_providers.factory import build_provider_registry

#: Discovery statuses (machine-readable, stable).
DISCOVERED = "discovered"
NOT_REQUESTED = "not_requested"
REJECTED_REMOTE = "rejected_remote"
PROVIDER_UNHEALTHY = "provider_unhealthy"
MODELS_UNKNOWN = "models_unknown"
EXECUTION_DISABLED = "execution_disabled"
NO_LOCAL_PROVIDER = "no_local_provider"
UNKNOWN_PROVIDER = "unknown_or_remote_provider"
MODEL_UNAVAILABLE = "model_unavailable"
MODEL_INSTALLED = "installed"
DISCOVERY_ERROR = "discovery_error"


@dataclass(frozen=True)
class ProviderDiscovery:
    provider: str
    is_local: bool
    available: bool
    status: str
    models: tuple[str, ...]
    detail: str


@dataclass(frozen=True)
class DiscoveredModel:
    provider: str
    model: str

    @property
    def identity(self) -> str:
        return f"{self.provider}::{self.model}"


@dataclass(frozen=True)
class DiscoveryResult:
    execution_mode: str
    status: str
    detail: str
    providers: tuple[ProviderDiscovery, ...]
    models: tuple[DiscoveredModel, ...]

    @property
    def available(self) -> bool:
        return bool(self.models)

    def document(self) -> dict[str, Any]:
        return {
            "execution_mode": self.execution_mode,
            "status": self.status,
            "detail": self.detail,
            "providers": [
                {
                    "provider": item.provider,
                    "is_local": item.is_local,
                    "available": item.available,
                    "status": item.status,
                    "models": list(item.models),
                    "detail": item.detail,
                }
                for item in self.providers
            ],
            "models": [
                {"provider": item.provider, "model": item.model, "identity": item.identity}
                for item in self.models
            ],
        }


@dataclass(frozen=True)
class ModelVerification:
    """Did preflight find *exactly* this model installed on a local provider?"""

    provider: str | None
    model: str
    available: bool
    status: str
    detail: str

    def document(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "available": self.available,
            "status": self.status,
            "detail": self.detail,
        }


async def discover_local_models(
    settings: Settings,
    *,
    provider_name: str | None = None,
    maximum_providers: int = 4,
    maximum_models: int = 64,
) -> DiscoveryResult:
    """Enumerate models already installed on configured *local* providers."""
    if settings.model_execution_mode != "local_only":
        return DiscoveryResult(
            execution_mode=settings.model_execution_mode,
            status=EXECUTION_DISABLED,
            detail=(
                "local discovery requires JARVIS_MODEL_EXECUTION_MODE=local_only "
                f"(current: {settings.model_execution_mode})"
            ),
            providers=(),
            models=(),
        )

    registry = build_provider_registry(settings)
    configured = registry.list()
    if not configured:
        return DiscoveryResult(
            execution_mode=settings.model_execution_mode,
            status=NO_LOCAL_PROVIDER,
            detail="no model provider is configured",
            providers=(),
            models=(),
        )

    discoveries: list[ProviderDiscovery] = []
    models: list[DiscoveredModel] = []
    candidates = [provider for provider in configured if provider.is_local]
    if provider_name is not None and not any(
        provider.name == provider_name for provider in candidates
    ):
        named = next((item for item in configured if item.name == provider_name), None)
        detail = (
            f"provider {provider_name!r} is not an available local provider "
            f"(local: {sorted(provider.name for provider in candidates)})"
        )
        if named is not None and not named.is_local:
            detail = f"provider {provider_name!r} is remote; remote providers are rejected"
        return DiscoveryResult(
            execution_mode=settings.model_execution_mode,
            status=UNKNOWN_PROVIDER,
            detail=detail,
            providers=(
                ProviderDiscovery(
                    provider=provider_name,
                    is_local=bool(named.is_local) if named else False,
                    available=False,
                    status=REJECTED_REMOTE
                    if named is not None and not named.is_local
                    else UNKNOWN_PROVIDER,
                    models=(),
                    detail=detail,
                ),
            ),
            models=(),
        )

    for provider in configured[: max(1, maximum_providers)]:
        if not provider.is_local:
            discoveries.append(
                ProviderDiscovery(
                    provider=provider.name,
                    is_local=False,
                    available=False,
                    status=REJECTED_REMOTE,
                    models=(),
                    detail="remote providers are never used for local qualification",
                )
            )
            continue
        if provider_name is not None and provider.name != provider_name:
            discoveries.append(
                ProviderDiscovery(
                    provider=provider.name,
                    is_local=True,
                    available=False,
                    status=NOT_REQUESTED,
                    models=(),
                    detail=f"provider {provider.name!r} was not requested",
                )
            )
            continue
        try:
            health = await registry.health([provider])
            status = health.get(provider.name)
            if status is None or not status.healthy:
                detail = (
                    status.detail
                    if status is not None and status.detail
                    else "provider health check reported unhealthy"
                )
                discoveries.append(
                    ProviderDiscovery(
                        provider=provider.name,
                        is_local=True,
                        available=False,
                        status=PROVIDER_UNHEALTHY,
                        models=(),
                        detail=scrub_text(str(detail))[:300],
                    )
                )
                continue
            names = await _safe_list_models(provider)
            if names is None:
                available = await provider.model_available(provider.default_model)
                known = (provider.default_model,) if available is True else ()
                discoveries.append(
                    ProviderDiscovery(
                        provider=provider.name,
                        is_local=True,
                        available=bool(known),
                        status=MODELS_UNKNOWN,
                        models=known,
                        detail=(
                            "provider does not enumerate models; only the configured "
                            "default model was verified"
                        ),
                    )
                )
            else:
                discoveries.append(
                    ProviderDiscovery(
                        provider=provider.name,
                        is_local=True,
                        available=bool(names),
                        status=DISCOVERED,
                        models=tuple(sorted(names)),
                        detail=f"{len(names)} installed model(s) advertised",
                    )
                )
            for name in sorted(discoveries[-1].models):
                if len(models) >= max(1, maximum_models):
                    break
                models.append(DiscoveredModel(provider=provider.name, model=name))
        except Exception as exc:  # noqa: BLE001 - discovery must never crash the CLI
            discoveries.append(
                ProviderDiscovery(
                    provider=provider.name,
                    is_local=True,
                    available=False,
                    status=DISCOVERY_ERROR,
                    models=(),
                    detail=scrub_text(f"{type(exc).__name__}: {exc}")[:300],
                )
            )

    models = models[: max(1, maximum_models)]
    if models:
        status = DISCOVERED
        detail = f"{len(models)} installed local model(s) discovered"
    elif any(item.status == PROVIDER_UNHEALTHY for item in discoveries):
        status = PROVIDER_UNHEALTHY
        detail = "no local provider is healthy; nothing was downloaded or started"
    elif any(item.status == DISCOVERY_ERROR for item in discoveries):
        status = DISCOVERY_ERROR
        detail = "discovery failed for every local provider"
    else:
        status = NO_LOCAL_PROVIDER
        detail = "no installed local model is advertised by a healthy local provider"

    return DiscoveryResult(
        execution_mode=settings.model_execution_mode,
        status=status,
        detail=detail,
        providers=tuple(discoveries),
        models=tuple(models),
    )


async def verify_installed_model(
    settings: Settings,
    *,
    model: str,
    provider_name: str | None = None,
    maximum_providers: int = 4,
    maximum_models: int = 64,
) -> ModelVerification:
    """Check that *exactly* ``model`` is installed locally (never a substitute)."""
    discovery = await discover_local_models(
        settings,
        provider_name=provider_name,
        maximum_providers=maximum_providers,
        maximum_models=maximum_models,
    )
    if discovery.status in {
        EXECUTION_DISABLED,
        NO_LOCAL_PROVIDER,
        PROVIDER_UNHEALTHY,
        DISCOVERY_ERROR,
    }:
        return ModelVerification(
            provider=None,
            model=model,
            available=False,
            status=discovery.status,
            detail=discovery.detail,
        )
    for candidate in discovery.models:
        if candidate.model == model:
            return ModelVerification(
                provider=candidate.provider,
                model=model,
                available=True,
                status=MODEL_INSTALLED,
                detail=f"model {model!r} is installed on local provider {candidate.provider!r}",
            )
    advertised = sorted({item.model for item in discovery.models})
    hint = ""
    if f"{model}:latest" in advertised:
        hint = f" (did you mean the exact installed name {model + ':latest'!r}?)"
    return ModelVerification(
        provider=None,
        model=model,
        available=False,
        status=MODEL_UNAVAILABLE,
        detail=(
            f"model {model!r} is not installed on a local provider{hint}; "
            f"installed: {advertised[:20]}"
        ),
    )


async def _safe_list_models(provider: Any) -> tuple[str, ...] | None:
    lister = getattr(provider, "list_models", None)
    if not callable(lister):
        return None
    names = await lister()
    if not names:
        return None
    return tuple(str(name) for name in names)


__all__ = [
    "DISCOVERED",
    "DISCOVERY_ERROR",
    "EXECUTION_DISABLED",
    "MODEL_INSTALLED",
    "MODEL_UNAVAILABLE",
    "MODELS_UNKNOWN",
    "NO_LOCAL_PROVIDER",
    "NOT_REQUESTED",
    "PROVIDER_UNHEALTHY",
    "REJECTED_REMOTE",
    "UNKNOWN_PROVIDER",
    "DiscoveredModel",
    "DiscoveryResult",
    "ModelVerification",
    "discover_local_models",
    "verify_installed_model",
]
