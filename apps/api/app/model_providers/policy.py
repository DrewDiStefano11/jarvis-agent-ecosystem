from __future__ import annotations

from app.model_providers.errors import ProviderExecutionDisabledError

CURRENT_PROVIDER_POLICY = "phase_1_no_live_models"


def require_live_provider_execution(
    *,
    provider: str,
    model: str,
    task_id: str | None = None,
    correlation_id: str | None = None,
) -> None:
    raise ProviderExecutionDisabledError(
        "live model-provider execution is disabled by the current project phase",
        provider=provider,
        model=model,
        task_id=task_id,
        correlation_id=correlation_id,
        metadata={"policy": CURRENT_PROVIDER_POLICY},
    )


def provider_network_health_allowed() -> bool:
    return False
