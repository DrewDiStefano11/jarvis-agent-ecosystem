"""A conservative bounded-request estimate; native provider quota remains authoritative."""

import json

from app.models.execution_budget import ExecutionBudget


def direct_request_bound(request, model=None) -> int:
    # Bound the provider-visible wire body, including the full response schema.
    # Metadata/timeouts are not prompt tokens. Unknown model names use the native
    # 200-character maximum in UTF-8; the small extra margin covers Ollama framing.
    if request.tools:
        raise ValueError("Direct planning does not authorize model tool calls")
    payload = {
        "model": model or request.model or "\U0001f7e3" * 200,
        "messages": [
            {
                key: value
                for key, value in {
                    "role": message.role.value,
                    "content": message.content,
                    "name": message.name,
                }.items()
                if value is not None
            }
            for message in request.messages
        ],
        "stream": False,
    }
    if request.temperature is not None:
        payload["temperature"] = request.temperature
    if request.max_output_tokens is not None:
        payload["max_completion_tokens"] = request.max_output_tokens
    if request.output_schema is not None:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": request.output_schema.name,
                "schema": request.output_schema.json_schema,
            },
        }
    return (
        len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        + 64 * len(request.messages)
        + 1152
        + (request.max_output_tokens or 0)
    )


def validate_runtime_budget(snapshot, budget: ExecutionBudget):
    stage = budget.direct_stage()
    request = snapshot.specification.autonomous_execution
    if (
        snapshot.specification.maximum_permitted_attempts > budget.maximumAttempts
        or request.maximum_provider_requests > stage.maximumRequests
        or request.maximum_repair_calls
        or request.maximum_output_tokens > stage.maximumOutputTokens
        or request.verification_criteria
        or request.correction_policy is not None
        or request.coordinator_verification is not None
        or request.execution_type != "planning_review"
        or request.response_format != "planning_review_json_v1"
    ):
        raise ValueError("Runtime scope exceeds persisted execution budget")
    return stage


def validate_direct_assembly(assembly, models=()):
    # Reuse the actual worker message/schema construction; do not estimate only source text.
    from app.autonomous_worker.service import AutonomousWorkerService
    from app.model_providers.contracts import ModelCapability, ModelExecutionRequest

    budget = assembly.executionBudget
    stage = budget.direct_stage()
    if assembly.modelRequest is None:
        return
    messages, _ = AutonomousWorkerService._execution_messages(
        assembly, response_format="planning_review_json_v1"
    )
    request = ModelExecutionRequest(
        messages=messages,
        temperature=0,
        max_output_tokens=stage.maximumOutputTokens,
        task_id=assembly.taskId,
        correlation_id="x" * 120,
        required_capability=ModelCapability.CHAT,
        **AutonomousWorkerService._output_options("planning_review_json_v1"),
    )
    bound = max(direct_request_bound(request, model) for model in (models or (None,)))
    if bound > budget.maximumTokens or bound - stage.maximumOutputTokens > stage.maximumInputTokens:
        raise ValueError(
            f"Conservative full request bound {bound} exceeds the approved stage/aggregate budget"
        )
    return bound


def direct_providers(router):
    from app.model_providers.contracts import ModelCapability
    from app.model_providers.ollama import OllamaProvider
    from app.model_providers.openai_compatible import OpenAICompatibleProvider

    # This is an implementation guarantee, not a model-supplied capability claim.
    return tuple(
        provider
        for provider in router.registry.list()
        if type(provider) in {OllamaProvider, OpenAICompatibleProvider}
        and {ModelCapability.CHAT, ModelCapability.STRUCTURED_OUTPUT} <= provider.capabilities
    )
