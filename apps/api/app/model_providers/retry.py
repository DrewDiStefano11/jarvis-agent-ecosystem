from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, Field

from app.model_providers.budget import BudgetTracker
from app.model_providers.contracts import ModelExecutionRequest
from app.model_providers.errors import ModelProviderError

T = TypeVar("T")
logger = logging.getLogger(__name__)


class RetryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    maximum_attempts: int = Field(default=2, ge=1, le=10)
    initial_backoff_seconds: float = Field(default=0.25, ge=0, le=60)
    maximum_backoff_seconds: float = Field(default=5, ge=0, le=300)

    def delay(self, attempt: int, retry_after_seconds: float | None = None) -> float:
        exponential = min(
            self.maximum_backoff_seconds,
            self.initial_backoff_seconds * (2 ** max(0, attempt - 1)),
        )
        if retry_after_seconds is None:
            return exponential
        return min(self.maximum_backoff_seconds, max(exponential, retry_after_seconds))


class RetryExecutor:
    def __init__(
        self,
        policy: RetryPolicy,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.policy = policy
        self.sleep = sleep

    async def execute(
        self,
        operation: Callable[[], Awaitable[T]],
        *,
        request: ModelExecutionRequest,
        budget: BudgetTracker,
        provider_name: str | None = None,
    ) -> T:
        for attempt in range(1, self.policy.maximum_attempts + 1):
            budget.before_attempt(request, provider=provider_name)
            try:
                return await operation()
            except ModelProviderError as exc:
                if not exc.retryable or attempt >= self.policy.maximum_attempts:
                    logger.info(
                        "model retry stopped provider=%s model=%s category=%s attempt=%s "
                        "task_id=%s correlation_id=%s",
                        exc.provider,
                        exc.model or request.model,
                        type(exc).__name__,
                        attempt,
                        request.task_id,
                        request.correlation_id,
                    )
                    raise
                delay = self.policy.delay(attempt, exc.retry_after_seconds)
                logger.info(
                    "model retry scheduled provider=%s model=%s category=%s attempt=%s "
                    "delay_seconds=%s task_id=%s correlation_id=%s",
                    exc.provider,
                    exc.model or request.model,
                    type(exc).__name__,
                    attempt,
                    delay,
                    request.task_id,
                    request.correlation_id,
                )
                await self.sleep(delay)
        raise AssertionError("retry loop must return or raise")
