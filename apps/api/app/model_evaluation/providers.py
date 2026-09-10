"""Model providers for evaluation: deterministic fixture and installed-local.

Two modes, both reusing the existing provider contracts
(:class:`app.model_providers.contracts.ModelExecutionRequest` /
:class:`~app.model_providers.contracts.ModelExecutionResponse`):

- :class:`ScriptedFixtureProvider` — deterministic scripted responses keyed by
  case id. Requires no installed model, reproducible in CI, and never
  described as real inference (``inference_mode == "fixture"``).
- :class:`LocalRouterProvider` — real inference through the existing
  :class:`app.model_providers.router.ModelRouter` (Ollama/loopback only).
  Preflight fails clearly when the requested model is unavailable; evaluation
  never downloads models, never starts model services, and never allows
  remote providers (``inference_mode == "installed_local"``).

No cloud-model dependency exists in this module.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from app.core.config import Settings
from app.model_providers.budget import TaskBudget
from app.model_providers.contracts import (
    ModelCapability,
    ModelExecutionRequest,
    ModelExecutionResponse,
    UsageQuality,
)
from app.model_providers.errors import ModelProviderError
from app.model_providers.factory import build_model_router, build_provider_registry
from app.model_providers.router import ModelRouter, RoutingRequirements

FIXTURE_PROVIDER_NAME = "fixture"
FIXTURE_MODEL_NAME = "deterministic-scripts/v1"


class EvaluationUnavailableError(RuntimeError):
    """Raised when a requested evaluation provider/model cannot be used."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(f"{code}: {detail}")


class EvalProvider(Protocol):
    """Minimal provider surface required by the evaluation runner."""

    name: str
    model_name: str
    inference_mode: str  # "fixture" | "installed_local"
    is_local: bool

    async def generate(self, request: ModelExecutionRequest) -> ModelExecutionResponse: ...


@dataclass
class FixtureCall:
    case_id: str
    call_index: int
    latency_ms: float


class ScriptedFixtureProvider:
    """Deterministic scripted responses; never real inference.

    Scripts map ``case_id`` to an ordered response list. Each entry is either
    response text or an exception instance to simulate a provider failure.
    Calls beyond the scripted list raise :class:`EvaluationUnavailableError`
    with code ``fixture_script_exhausted`` so unbounded calls fail loudly.
    """

    name = FIXTURE_PROVIDER_NAME
    model_name = FIXTURE_MODEL_NAME
    inference_mode = "fixture"
    is_local = True

    def __init__(
        self,
        scripts: dict[str, list[str | Exception]],
        *,
        model_name: str | None = None,
    ) -> None:
        """Scripted responses keyed by case id.

        ``model_name`` lets a caller label a scripted fixture model (used by
        qualification personas so several fixture models can be compared); it
        defaults to :data:`FIXTURE_MODEL_NAME` and never implies real inference.
        """
        self._scripts = {key: list(value) for key, value in scripts.items()}
        self.calls: list[FixtureCall] = []
        self._counters: dict[str, int] = {}
        if model_name:
            self.model_name = model_name

    async def generate(self, request: ModelExecutionRequest) -> ModelExecutionResponse:
        case_id = (request.task_id or "unknown").split(":")[0]
        started = time.perf_counter()
        index = self._counters.get(case_id, 0)
        script = self._scripts.get(case_id, [])
        if index >= len(script):
            raise EvaluationUnavailableError(
                "fixture_script_exhausted",
                f"fixture has no scripted response {index + 1} for case {case_id!r}",
            )
        entry = script[index]
        self._counters[case_id] = index + 1
        latency_ms = max(0.0, (time.perf_counter() - started) * 1000)
        self.calls.append(FixtureCall(case_id=case_id, call_index=index + 1, latency_ms=latency_ms))
        if isinstance(entry, Exception):
            raise entry
        return ModelExecutionResponse(
            content=entry,
            provider=self.name,
            model=self.model_name,
            usage_quality=UsageQuality.UNKNOWN,
            latency_ms=latency_ms,
            task_id=request.task_id,
            correlation_id=request.correlation_id,
        )


@dataclass
class LocalEvaluationHandle:
    """Preflight-verified handle for installed-local-model evaluation."""

    router: ModelRouter
    requirements: RoutingRequirements
    budget: TaskBudget
    provider_name: str
    model_name: str
    inference_mode: str = "installed_local"
    is_local: bool = True


class LocalRouterProvider:
    """Real local inference through the existing model router."""

    inference_mode = "installed_local"
    is_local = True

    def __init__(self, handle: LocalEvaluationHandle) -> None:
        self._handle = handle
        self.name = handle.provider_name
        self.model_name = handle.model_name
        self.calls = 0

    async def generate(self, request: ModelExecutionRequest) -> ModelExecutionResponse:
        self.calls += 1
        routed = request.model_copy(update={"model": self.model_name})
        try:
            return await self._handle.router.execute(
                request=routed,
                requirements=self._handle.requirements,
                budget=self._handle.budget,
            )
        except ModelProviderError as exc:
            # Normalize to a category-only error: raw provider exception
            # internals never reach evaluation evidence.
            raise EvaluationUnavailableError(
                "provider_error", f"provider {self.name} failed: {exc.category.value}"
            ) from exc


async def build_local_provider(
    settings: Settings,
    *,
    provider_name: str | None = None,
    model: str | None = None,
    maximum_requests: int = 200,
) -> LocalRouterProvider:
    """Preflight and build an installed-local evaluation provider.

    Rules (fail-clearly, no side effects):

    - ``JARVIS_MODEL_EXECUTION_MODE`` must be ``local_only``.
    - At least one local provider must be configured and healthy.
    - Only ``is_local`` providers are eligible; remote providers are rejected
      even when explicitly named.
    - The requested (or provider-default) model must report available via the
      provider's own ``model_available`` check. Nothing is downloaded or
      started; an unavailable model is a clear error, not a silent fallback.
    """
    if settings.model_execution_mode != "local_only":
        raise EvaluationUnavailableError(
            "execution_disabled",
            "local evaluation requires JARVIS_MODEL_EXECUTION_MODE=local_only",
        )
    registry = build_provider_registry(settings)
    local_providers = [provider for provider in registry.list() if provider.is_local]
    if not local_providers:
        raise EvaluationUnavailableError(
            "no_local_provider",
            "no local model provider is configured "
            "(enable JARVIS_MODEL_OLLAMA_ENABLED with a loopback base URL)",
        )
    if provider_name is not None:
        names = {provider.name for provider in local_providers}
        if provider_name not in names:
            raise EvaluationUnavailableError(
                "unknown_or_remote_provider",
                f"provider {provider_name!r} is not an available local provider "
                f"(local: {sorted(names)})",
            )
        provider = registry.get(provider_name)
    else:
        provider = local_providers[0]
    health = await registry.health([provider])
    status = health[provider.name]
    if not status.healthy:
        raise EvaluationUnavailableError(
            "provider_unhealthy",
            f"local provider {provider.name!r} is not healthy: "
            f"{status.status.value} ({status.detail or 'no detail'})",
        )
    effective_model = model or provider.default_model
    available = await provider.model_available(effective_model)
    if available is not True:
        raise EvaluationUnavailableError(
            "model_unavailable",
            f"model {effective_model!r} is not available from local provider "
            f"{provider.name!r}; install/pull it out-of-band, then re-run",
        )
    requirements = RoutingRequirements(
        requested_provider=provider.name,
        required_capability=ModelCapability.CHAT,
        preferred_model=effective_model,
        prefer_local=True,
        allow_remote=False,
        allow_fallback=False,
    )
    budget = TaskBudget(maximum_requests=maximum_requests)
    router = build_model_router(settings)
    return LocalRouterProvider(
        LocalEvaluationHandle(
            router=router,
            requirements=requirements,
            budget=budget,
            provider_name=provider.name,
            model_name=effective_model,
        )
    )


def describe_identity(provider: EvalProvider) -> dict[str, str]:
    return {
        "mode": provider.inference_mode,
        "provider": provider.name,
        "model": provider.model_name,
    }
