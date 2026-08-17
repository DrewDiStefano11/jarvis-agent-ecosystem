from __future__ import annotations

from app.model_providers.errors import ProviderExecutionDisabledError

CURRENT_PROVIDER_POLICY = "phase_2c_local_only"


def require_live_provider_execution(
    *,
    provider: str,
    model: str,
    task_id: str | None = None,
    correlation_id: str | None = None,
    execution_mode: str = "disabled",
    is_local: bool = False,
) -> None:
    if execution_mode == "local_only" and is_local:
        return
    detail = (
        "model execution is disabled"
        if execution_mode == "disabled"
        else "local model execution requires a structurally loopback provider"
    )
    raise ProviderExecutionDisabledError(
        detail,
        provider=provider,
        model=model,
        task_id=task_id,
        correlation_id=correlation_id,
        metadata={"policy": CURRENT_PROVIDER_POLICY, "execution_mode": execution_mode},
    )


def provider_network_health_allowed(
    *, execution_mode: str = "disabled", is_local: bool = False
) -> bool:
    return execution_mode == "local_only" and is_local


def require_provider_network_health(
    *,
    provider: str,
    model: str,
    allowed: bool | None = None,
    execution_mode: str = "disabled",
    is_local: bool = False,
) -> None:
    if (
        provider_network_health_allowed(execution_mode=execution_mode, is_local=is_local)
        if allowed is None
        else allowed
    ):
        return
    raise ProviderExecutionDisabledError(
        "provider network health is disabled by the current project phase",
        provider=provider,
        model=model,
        metadata={"policy": CURRENT_PROVIDER_POLICY, "execution_mode": execution_mode},
    )
