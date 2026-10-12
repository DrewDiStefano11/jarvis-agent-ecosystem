from __future__ import annotations

import gzip
import json
import os

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from app.model_providers.budget import TaskBudget
from app.model_providers.contracts import (
    ModelCapability,
    ModelExecutionRequest,
)
from app.model_providers.errors import (
    BudgetExceededError,
    InvalidModelRequestError,
    MalformedProviderResponseError,
    ProviderExecutionDisabledError,
    UnknownProviderError,
)
from app.model_providers.ollama import OllamaProvider
from app.model_providers.openai_compatible import HealthCheckStrategy, OpenAICompatibleProvider
from app.model_providers.registry import ProviderRegistry
from app.model_providers.retry import RetryExecutor, RetryPolicy
from app.model_providers.router import ModelRouter, RoutingRequirements
from app.models.model_tools import ModelToolDefinition, ModelToolResult
from app.models.tool_execution import AuthorizeToolExecutionRequest

CAPABILITIES = frozenset({ModelCapability.CHAT, ModelCapability.TOOL_CALLING})


def definition() -> ModelToolDefinition:
    return ModelToolDefinition.model_validate(
        {
            "name": "workspace_read",
            "description": "Request reading a reviewed workspace path.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "maxLength": 240}},
                "required": ["path"],
                "additionalProperties": False,
            },
        }
    )


def request(**updates) -> ModelExecutionRequest:
    return ModelExecutionRequest(
        prompt="Request workspace_read for inputs/brief.txt.",
        tools=[definition()],
        tool_request_id="persisted-request-1",
        **updates,
    )


def native_call(kind: str, arguments=None, **updates) -> dict:
    arguments = {"path": "inputs/brief.txt"} if arguments is None else arguments
    result = {
        "type": "function",
        "function": {
            "name": "workspace_read",
            "arguments": json.dumps(arguments) if kind == "compatible" else arguments,
        },
    }
    if kind == "compatible":
        result["id"] = "native-call-1"
    return {**result, **updates}


def body(kind: str, calls=None, content=None) -> dict:
    calls = [native_call(kind)] if calls is None else calls
    message = {
        "role": "assistant",
        "content": content if content is not None else (None if kind == "compatible" else ""),
        "tool_calls": calls,
    }
    if kind == "ollama":
        return {
            "model": "local-model",
            "message": message,
            "done": True,
            "prompt_eval_count": 20,
            "eval_count": 10,
        }
    return {
        "model": "local-model",
        "choices": [{"message": message, "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 10},
    }


def provider(kind, client, **updates):
    common = dict(
        default_model="local-model",
        execution_mode="local_only",
        capabilities=CAPABILITIES,
        client=client,
    )
    common.update(updates)
    if kind == "ollama":
        return OllamaProvider(**common)
    return OpenAICompatibleProvider(
        name="compatible",
        base_url="http://127.0.0.1:11535/v1",
        api_key=SecretStr("transport-credential"),
        health_strategy=HealthCheckStrategy.CONFIGURATION,
        **common,
    )


def router(adapter):
    registry = ProviderRegistry([adapter])
    return ModelRouter(registry, RetryExecutor(RetryPolicy(maximum_attempts=1)))


@pytest.mark.parametrize("kind", ["ollama", "compatible"])
async def test_production_router_native_request_and_stable_identity(kind):
    sent = []

    def handle(req):
        if req.method == "GET":
            return httpx.Response(200, json={"models": [{"name": "local-model"}]})
        sent.append(json.loads(req.content))
        return httpx.Response(200, json=body(kind))

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:11535/v1/", transport=httpx.MockTransport(handle)
    ) as client:
        production_router = router(provider(kind, client))
        first = await production_router.execute(
            request=request(),
            requirements=RoutingRequirements(),
            budget=TaskBudget(maximum_output_tokens=32),
        )
        replay = await production_router.execute(
            request=request(), requirements=RoutingRequirements(), budget=TaskBudget()
        )
    assert first.tool_requests == replay.tool_requests
    assert first.content == ""
    assert first.tool_request_id == "persisted-request-1"
    assert first.tool_requests[0].trust == "model_request"
    assert first.tool_requests[0].arguments == {"path": "inputs/brief.txt"}
    assert sent[0]["tools"] == [definition().wire_definition()]
    assert "tool_request_id" not in sent[0]
    assert "maximum_tool_calls" not in sent[0]
    assert first.routing_metadata["selected_provider"] == first.provider
    assert first.total_tokens == 30
    # Neither normalized model request nor result can become a trusted approval.
    with pytest.raises(ValidationError):
        AuthorizeToolExecutionRequest.model_validate(first.tool_requests[0].model_dump())
    result = ModelToolResult(
        call_id=first.tool_requests[0].call_id,
        execution_id="execution-1",
        status="uncertain",
        output=None,
    )
    with pytest.raises(ValidationError):
        AuthorizeToolExecutionRequest.model_validate(result.model_dump())


@pytest.mark.parametrize("kind", ["ollama", "compatible"])
@pytest.mark.parametrize(
    "mutation",
    [
        "unknown",
        "extra_argument",
        "missing",
        "type",
        "count",
        "secret",
        "wrong_native_type",
        "unknown_field",
        "truncated",
        "duplicate_id",
        "non_object",
        "depth",
        "bytes",
    ],
)
async def test_malformed_native_output_fails_without_raw_exception_chain(kind, mutation):
    response = body(kind)
    message = response["message"] if kind == "ollama" else response["choices"][0]["message"]
    call = native_call(kind)
    arguments = {"path": "inputs/brief.txt"}
    if mutation == "unknown":
        call["function"]["name"] = "shell_execute"
    elif mutation == "extra_argument":
        arguments["approved"] = True
    elif mutation == "missing":
        arguments = {}
    elif mutation == "type":
        arguments["path"] = 42
    elif mutation == "count":
        message["tool_calls"] = [call, call]
    elif mutation == "secret":
        arguments["path"] = "password=do-not-expose"
    elif mutation == "wrong_native_type":
        call["type"] = "custom"
    elif mutation == "unknown_field":
        call["approved"] = True
    elif mutation == "truncated":
        if kind == "ollama":
            response["done"] = False
        else:
            response["choices"][0]["finish_reason"] = "length"
    elif mutation == "duplicate_id":
        message["tool_calls"] = [call, call]
    elif mutation == "non_object":
        arguments = ["path"]
    elif mutation == "depth":
        arguments["path"] = [[[[[[[[[["x"]]]]]]]]]]
    elif mutation == "bytes":
        arguments["path"] = "x" * 16_385
    call["function"]["arguments"] = json.dumps(arguments) if kind == "compatible" else arguments
    if mutation not in {"count", "duplicate_id"}:
        message["tool_calls"] = [call]
    # Ollama does not expose native IDs; two calls with the same arguments are valid
    # when the application explicitly allows both, with different ordinal identities.
    limit = 2 if mutation == "duplicate_id" and kind == "compatible" else 1
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response)),
        base_url="http://127.0.0.1:11535/v1/",
    ) as client:
        with pytest.raises(MalformedProviderResponseError) as caught:
            await provider(kind, client).execute(request(maximum_tool_calls=limit))
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None
    assert "do-not-expose" not in str(caught.value)


@pytest.mark.parametrize("raw", ['{"path":"a","path":"b"}', '{"path":NaN}', "{broken"])
async def test_compatible_rejects_duplicate_and_invalid_json(raw):
    call = native_call("compatible")
    call["function"]["arguments"] = raw
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:11535/v1/",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=body("compatible", [call]))
        ),
    ) as client:
        with pytest.raises(MalformedProviderResponseError):
            await provider("compatible", client).execute(request())


@pytest.mark.parametrize("kind", ["ollama", "compatible"])
async def test_unsupported_and_disabled_before_client_creation(kind, monkeypatch):
    adapter = provider(kind, None, capabilities=frozenset({ModelCapability.CHAT}))
    monkeypatch.setattr(adapter, "_client_or_new", lambda: pytest.fail("client created"))
    with pytest.raises(InvalidModelRequestError):
        await adapter.execute(request())
    with pytest.raises(UnknownProviderError):
        await router(adapter).execute(
            request=request(), requirements=RoutingRequirements(), budget=TaskBudget()
        )
    adapter.capabilities = CAPABILITIES
    adapter.execution_mode = "disabled"
    with pytest.raises(ProviderExecutionDisabledError):
        await adapter.execute(request())


@pytest.mark.parametrize("kind", ["ollama", "compatible"])
async def test_unsolicited_calls_are_rejected(kind):
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:11535/v1/",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body(kind))),
    ) as client:
        with pytest.raises(MalformedProviderResponseError):
            await provider(kind, client).execute(ModelExecutionRequest(prompt="Hello"))


async def test_compatible_echoed_credential_is_rejected():
    call = native_call("compatible", {"path": "transport-credential"})
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:11535/v1/",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=body("compatible", [call]))
        ),
    ) as client:
        with pytest.raises(MalformedProviderResponseError) as caught:
            await provider("compatible", client).execute(request())
    assert "transport-credential" not in str(caught.value)
    assert caught.value.__context__ is None


@pytest.mark.parametrize(
    "location", ["escaped_argument", "content", "model", "id", "header", "malformed_model"]
)
async def test_credential_echo_rejection_covers_decoded_calls_and_all_response_fields(location):
    response = body("compatible")
    message = response["choices"][0]["message"]
    headers = {}
    credential = "transport-credential"
    if location == "escaped_argument":
        message["tool_calls"][0]["function"]["arguments"] = '{"path":"transport\\u002dcredential"}'
    elif location == "content":
        message["tool_calls"] = []
        message["content"] = credential
        response["choices"][0]["finish_reason"] = "stop"
    elif location in {"model", "id", "malformed_model"}:
        response["model" if location == "malformed_model" else location] = credential
        if location == "malformed_model":
            message["tool_calls"][0]["function"]["name"] = "unknown"
    else:
        headers["x-request-id"] = credential
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:11535/v1/",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=response, headers=headers)
        ),
    ) as client:
        with pytest.raises(MalformedProviderResponseError) as caught:
            await provider("compatible", client).execute(request())
    assert credential not in str(caught.value.safe_details())
    assert caught.value.__context__ is None


@pytest.mark.parametrize("kind", ["ollama", "compatible"])
@pytest.mark.parametrize(
    "case", ["duplicate_outer", "duplicate_argument", "wire_size", "normalized_size"]
)
async def test_wire_and_normalized_response_limits(kind, case):
    response = body(kind)
    if case == "wire_size":
        wire = b" " * 262_145
    elif case == "duplicate_outer":
        wire = b'{"model":"a","model":"b"}'
    elif case == "duplicate_argument":
        if kind == "ollama":
            wire = (
                json.dumps(response)
                .replace('"path": "inputs/brief.txt"', '"path":"a","path":"b"')
                .encode()
            )
        else:
            response["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = (
                '{"path":"a","path":"b"}'
            )
            wire = json.dumps(response).encode()
    else:
        message = response["message"] if kind == "ollama" else response["choices"][0]["message"]
        message["content"] = "x" * 65_537
        wire = json.dumps(response).encode()
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:11535/v1/",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=wire)),
    ) as client:
        with pytest.raises(MalformedProviderResponseError) as caught:
            await provider(kind, client).execute(request())
    assert caught.value.__context__ is None


@pytest.mark.parametrize("kind", ["ollama", "compatible"])
async def test_compressed_native_response_is_decoded_once(kind):
    compressed = gzip.compress(json.dumps(body(kind)).encode())
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:11535/v1/",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                content=compressed,
                headers={"Content-Encoding": "gzip", "Content-Length": str(len(compressed))},
            )
        ),
    ) as client:
        response = await provider(kind, client).execute(request())
    assert response.tool_requests[0].arguments == {"path": "inputs/brief.txt"}


async def test_deep_native_arguments_fail_as_normalized_error():
    # Deep wire JSON can parse before schema validation; credential scanning must
    # not recursively overflow while inspecting otherwise bounded native data.
    wire = (
        '{"model":"local-model","choices":[{"finish_reason":"tool_calls","message":'
        + '{"role":"assistant","content":null,"tool_calls":[{"type":"function","id":"call-1",'
        + '"function":{"name":"workspace_read","arguments":'
        + "[" * 600
        + "0"
        + "]" * 600
        + "}}]}}]}"
    )
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:11535/v1/",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=wire)),
    ) as client:
        with pytest.raises(MalformedProviderResponseError) as caught:
            await provider("compatible", client).execute(request())
    assert caught.value.__context__ is None


async def test_router_revalidates_provider_call_identity_and_mutation():
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:11535/v1/",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body("compatible"))),
    ) as client:
        adapter = provider("compatible", client)
        response = await adapter.execute(request())
        response.tool_requests[0].arguments["path"] = "changed"

        async def mutated(_):
            return response

        adapter.execute = mutated
        with pytest.raises(MalformedProviderResponseError):
            await router(adapter).execute(
                request=request(), requirements=RoutingRequirements(), budget=TaskBudget()
            )


def test_distinct_argument_and_request_identities_do_not_alias():
    from app.models.model_tools import tool_call_identity

    base = dict(
        request_id="request-1",
        provider="ollama",
        model="local",
        ordinal=0,
        name="workspace_read",
        arguments={"path": "a"},
    )
    original = tool_call_identity(**base)
    for change in (
        {"request_id": "request-2"},
        {"ordinal": 1},
        {"arguments": {"path": "b"}},
        {"provider": "compatible"},
        {"model": "other"},
    ):
        assert tool_call_identity(**{**base, **change}) != original


def test_request_and_result_are_rejected_by_existing_execution_api(tmp_path):
    from fastapi.testclient import TestClient
    from sqlalchemy import func, select

    from app.db.models import ToolExecutionRow
    from app.main import create_app
    from tests.test_agent_runtime_sql_control_plane import grant_runtime_permissions
    from tests.test_persistence import database_url

    app = create_app(database_url=database_url(tmp_path / "tool-authority.db"))
    with TestClient(app) as client:
        actor_id = grant_runtime_permissions(app, "model-request-test", task_id="task-demo")
        before = app.state.task_leases.session_factory
        # Even enabling the existing tool gate cannot turn model data into approval.
        app.state.settings.tool_execution_enabled = True
        payload = {
            "call_id": "mtc_" + "a" * 64,
            "ordinal": 0,
            "name": "workspace_read",
            "arguments": {"path": "inputs/brief.txt"},
            "trust": "model_request",
        }
        response = client.post(
            "/api/tool-executions/authorize", json=payload, headers={"X-Jarvis-Actor-Id": actor_id}
        )
        assert response.status_code == 422
        result = ModelToolResult(
            call_id=payload["call_id"],
            execution_id="unapproved",
            status="completed",
            output="observation",
        )
        response = client.post(
            "/api/tool-executions/authorize",
            json=result.model_dump(),
            headers={"X-Jarvis-Actor-Id": actor_id},
        )
        assert response.status_code == 422
        with before() as session:
            assert session.scalar(select(func.count()).select_from(ToolExecutionRow)) == 0


@pytest.mark.parametrize(
    "change",
    [
        {"tool_request_id": None},
        {"maximum_tool_calls": True},
        {"tools": [definition(), definition()]},
    ],
)
def test_invalid_request_bounds(change):
    payload = request().model_dump()
    with pytest.raises(ValidationError):
        ModelExecutionRequest.model_validate({**payload, **change})


@pytest.mark.parametrize(
    "change",
    [
        {"parameters": {"type": "object", "$ref": "http://example.invalid"}},
        {"name": "shell.execute"},
        {"authorized": True},
    ],
)
def test_invalid_tool_definitions(change):
    with pytest.raises(ValidationError):
        ModelToolDefinition.model_validate({**definition().model_dump(), **change})


@pytest.mark.parametrize(
    ("schema", "value", "invalid"),
    [
        ({"type": "integer"}, 2, True),
        ({"type": "number"}, 1.5, False),
        ({"type": "boolean"}, True, 1),
        ({"type": "null"}, None, "null"),
        ({"type": "string", "maxLength": 2, "enum": ["a", "b"]}, "a", "c"),
        ({"type": "array", "items": {"type": "integer"}, "maxItems": 2}, [1, 2], [1, 2, 3]),
        (
            {
                "type": "object",
                "properties": {"flag": {"type": "boolean"}},
                "required": ["flag"],
                "additionalProperties": False,
            },
            {"flag": True},
            {"flag": True, "extra": 1},
        ),
    ],
)
def test_strict_parameter_schema_subset(schema, value, invalid):
    tool = ModelToolDefinition.model_validate(
        {
            "name": "probe",
            "description": "Request only; no executable registration.",
            "parameters": {
                "type": "object",
                "properties": {"value": schema},
                "required": ["value"],
                "additionalProperties": False,
            },
        }
    )
    tool.validate_arguments({"value": value})
    with pytest.raises(ValueError):
        tool.validate_arguments({"value": invalid})


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "properties": {}, "required": [], "additionalProperties": True},
        {"type": "object", "properties": {}, "required": [], "additionalProperties": 0},
        {
            "type": "object",
            "properties": {"x": {"type": "integer"}},
            "required": [],
            "additionalProperties": False,
        },
        {"type": "array", "items": {"type": "integer"}},
        {"type": "string"},
        {"type": "integer", "enum": [True]},
        {"type": "integer", "minimum": 0},
    ],
)
def test_incomplete_or_unsupported_schema_constraints_fail_explicitly(schema):
    from app.models.model_tools import ToolParameterSchema

    with pytest.raises(ValidationError):
        ToolParameterSchema.model_validate(schema)


def test_result_output_byte_and_trust_bounds():
    call_id = "mtc_" + "a" * 64
    with pytest.raises(ValidationError):
        ModelToolResult(call_id=call_id, execution_id="e", status="completed", output="😀" * 20_000)
    with pytest.raises(ValidationError):
        ModelToolResult(
            call_id=call_id, execution_id="e", status="completed", output=None, trust="authorized"
        )


@pytest.mark.parametrize("kind", ["ollama", "compatible"])
async def test_router_budget_still_applies_to_tool_response(kind):
    def handle(req):
        if req.method == "GET":
            return httpx.Response(200, json={"models": [{"name": "local-model"}]})
        return httpx.Response(200, json=body(kind))

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:11535/v1/", transport=httpx.MockTransport(handle)
    ) as client:
        with pytest.raises(BudgetExceededError):
            await router(provider(kind, client)).execute(
                request=request(),
                requirements=RoutingRequirements(),
                budget=TaskBudget(maximum_output_tokens=1),
            )


async def test_live_ollama_native_tool_request_through_production_router():
    """Real inference, explicitly opted in. Fixtures above prove mapping only."""
    model = os.environ.get("JARVIS_TEST_OLLAMA_TOOL_MODEL")
    if not model:
        pytest.skip("no real tool-capable Ollama model explicitly configured")
    adapter = OllamaProvider(
        default_model=model,
        capabilities=CAPABILITIES,
        execution_mode="local_only",
        timeout_seconds=120,
    )
    response = await router(adapter).execute(
        request=request(temperature=0, max_output_tokens=512, prefer_no_reasoning=True),
        requirements=RoutingRequirements(requested_provider=adapter.name),
        budget=TaskBudget(),
    )
    assert response.tool_requests, "real model returned no native tool request"
    assert response.tool_requests[0].name == "workspace_read"
    assert response.tool_requests[0].arguments == {"path": "inputs/brief.txt"}
    with pytest.raises(ValidationError):
        AuthorizeToolExecutionRequest.model_validate(response.tool_requests[0].model_dump())
