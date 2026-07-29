from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.model_providers.contracts import (
    MessageRole,
    ModelExecutionRequest,
    ModelExecutionResponse,
    ModelMessage,
)
from app.model_providers.errors import AuthenticationError
from app.model_providers.security import REDACTED, redact_secrets


def test_prompt_is_normalized_to_one_user_message() -> None:
    request = ModelExecutionRequest(prompt="hello")
    assert request.prompt is None
    assert request.messages == [ModelMessage(role=MessageRole.USER, content="hello")]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"prompt": "hello", "messages": [{"role": "user", "content": "hello"}]},
        {"prompt": "hello", "streaming": True},
        {"prompt": "hello", "temperature": -0.1},
        {"prompt": "hello", "temperature": 2.1},
        {"prompt": "hello", "timeout_seconds": 0},
        {"prompt": "hello", "max_output_tokens": 0},
    ],
)
def test_invalid_request_contracts_are_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ModelExecutionRequest(**payload)


@pytest.mark.parametrize(
    "metadata",
    [
        {"authorization": "Bearer abc"},
        {"nested": {"api-key": "abc"}},
        {"items": [{"credential_id": "abc"}]},
        {"message": "Bearer abc.def"},
        {"value": object()},
        {"value": float("nan")},
        {"value": float("inf")},
        {"value": float("-inf")},
        {1: "non-string key"},
        {f"k{index}": index for index in range(33)},
        {"large": "x" * 20_001},
    ],
)
def test_metadata_is_bounded_and_secret_keys_are_rejected(
    metadata: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ModelExecutionRequest(prompt="hello", metadata=metadata)


def test_metadata_nesting_is_bounded() -> None:
    nested: object = "too deep"
    for _ in range(18):
        nested = [nested]
    with pytest.raises(ValidationError):
        ModelExecutionRequest(prompt="hello", metadata={"deep": nested})


def test_response_derives_total_tokens_and_accepts_explicit_zero_usage() -> None:
    response = ModelExecutionResponse(
        content="done",
        provider="mock",
        model="model",
        input_tokens=0,
        output_tokens=0,
        latency_ms=1,
    )
    assert response.total_tokens == 0


def test_response_derives_total_from_components_when_provider_total_is_inconsistent() -> None:
    response = ModelExecutionResponse(
        content="done",
        provider="mock",
        model="model",
        input_tokens=4,
        output_tokens=3,
        total_tokens=1,
        latency_ms=1,
    )
    assert response.total_tokens == 7


def test_recursive_redaction_handles_keys_bearers_and_assignments() -> None:
    value: dict[str, object] = {
        "api_key": "raw",
        "message": "Authorization: Bearer abc.def token=xyz",
    }
    value["cycle"] = value
    redacted = redact_secrets(value)
    assert "raw" not in repr(redacted)
    assert "abc.def" not in repr(redacted)
    assert "xyz" not in repr(redacted)
    assert REDACTED in repr(redacted)


def test_normalized_errors_never_expose_secret_text() -> None:
    error = AuthenticationError(
        "failed Authorization: Bearer abc api_key=secret-value",
        metadata={"password": "hidden"},
    )
    assert "abc" not in str(error)
    assert "secret-value" not in str(error)
    assert "hidden" not in repr(error.safe_details())


def test_settings_and_provider_keys_are_repr_safe() -> None:
    settings = Settings(
        JARVIS_MODEL_OPENAI_COMPATIBLE_ENABLED=True,
        JARVIS_MODEL_OPENAI_COMPATIBLE_API_KEY="very-secret",
    )
    assert "very-secret" not in repr(settings)
    assert isinstance(settings.model_openai_compatible_api_key, SecretStr)


def test_settings_defaults_disable_both_providers() -> None:
    settings = Settings(_env_file=None)
    assert settings.model_ollama_enabled is False
    assert settings.model_openai_compatible_enabled is False
    assert not hasattr(settings, "model_live_execution_enabled")


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "JARVIS_MODEL_OPENAI_COMPATIBLE_ENABLED": True,
            "JARVIS_MODEL_OPENAI_COMPATIBLE_API_KEY": "",
        },
        {"JARVIS_MODEL_PROVIDER_PRIORITY": "a,a"},
        {"JARVIS_MODEL_OLLAMA_MODEL": ""},
        {"JARVIS_MODEL_OLLAMA_CAPABILITIES": "vision"},
        {"JARVIS_MODEL_OLLAMA_CAPABILITIES": "CHAT"},
        {"JARVIS_MODEL_PRICING_JSON": "[]"},
        {"JARVIS_MODEL_PRICING_JSON": '{"m":{"input_per_million_usd":1}}'},
        {
            "JARVIS_MODEL_PRICING_JSON": (
                '{"p":{"m":{"input_per_million_usd":-1,"output_per_million_usd":2}}}'
            )
        },
    ],
)
def test_invalid_provider_configuration_fails_closed(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **kwargs)
