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
    "perday",
    "billing limit",
    "billing",
    "hard limit",
    "hard usage cap",
)
MINUTE_QUOTA_TERMS = ("per-minute", "per minute", "perminute")
GENERIC_EXHAUSTION_TERMS = ("quota exhausted", "quota_exhausted")


def retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def safe_error_signals(response: httpx.Response) -> tuple[str, ...]:
    try:
        payload = response.json()
    except ValueError:
        return ()
    error = payload.get("error", payload) if isinstance(payload, dict) else {}
    if not isinstance(error, dict):
        return ()
    signals: list[str] = []

    def add(value: object) -> None:
        if isinstance(value, (str, int)):
            signals.append(str(value)[:300].lower())

    for key in ("code", "status", "type", "message"):
        add(error.get(key))
    details = error.get("details")
    if isinstance(details, list):
        for detail in details[:20]:
            if not isinstance(detail, dict):
                continue
            add(detail.get("@type"))
            violations = detail.get("violations")
            if not isinstance(violations, list):
                continue
            for violation in violations[:50]:
                if not isinstance(violation, dict):
                    continue
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
        signals = safe_error_signals(response)
        text = " ".join(signals)
        if any(term in text for term in HARD_QUOTA_TERMS):
            return QuotaExhaustedError("provider quota is exhausted", **common)
        if any(term in text for term in MINUTE_QUOTA_TERMS):
            return RateLimitError(
                "provider rate limit reached",
                retry_after_seconds=retry_after(response),
                **common,
            )
        if any(term in text for term in GENERIC_EXHAUSTION_TERMS):
            return QuotaExhaustedError("provider quota is exhausted", **common)
        return RateLimitError(
            "provider rate limit reached",
            retry_after_seconds=retry_after(response),
            **common,
        )
    if status >= 500:
        return TransientProviderError("provider returned a transient server error", **common)
    return InvalidModelRequestError("provider rejected the request", **common)
