from __future__ import annotations

from typing import Any

import httpx

from app.model_providers.errors import (
    AuthenticationError,
    InvalidModelRequestError,
    ModelProviderError,
    ModelUnavailableError,
    ProviderUnavailableError,
    QuotaExhaustedError,
    RateLimitError,
    RequestTimeoutError,
    TransientProviderError,
)

HARD_QUOTA_TERMS = (
    "insufficient_quota",
    "daily quota",
    "per-day",
    "per day",
    "billing",
    "hard limit",
    "hard usage cap",
)
TEMPORARY_QUOTA_TERMS = ("per-minute", "per minute", "perminute", "rate_limit")


def retry_after(response: httpx.Response, *, maximum: float = 300) -> float | None:
    value = response.headers.get("retry-after")
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return min(maximum, max(0.0, parsed))


def safe_error_signals(response: httpx.Response) -> tuple[str, ...]:
    try:
        payload = response.json()
    except (ValueError, TypeError):
        return ()
    if not isinstance(payload, dict):
        return ()
    error = payload.get("error", payload)
    if not isinstance(error, dict):
        return ()
    signals: list[str] = []

    def add(value: object) -> None:
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            signals.append(str(value)[:200].lower())

    for key in ("code", "status", "type", "message"):
        add(error.get(key))
    details = error.get("details")
    if isinstance(details, list):
        for detail in details[:10]:
            if not isinstance(detail, dict):
                continue
            add(detail.get("@type"))
            violations = detail.get("violations")
            if isinstance(violations, list):
                for violation in violations[:20]:
                    if isinstance(violation, dict):
                        add(violation.get("quotaId"))
                        add(violation.get("quotaMetric"))
    return tuple(signals)


def translate_http_error(
    exc: Exception,
    *,
    provider: str,
    model: str,
    task_id: str | None = None,
    correlation_id: str | None = None,
    maximum_retry_after_seconds: float = 300,
) -> ModelProviderError:
    common: dict[str, Any] = {
        "provider": provider,
        "model": model,
        "task_id": task_id,
        "correlation_id": correlation_id,
    }
    if isinstance(exc, httpx.TimeoutException):
        return RequestTimeoutError("provider request timed out", **common)
    if isinstance(exc, httpx.NetworkError):
        return ProviderUnavailableError("provider network connection failed", **common)
    if not isinstance(exc, httpx.HTTPStatusError):
        return ProviderUnavailableError("provider request failed", **common)
    response = exc.response
    status = response.status_code
    common["status_code"] = status
    if status in (401, 403):
        return AuthenticationError("provider authentication or authorization failed", **common)
    if status in (400, 409, 422):
        return InvalidModelRequestError("provider rejected the model request", **common)
    if status == 404:
        return ModelUnavailableError("provider endpoint or model is unavailable", **common)
    if status in (408, 504):
        return RequestTimeoutError("provider request timed out", **common)
    if status == 429:
        text = " ".join(safe_error_signals(response))
        if any(term in text for term in HARD_QUOTA_TERMS):
            return QuotaExhaustedError("provider quota is exhausted", **common)
        return RateLimitError(
            "provider rate limit reached",
            retry_after_seconds=retry_after(response, maximum=maximum_retry_after_seconds),
            **common,
        )
    if status >= 500:
        return TransientProviderError("provider returned a transient server error", **common)
    return InvalidModelRequestError("provider rejected the request", **common)
