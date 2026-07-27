from __future__ import annotations

from typing import Any

from app.model_providers.security import redact_secrets


class ModelProviderError(Exception):
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
        self.metadata = redact_secrets(metadata or {})
        super().__init__(self.message)

    def safe_details(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "category": type(self).__name__,
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
    retryable = True


class AuthenticationError(ModelProviderError):
    pass


class RateLimitError(ModelProviderError):
    retryable = True


class QuotaExhaustedError(ModelProviderError):
    pass


class RequestTimeoutError(ModelProviderError):
    retryable = True


class InvalidModelRequestError(ModelProviderError):
    pass


class ModelUnavailableError(ModelProviderError):
    pass


class BudgetExceededError(ModelProviderError):
    pass


class MalformedProviderResponseError(ModelProviderError):
    pass


class UnknownProviderError(ModelProviderError):
    pass


class DuplicateProviderError(ModelProviderError):
    pass


class ProviderConfigurationError(ModelProviderError):
    pass


class TransientProviderError(ModelProviderError):
    retryable = True
