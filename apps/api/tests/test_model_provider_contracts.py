from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.model_providers.budget import BudgetTracker, ModelPricing, TaskBudget
from app.model_providers.contracts import (
    MessageRole,
    ModelCapability,
    ModelExecutionRequest,
    ModelExecutionResponse,
    ModelMessage,
    UsageQuality,
)
from app.model_providers.errors import BudgetExceededError, ModelProviderError
from app.model_providers.security import REDACTED, redact_secrets


def request(**changes: object) -> ModelExecutionRequest:
    return ModelExecutionRequest(
        prompt="hello", task_id="task-1", correlation_id="corr-1", **changes
    )


def response(**changes: object) -> ModelExecutionResponse:
    values = {
        "content": "answer",
        "provider": "fixture",
        "model": "fixture-model",
        "input_tokens": 3,
        "output_tokens": 2,
        "latency_ms": 1,
        "task_id": "task-1",
        "correlation_id": "corr-1",
    }
    values.update(changes)
    return ModelExecutionResponse(**values)


def test_prompt_is_normalized_and_messages_are_validated() -> None:
    item = request(required_capability=ModelCapability.CODE_GENERATION)
    assert item.prompt is None
    assert item.messages[0].role == MessageRole.USER
    with pytest.raises(ValidationError):
        ModelExecutionRequest()
    with pytest.raises(ValidationError):
        ModelExecutionRequest(prompt="one", messages=[{"role": "user", "content": "two"}])
    with pytest.raises(ValidationError):
        ModelExecutionRequest(prompt="hello", timeout_seconds=0)
    with pytest.raises(ValidationError):
        ModelExecutionRequest(prompt="hello", streaming=True)


def test_secret_metadata_is_rejected_and_response_derives_total() -> None:
    secret_metadata = [
        {"api_key": "secret"},
        {"nested": {"api_key": "secret"}},
        {"items": [{"authorization": "Bearer secret"}]},
        {"deep": ({"deeper": [{"token": "secret"}]},)},
        {"one": {"two": {"three": {"password": "secret"}}}},
        {"one": [{"two": ({"credential": "secret"},)}]},
    ]
    for metadata in secret_metadata:
        with pytest.raises(ValidationError):
            ModelExecutionRequest(prompt="hello", metadata=metadata)
    with pytest.raises(ValidationError):
        ModelMessage(
            role=MessageRole.USER,
            content="hello",
            metadata={"nested": [{"secret": "value"}]},
        )
    item = response()
    assert item.total_tokens == 5
    assert item.usage_quality == UsageQuality.UNKNOWN


def test_recursive_redaction_and_error_details_never_expose_secrets() -> None:
    secret = "sk-example-secret-value"
    value = redact_secrets(
        {"authorization": f"Bearer {secret}", "nested": [{"password": secret}, f"token={secret}"]}
    )
    assert secret not in str(value)
    assert value["authorization"] == REDACTED
    error = ModelProviderError(
        f"authorization: Bearer {secret}",
        metadata={"api_key": secret, "nested": {"token": secret}},
    )
    assert secret not in str(error.safe_details())


def test_budget_counts_requests_preflight_tokens_and_cost() -> None:
    tracker = BudgetTracker(
        TaskBudget(
            maximum_requests=2,
            maximum_output_tokens=5,
            maximum_total_tokens=10,
            maximum_cost_usd=1,
        ),
        {"fixture-model": ModelPricing(input_per_million_usd=10, output_per_million_usd=20)},
    )
    tracker.before_attempt(request(model="fixture-model", max_output_tokens=5), provider="fixture")
    cost = tracker.record(response())
    assert tracker.usage.requests == 1
    assert tracker.usage.total_tokens == 5
    assert cost == pytest.approx(0.00007)
    with pytest.raises(BudgetExceededError) as raised:
        tracker.before_attempt(
            request(model="fixture-model", max_output_tokens=4), provider="fixture"
        )
    assert raised.value.task_id == "task-1"
    assert raised.value.correlation_id == "corr-1"


def test_unknown_usage_is_not_treated_as_zero() -> None:
    tracker = BudgetTracker(TaskBudget(maximum_requests=1, maximum_total_tokens=10))
    tracker.before_attempt(request())
    with pytest.raises(BudgetExceededError):
        tracker.record(
            response(
                input_tokens=None,
                output_tokens=None,
                total_tokens=None,
                usage_quality=UsageQuality.UNKNOWN,
            )
        )


@pytest.mark.parametrize(
    ("input_tokens", "output_tokens", "total_tokens"),
    [
        (None, None, None),
        (3, None, None),
        (None, 2, None),
        (3, None, 5),
    ],
)
def test_cost_budget_rejects_missing_or_incomplete_usage(
    input_tokens: int | None,
    output_tokens: int | None,
    total_tokens: int | None,
) -> None:
    tracker = BudgetTracker(
        TaskBudget(
            maximum_requests=3,
            maximum_cost_usd=1,
            reject_unknown_usage=False,
        ),
        {
            "fixture-model": ModelPricing(
                input_per_million_usd=10,
                output_per_million_usd=20,
            )
        },
    )
    tracker.before_attempt(request(model="fixture-model"), provider="fixture")
    secret = "raw-secret-response-value"

    with pytest.raises(BudgetExceededError) as raised:
        tracker.record(
            response(
                content=secret,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                usage_quality=UsageQuality.UNKNOWN,
            )
        )

    details = raised.value.safe_details()
    assert details["provider"] == "fixture"
    assert details["model"] == "fixture-model"
    assert details["task_id"] == "task-1"
    assert details["correlation_id"] == "corr-1"
    assert details["metadata"]["reason"] == "usage_unavailable_for_cost_budget"
    assert secret not in str(details)
    assert tracker.usage.requests == 1
    assert not tracker.usage.usage_known
    with pytest.raises(BudgetExceededError) as blocked:
        tracker.before_attempt(request(model="fixture-model"), provider="fixture")
    assert blocked.value.metadata["reason"] == "usage_unavailable_for_cost_budget"
    assert tracker.usage.requests == 1


def test_usage_without_cost_or_token_limit_may_be_unknown() -> None:
    tracker = BudgetTracker(TaskBudget(maximum_requests=1))
    tracker.before_attempt(request())
    cost = tracker.record(
        response(
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            usage_quality=UsageQuality.UNKNOWN,
        )
    )
    assert cost is None
    assert not tracker.usage.usage_known


def test_cost_budget_accepts_complete_and_explicit_zero_usage() -> None:
    pricing = {
        "fixture-model": ModelPricing(
            input_per_million_usd=10,
            output_per_million_usd=20,
        )
    }
    complete = BudgetTracker(TaskBudget(maximum_requests=1, maximum_cost_usd=1), pricing)
    complete.before_attempt(request(model="fixture-model"), provider="fixture")
    assert complete.record(response()) == pytest.approx(0.00007)
    assert complete.usage.usage_known

    zero = BudgetTracker(TaskBudget(maximum_requests=1, maximum_cost_usd=1), pricing)
    zero.before_attempt(request(model="fixture-model"), provider="fixture")
    assert zero.record(response(input_tokens=0, output_tokens=0, total_tokens=0)) == 0
    assert zero.usage.usage_known


def test_cost_budget_fails_closed_for_missing_pricing_and_response_alias() -> None:
    unpriced = BudgetTracker(TaskBudget(maximum_requests=1, maximum_cost_usd=1))
    with pytest.raises(BudgetExceededError) as preflight:
        unpriced.before_attempt(request(model="vendor/model-latest"), provider="remote")
    assert preflight.value.provider == "remote"
    assert preflight.value.model == "vendor/model-latest"
    assert preflight.value.metadata["reason"] == "pricing_unavailable"

    no_cost_limit = BudgetTracker(TaskBudget(maximum_requests=1))
    no_cost_limit.before_attempt(request(model="vendor/model-latest"), provider="remote")

    priced = BudgetTracker(
        TaskBudget(maximum_requests=1, maximum_cost_usd=1),
        {
            "vendor/model-latest": ModelPricing(
                input_per_million_usd=1,
                output_per_million_usd=1,
            )
        },
    )
    priced.before_attempt(request(model="vendor/model-latest"), provider="remote")
    with pytest.raises(BudgetExceededError) as alias:
        priced.record(response(provider="remote", model="vendor/model-2026-07-01"))
    assert alias.value.metadata["reason"] == "response_model_pricing_unavailable"


def test_configured_cost_pricing_enforces_cap() -> None:
    tracker = BudgetTracker(
        TaskBudget(maximum_requests=1, maximum_cost_usd=0.000001),
        {
            "fixture-model": ModelPricing(
                input_per_million_usd=10,
                output_per_million_usd=20,
            )
        },
    )
    tracker.before_attempt(request(model="fixture-model"), provider="fixture")
    with pytest.raises(BudgetExceededError, match="cost budget exceeded"):
        tracker.record(response())
