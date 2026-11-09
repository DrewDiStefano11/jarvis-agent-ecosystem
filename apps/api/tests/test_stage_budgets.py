"""Opt-in stage budgets must remove hidden planning calls and bind native execution."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.autonomous_worker.errors import AutonomousWorkerError
from app.autonomous_worker.service import AutonomousWorkerService
from app.main import create_app
from app.models.agent_runtime import AutonomousExecutionSpecification
from app.models.context import ContextAssembly
from tests.test_context_integration import context_body


def envelope(**overrides):
    return {
        "maximumRequests": 1,
        "maximumTokens": 7500,
        "maximumAttempts": 1,
        "stages": [
            {
                "stage": "planning",
                "maximumRequests": 1,
                "maximumInputTokens": 5964,
                "maximumOutputTokens": 1536,
            }
        ],
        **overrides,
    }


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_MODEL_EXECUTION_MODE", "disabled")
    monkeypatch.setenv("JARVIS_AUTONOMOUS_WORKER_ENABLED", "false")
    app = create_app(database_url=f"sqlite:///{(tmp_path / 'budget.db').as_posix()}")
    from app.model_providers.contracts import ModelCapability
    from app.model_providers.ollama import OllamaProvider

    provider = OllamaProvider(
        name="fixture",
        default_model="fixture-model",
        base_url="http://127.0.0.1:9",
        capabilities=frozenset({ModelCapability.CHAT, ModelCapability.STRUCTURED_OUTPUT}),
    )
    app.state.model_router.registry._providers = {provider.name: provider}
    with TestClient(app) as api:
        yield api


def test_direct_assembly_and_replay_skip_team_and_decomposition_models(client, monkeypatch):
    from app.decomposition.service import DecompositionService
    from app.team_selection.service import TeamSelectionService

    async def forbidden(*args, **kwargs):
        pytest.fail("Direct envelope dispatched an organizational planning stage")

    monkeypatch.setattr(TeamSelectionService, "assign_team", forbidden)
    monkeypatch.setattr(DecompositionService, "prepare", forbidden)
    client.app.state.settings.model_execution_mode = "local_only"
    task = client.post(
        "/api/tasks",
        json={"title": "Budgeted planning", "description": "Prepare a bounded checklist."},
    ).json()["data"]
    body = context_body(task_id=task["id"])
    body["executionBudget"] = envelope()
    body["policy"]["reservedOutputTokens"] = 1536
    body["policy"]["maximumContextTokens"] = 4096
    response = client.post(
        "/api/context/assemblies", json=body, headers={"Idempotency-Key": "direct-budget"}
    )
    assert response.status_code == 201, response.text
    assembly = response.json()["data"]
    assert assembly["executionBudget"] == envelope()
    replay = client.post(
        "/api/context/assemblies", json=body, headers={"Idempotency-Key": "direct-budget"}
    )
    assert replay.status_code == 201
    assert replay.json()["data"] == assembly
    assert (
        client.get(f"/api/context/assemblies/{assembly['id']}").json()["data"]["executionBudget"]
        == envelope()
    )


@pytest.mark.parametrize(
    "budget",
    [
        envelope(maximumTokens=7000),
        envelope(maximumRequests=True),
        envelope(
            stages=[
                {
                    "stage": "planning",
                    "maximumRequests": 2,
                    "maximumInputTokens": 5964,
                    "maximumOutputTokens": 1536,
                }
            ]
        ),
    ],
)
def test_invalid_aggregate_envelope_is_rejected_before_work(client, budget):
    body = context_body()
    body["executionBudget"] = budget
    assert client.post("/api/context/assemblies", json=body).status_code == 422


def test_wider_runtime_spec_cannot_exceed_persisted_assembly_envelope(client):
    task = client.post(
        "/api/tasks", json={"title": "Fixed scope", "description": "Prepare a checklist."}
    ).json()["data"]
    body = context_body(task_id=task["id"])
    response = client.post("/api/context/assemblies", json=body)
    assert response.status_code == 201
    payload = response.json()["data"]
    payload["executionBudget"] = envelope()
    assembly = ContextAssembly.model_validate(payload)
    execution = AutonomousExecutionSpecification(
        execution_type="planning_review", context_assembly_id=assembly.id
    )
    snapshot = SimpleNamespace(
        specification=SimpleNamespace(
            task_id=task["id"], autonomous_execution=execution, maximum_permitted_attempts=3
        )
    )
    with pytest.raises(AutonomousWorkerError) as error:
        AutonomousWorkerService._validate_assembly(snapshot, assembly)
    assert error.value.code == "MODEL_EXECUTION_BUDGET_EXCEEDED"


def test_supported_direct_scope_and_legacy_serialization(client):
    body = context_body()
    response = client.post("/api/context/assemblies", json=body)
    assert response.status_code == 201
    assert "executionBudget" not in response.json()["data"]
    assert "executionBudget" not in __import__(
        "app.models.context", fromlist=["CreateContextAssemblyRequest"]
    ).CreateContextAssemblyRequest.model_validate(body).model_dump(mode="json")
    payload = response.json()["data"]
    payload["executionBudget"] = envelope()
    assembly = ContextAssembly.model_validate(payload)
    execution = AutonomousExecutionSpecification(
        execution_type="planning_review",
        response_format="planning_review_json_v1",
        context_assembly_id=assembly.id,
        maximum_provider_requests=1,
        maximum_repair_calls=0,
        maximum_output_tokens=1536,
    )
    snapshot = SimpleNamespace(
        specification=SimpleNamespace(
            task_id=assembly.taskId, autonomous_execution=execution, maximum_permitted_attempts=1
        )
    )
    AutonomousWorkerService._validate_assembly(snapshot, assembly)


def test_unsupported_multistage_graph_blocks_before_team_selection(client, monkeypatch):
    from app.team_selection.service import TeamSelectionService

    async def forbidden(*args, **kwargs):
        pytest.fail("Unsupported graph reached a planning model")

    monkeypatch.setattr(TeamSelectionService, "assign_team", forbidden)
    body = context_body()
    body["executionBudget"] = envelope(
        maximumRequests=2,
        maximumTokens=15000,
        stages=[
            {
                "stage": "team_selection",
                "maximumRequests": 1,
                "maximumInputTokens": 5964,
                "maximumOutputTokens": 1536,
            },
            {
                "stage": "planning",
                "maximumRequests": 1,
                "maximumInputTokens": 5964,
                "maximumOutputTokens": 1536,
            },
        ],
    )
    response = client.post("/api/context/assemblies", json=body)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "EXECUTION_GRAPH_UNSUPPORTED"


def test_stage_contract_rejects_duplicates_unknown_stages_and_token_oversubscription():
    from pydantic import ValidationError

    from app.models.execution_budget import ExecutionBudget

    for invalid in (
        envelope(stages=[envelope()["stages"][0]] * 2),
        envelope(stages=[{**envelope()["stages"][0], "stage": "unbounded"}]),
        envelope(maximumTokens=7499),
        envelope(stages=[{**envelope()["stages"][0], "maximumInputTokens": True}]),
    ):
        with pytest.raises(ValidationError):
            ExecutionBudget.model_validate(invalid)


def test_direct_estimate_bounds_schema_and_utf8_framing():
    import json

    from app.model_providers.contracts import MessageRole, ModelExecutionRequest, ModelMessage
    from app.services.execution_budget import direct_request_bound

    request = ModelExecutionRequest(
        messages=[ModelMessage(role=MessageRole.USER, content="Review 🟣 schedules")],
        model="fixture-model",
        temperature=0,
        max_output_tokens=1536,
        **AutonomousWorkerService._output_options("planning_review_json_v1"),
    )
    actual_payload = {
        "model": request.model,
        "messages": [{"role": "user", "content": request.messages[0].content}],
        "temperature": 0,
        "max_completion_tokens": 1536,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": request.output_schema.name,
                "schema": request.output_schema.json_schema,
            },
        },
    }
    native_bound = len(json.dumps(actual_payload, ensure_ascii=False).encode()) + 64 + 1024 + 1536
    assert direct_request_bound(request) >= native_bound


def test_large_context_is_rejected_before_any_dispatch_or_context_commit(client):
    body = context_body(content="x" * 10000)
    body["executionBudget"] = envelope()
    body["policy"]["maximumContextTokens"] = 8192
    before = len(client.get("/api/context/assemblies").json()["data"])
    response = client.post("/api/context/assemblies", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "EXECUTION_BUDGET_EXCEEDED"
    assert len(client.get("/api/context/assemblies").json()["data"]) == before


@pytest.mark.parametrize("kind", ["chatgpt", "codex"])
@pytest.mark.asyncio
async def test_direct_worker_rejects_unbounded_native_transports_before_dispatch(client, kind):
    from app.model_providers.contracts import MessageRole, ModelExecutionRequest, ModelMessage
    from app.model_providers.subscription import ChatGPTPlanProvider, CodexProvider

    # Construct inert type identities only; no account/session/credentials exist.
    provider = object.__new__(ChatGPTPlanProvider if kind == "chatgpt" else CodexProvider)
    provider.name = "unbounded-fixture"
    provider.default_model = "fixture-model"
    from app.model_providers.contracts import ModelCapability

    provider.capabilities = frozenset({ModelCapability.CHAT, ModelCapability.STRUCTURED_OUTPUT})
    payload = client.post("/api/context/assemblies", json=context_body()).json()["data"]
    payload["executionBudget"] = envelope()
    assembly = ContextAssembly.model_validate(payload)
    request = AutonomousExecutionSpecification(
        execution_type="planning_review",
        context_assembly_id=assembly.id,
        response_format="planning_review_json_v1",
        maximum_provider_requests=1,
        maximum_repair_calls=0,
        maximum_output_tokens=1536,
    )
    snapshot = SimpleNamespace(
        specification=SimpleNamespace(
            task_id=assembly.taskId, autonomous_execution=request, maximum_permitted_attempts=1
        )
    )

    def forbidden(*args, **kwargs):
        pytest.fail("Unbounded provider reached dispatch/accounting")

    async def forbidden_transport(**kwargs):
        pytest.fail("Unbounded provider reached physical transport routing")

    import asyncio

    executor = SimpleNamespace(
        execution_timeout=lambda _: 30,
        _lease_heartbeat=lambda *args: asyncio.sleep(10),
        _assert_live_policy=lambda *args: None,
        executions=SimpleNamespace(
            get=lambda _: SimpleNamespace(requestCount=0),
            load_context_assembly=lambda _: assembly,
            record_call_started=forbidden,
        ),
        router=SimpleNamespace(
            registry=SimpleNamespace(list=lambda: [provider]),
            effective_model=lambda *args: "fixture-model",
            execute=forbidden_transport,
        ),
        settings=SimpleNamespace(
            model_execution_mode="free_only",
            model_prefer_local=True,
            verified_cloud_execution_allowed=True,
        ),
    )
    transport = ModelExecutionRequest(
        messages=[ModelMessage(role=MessageRole.USER, content="Test")], max_output_tokens=1536
    )
    with pytest.raises(AutonomousWorkerError) as error:
        await AutonomousWorkerService._provider_call(
            executor, snapshot, None, "worker", "lease", "execution", transport, 1
        )
    assert error.value.code == "BOUNDED_PROVIDER_REQUIRED"


@pytest.mark.parametrize("adapter", ["ollama", "compatible"])
@pytest.mark.asyncio
async def test_bounded_native_adapters_put_completion_cap_on_physical_body(adapter):
    import json

    import httpx
    from pydantic import SecretStr

    from app.model_providers.contracts import (
        MessageRole,
        ModelCapability,
        ModelExecutionRequest,
        ModelMessage,
        ModelOutputSchema,
    )
    from app.model_providers.ollama import OllamaProvider
    from app.model_providers.openai_compatible import OpenAICompatibleProvider
    from app.services.execution_budget import direct_providers

    observed = []

    def transport(request):
        observed.append(json.loads(request.content))
        if adapter == "ollama":
            return httpx.Response(
                200,
                json={
                    "model": "fixture-model",
                    "message": {"role": "assistant", "content": "{}"},
                    "done": True,
                    "done_reason": "stop",
                    "prompt_eval_count": 10,
                    "eval_count": 2,
                },
            )
        return httpx.Response(
            200,
            json={
                "model": "fixture-model",
                "choices": [
                    {"message": {"role": "assistant", "content": "{}"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), base_url="http://127.0.0.1:9"
    ) as client:
        options = dict(
            name="fixture",
            base_url="http://127.0.0.1:9",
            default_model="fixture-model",
            capabilities=frozenset({ModelCapability.CHAT, ModelCapability.STRUCTURED_OUTPUT}),
            execution_mode="local_only",
            client=client,
        )
        provider = (
            OllamaProvider(**options)
            if adapter == "ollama"
            else OpenAICompatibleProvider(api_key=SecretStr("fake-test-only"), **options)
        )
        assert direct_providers(
            SimpleNamespace(registry=SimpleNamespace(list=lambda: [provider]))
        ) == (provider,)
        await provider.execute(
            ModelExecutionRequest(
                messages=[ModelMessage(role=MessageRole.USER, content="Return an object")],
                max_output_tokens=128,
                task_id="task-fixture",
                correlation_id="scope-fixture",
                output_schema=ModelOutputSchema(
                    name="budget_fixture",
                    json_schema={"type": "object", "properties": {}, "additionalProperties": False},
                ),
            )
        )
    assert len(observed) == 1
    if adapter == "ollama":
        assert observed[0]["options"]["num_predict"] == 128
        assert observed[0]["format"]["type"] == "object"
    else:
        assert observed[0]["max_tokens"] == 128
        assert observed[0]["response_format"]["type"] == "json_schema"
