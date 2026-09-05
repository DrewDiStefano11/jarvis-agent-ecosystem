from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from app.autonomous_worker.repository import canonical_json
from app.autonomous_worker.service import AutonomousWorkerService
from app.model_providers.contracts import ModelCapability, ModelExecutionRequest, ModelOutputSchema
from app.model_providers.ollama import OllamaProvider
from app.model_providers.openai_compatible import OpenAICompatibleProvider
from app.models.agent_runtime import AutonomousExecutionSpecification
from app.models.autonomous_worker import PlanningReviewResult
from tests.test_autonomous_worker import VALID_RESULT, FakeRouter, worker_fixture


def test_output_schema_is_bounded_and_result_limits_remain_authoritative() -> None:
    for schema in [{"type": "array"}, {"type": "object", "description": "x" * 65_537}]:
        with pytest.raises(ValidationError):
            ModelOutputSchema(name="result", json_schema=schema)
    options = AutonomousWorkerService._output_options("planning_review_json_v1")
    schema = options["output_schema"].json_schema
    assert set(schema["required"]) == set(PlanningReviewResult.model_fields)
    assert set(schema["$defs"]["PlanningRecommendation"]["properties"]) == {
        "title",
        "description",
        "priority",
    }
    assert "maxLength" not in json.dumps(schema)
    assert "maxItems" not in json.dumps(schema)
    with pytest.raises(ValidationError):
        PlanningReviewResult.model_validate({**VALID_RESULT, "summary": "x" * 2001})


def test_legacy_request_serialization_and_execution_hash_are_preserved(tmp_path: Path) -> None:
    app, client, _, _ = worker_fixture(tmp_path, router=FakeRouter([]))
    try:
        run = app.state.agent_runtime_service.repository.load_run("run-autonomous-1")
        specification = run.specification.autonomous_execution
        legacy = specification.model_dump(mode="json", exclude_none=False)
        assert "response_format" not in legacy
        assert (
            AutonomousExecutionSpecification.model_validate(legacy).model_dump(mode="json")
            == legacy
        )
        assert (
            "response_format"
            not in run.model_dump(mode="json")["specification"]["autonomous_execution"]
        )
        assembly = app.state.model_execution_repository.load_context_assembly(
            specification.context_assembly_id
        )
        messages, old_hash = AutonomousWorkerService._execution_messages(assembly)
        old_payload = {
            "assemblyRequestHash": assembly.requestHash,
            "messages": [message.model_dump(mode="json") for message in messages],
            "executionType": "planning_review",
            "outputSchemaVersion": "1.0",
        }
        assert old_hash == sha256(canonical_json(old_payload).encode()).hexdigest()
        _, new_hash = AutonomousWorkerService._execution_messages(
            assembly, (), "planning_review_json_v1"
        )
        assert new_hash != old_hash
        upgraded = AutonomousExecutionSpecification.model_validate(
            {**legacy, "response_format": "planning_review_json_v1"}
        )
        assert upgraded.model_dump(mode="json")["response_format"] == "planning_review_json_v1"
        assert upgraded.model_dump(mode="json") != legacy
    finally:
        client.__exit__(None, None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["ollama", "compatible", "compatible-structured"])
@pytest.mark.parametrize("opt_in", [False, True])
async def test_output_options_map_only_to_supported_transport_fields(
    kind: str, opt_in: bool
) -> None:
    sent = []

    def handle(request):
        sent.append(json.loads(request.content))
        if kind == "ollama":
            return httpx.Response(200, json={"model": "local", "message": {"content": "{}"}})
        return httpx.Response(
            200, json={"model": "local", "choices": [{"message": {"content": "{}"}}]}
        )

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:11535", transport=httpx.MockTransport(handle)
    ) as client:
        if kind == "ollama":
            provider = OllamaProvider(
                default_model="local", execution_mode="local_only", client=client
            )
        else:
            capabilities = {ModelCapability.CHAT}
            if kind == "compatible-structured":
                capabilities.add(ModelCapability.STRUCTURED_OUTPUT)
            provider = OpenAICompatibleProvider(
                name="compatible",
                default_model="local",
                base_url="http://127.0.0.1:11535/v1",
                api_key=SecretStr("fixture"),
                execution_mode="local_only",
                capabilities=frozenset(capabilities),
                client=client,
            )
        options = (
            AutonomousWorkerService._output_options("planning_review_json_v1") if opt_in else {}
        )
        await provider.execute(
            ModelExecutionRequest(prompt="Return JSON", max_output_tokens=256, **options)
        )
    body = sent[0]
    if kind == "ollama":
        assert ("format" in body) is opt_in
        assert ("think" in body) is opt_in
        if opt_in:
            assert body["think"] is False
            assert body["format"] == options["output_schema"].json_schema
    else:
        assert "think" not in body
        assert "prefer_no_reasoning" not in body
        assert ("response_format" in body) is (opt_in and kind == "compatible-structured")
        if opt_in and kind == "compatible-structured":
            assert (
                body["response_format"]["json_schema"]["schema"]
                == options["output_schema"].json_schema
            )
