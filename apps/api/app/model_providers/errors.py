from __future__ import annotations

from enum import StrEnum
from typing import Any

from app.model_providers.security import redact_secrets


class ErrorCategory(StrEnum):
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    AUTHENTICATION_FAILURE = "authentication_failure"
    TEMPORARY_RATE_LIMIT = "temporary_rate_limit"
    HARD_QUOTA_EXHAUSTED = "hard_quota_exhausted"
    TIMEOUT = "timeout"
    INVALID_MODEL_REQUEST = "invalid_model_request"
    MODEL_UNAVAILABLE = "model_unavailable"
    BUDGET_EXCEEDED = "budget_exceeded"
    MALFORMED_PROVIDER_RESPONSE = "malformed_provider_response"
    UNKNOWN_PROVIDER = "unknown_provider"
    DUPLICATE_PROVIDER = "duplicate_provider"
    PROVIDER_CONFIGURATION_FAILURE = "provider_configuration_failure"
    PROVIDER_EXECUTION_DISABLED = "provider_execution_disabled"
    TRANSIENT_PROVIDER_FAILURE = "transient_provider_failure"


class ModelProviderError(Exception):
    category = ErrorCategory.PROVIDER_UNAVAILABLE
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        model: str | None = None,
        retry_after_seconds: float | None = None,
        task_id: str | None = None,
        correlation_id: str | None = None,
        status_code: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.message = str(redact_secrets(message))
        self.provider = provider
        self.model = model
        self.retry_after_seconds = retry_after_seconds
        self.task_id = task_id
        self.correlation_id = correlation_id
        self.status_code = status_code
        sanitized = redact_secrets(metadata or {})
        self.metadata = sanitized if isinstance(sanitized, dict) else {}
        super().__init__(self.message)

    def safe_details(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "category": self.category.value,
                "message": self.message,
                "provider": self.provider,
                "model": self.model,
                "retryable": self.retryable,
                "retry_after_seconds": self.retry_after_seconds,
                "task_id": self.task_id,
                "correlation_id": self.correlation_id,
                "status_code": self.status_code,
                "metadata": self.metadata or None,
            }.items()
            if value is not None
        }


class ProviderUnavailableError(ModelProviderError):
    category = ErrorCategory.PROVIDER_UNAVAILABLE
    retryable = True


class AuthenticationError(ModelProviderError):
    category = ErrorCategory.AUTHENTICATION_FAILURE


class RateLimitError(ModelProviderError):
    category = ErrorCategory.TEMPORARY_RATE_LIMIT
    retryable = True


class QuotaExhaustedError(ModelProviderError):
    category = ErrorCategory.HARD_QUOTA_EXHAUSTED


class RequestTimeoutError(ModelProviderError):
    category = ErrorCategory.TIMEOUT
    retryable = True


class InvalidModelRequestError(ModelProviderError):
    category = ErrorCategory.INVALID_MODEL_REQUEST


class ModelUnavailableError(ModelProviderError):
    category = ErrorCategory.MODEL_UNAVAILABLE


class BudgetExceededError(ModelProviderError):
    category = ErrorCategory.BUDGET_EXCEEDED


class MalformedProviderResponseError(ModelProviderError):
    category = ErrorCategory.MALFORMED_PROVIDER_RESPONSE


class UnknownProviderError(ModelProviderError):
    category = ErrorCategory.UNKNOWN_PROVIDER


class DuplicateProviderError(ModelProviderError):
    category = ErrorCategory.DUPLICATE_PROVIDER


class ProviderConfigurationError(ModelProviderError):
    category = ErrorCategory.PROVIDER_CONFIGURATION_FAILURE


class ProviderExecutionDisabledError(ModelProviderError):
    category = ErrorCategory.PROVIDER_EXECUTION_DISABLED


class TransientProviderError(ModelProviderError):
    category = ErrorCategory.TRANSIENT_PROVIDER_FAILURE
    retryable = True
