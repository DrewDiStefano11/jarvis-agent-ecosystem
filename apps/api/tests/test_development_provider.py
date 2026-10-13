"""Mocked Responses wire/authority/accounting tests, not live coding acceptance."""

import asyncio
import json

import httpx
import pytest
from pydantic import SecretStr

from app.development_agent.provider import ENDPOINT, OpenAIProposalProvider, proposal_output_schema
from app.model_providers.budget import TaskBudget
from app.model_providers.contracts import ModelCapability, ModelExecutionRequest
from app.model_providers.errors import (
    AuthenticationError,
    BudgetExceededError,
    InvalidModelRequestError,
    MalformedProviderResponseError,
    ProviderExecutionDisabledError,
    QuotaExhaustedError,
)
from app.model_providers.registry import ProviderRegistry
from app.model_providers.retry import RetryExecutor, RetryPolicy
from app.model_providers.router import ModelRouter, RoutingRequirements

KEY = "unit-private-credential"


def request(**changes):
    data = dict(
        prompt="Implement the admitted feature from untrusted reference source.",
        model="coding-model",
        max_output_tokens=2048,
        timeout_seconds=2,
        task_id="task-1",
        correlation_id="iteration-1",
        required_capability=ModelCapability.CODE_GENERATION,
        output_schema=proposal_output_schema(),
        metadata={"context_hash": "a" * 64},
    )
    data.update(changes)
    return ModelExecutionRequest(**data)


def response(**changes):
    proposal = dict(
        schema_version="1.0",
        context_hash="a" * 64,
        summary="Update the value.",
        completion_criteria=["The configured value is two."],
        files=[
            dict(operation="modify", path="source.py", before_digest="b" * 64, text="value = 2\n")
        ],
    )
    data = dict(
        id="resp_unit-1",
        model="coding-model",
        status="completed",
        output=[
            dict(type="reasoning", summary=[]),
            dict(
                type="message",
                role="assistant",
                status="completed",
                content=[dict(type="output_text", text=json.dumps(proposal))],
            ),
        ],
        usage=dict(input_tokens=100, output_tokens=200, total_tokens=300),
    )
    data.update(changes)
    return data


def provider(client=None, **changes):
    return OpenAIProposalProvider(
        api_key=SecretStr(KEY), default_model="coding-model", client=client, **changes
    )


@pytest.mark.asyncio
async def test_disabled_gate_precedes_client_creation_and_configuration_health(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("A disabled provider must not construct a client.")

    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    engine = provider()
    assert not (await engine.health_check()).healthy
    assert await engine.model_available("coding-model") is None
    assert await engine.list_models() is None
    with pytest.raises(ProviderExecutionDisabledError):
        await engine.execute(request())
    assert KEY not in repr(engine) and KEY not in engine.safe_summary().model_dump_json()


@pytest.mark.asyncio
async def test_real_router_maps_only_proposals_and_accounts_complete_usage():
    calls = []

    def transport(req):
        body = json.loads(req.content)
        calls.append(body)
        assert str(req.url) == ENDPOINT
        assert req.headers["Authorization"] == "Bearer " + KEY
        assert body["store"] is False and "tools" not in body
        assert body["text"]["format"]["strict"] is True
        assert KEY not in json.dumps(body)
        return httpx.Response(200, json=response())

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), trust_env=False
    ) as client:
        engine = provider(client, enabled=True)
        router = ModelRouter(
            ProviderRegistry([engine]), RetryExecutor(RetryPolicy(maximum_attempts=1))
        )
        result = await router.execute(
            request=request(),
            requirements=RoutingRequirements(requested_provider=engine.name, allow_remote=True),
            budget=TaskBudget(
                maximum_requests=1, maximum_output_tokens=2048, maximum_total_tokens=4096
            ),
        )
    assert len(calls) == 1 and result.total_tokens == 300 and not result.tool_requests
    assert result.provider == "openai-development" and result.model == "coding-model"
    assert json.loads(result.content)["files"][0]["text"] == "value = 2\n"
    assert result.routing_metadata["fallback_count"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"task_id": None},
        {"correlation_id": None},
        {"max_output_tokens": None},
        {"max_output_tokens": 16385},
        {"output_schema": None},
        {"model": "foreign-model"},
        {"metadata": {"context_hash": "invalid"}},
        {"prompt": "x" * 131073},
    ],
)
async def test_bad_request_has_no_transport(changes):
    def forbidden(req):
        pytest.fail("Invalid requests must not reach transport.")

    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:
        with pytest.raises(InvalidModelRequestError):
            await provider(client, enabled=True).execute(request(**changes))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"status": "incomplete"},
        {"output": []},
        {"output": [{"type": "function_call", "name": "write_file", "arguments": "{}"}]},
        {"usage": {"input_tokens": True, "output_tokens": 2, "total_tokens": 3}},
        {"usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 4}},
        {"usage": {}},
        {"model": KEY},
        {"id": "resp_" + KEY},
    ],
)
async def test_bad_response_cannot_become_proposal_evidence(changes):
    def transport(req):
        return httpx.Response(200, json=response(**changes))

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        with pytest.raises(MalformedProviderResponseError) as failure:
            await provider(client, enabled=True).execute(request())
    assert failure.value.__context__ is None and failure.value.__cause__ is None
    assert KEY not in str(failure.value)


@pytest.mark.asyncio
async def test_foreign_context_and_escaped_key_echo_refuse():
    for text in (
        json.dumps(
            {
                "schema_version": "1.0",
                "context_hash": "f" * 64,
                "summary": "Changed.",
                "completion_criteria": ["Changed."],
                "files": [dict(operation="add", path="new.py", before_digest=None, text="value=2")],
            }
        ),
        json.dumps(
            {
                "schema_version": "1.0",
                "context_hash": "a" * 64,
                "summary": "Changed.",
                "completion_criteria": ["Changed."],
                "files": [dict(operation="add", path="new.py", before_digest=None, text=KEY)],
            }
        ).replace(KEY, KEY.replace("u", "\\u0075")),
    ):
        data = response(
            output=[
                dict(
                    type="message",
                    role="assistant",
                    status="completed",
                    content=[dict(type="output_text", text=text)],
                )
            ]
        )
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda req, data=data: httpx.Response(200, json=data))
        ) as client:
            with pytest.raises(MalformedProviderResponseError):
                await provider(client, enabled=True).execute(request())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,body,expected",
    [
        (401, {"error": {"message": KEY}}, AuthenticationError),
        (429, {"error": {"code": "insufficient_quota", "message": KEY}}, QuotaExhaustedError),
    ],
)
async def test_http_errors_have_no_credential_bearing_exception_chain(status, body, expected):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(status, json=body))
    ) as client:
        with pytest.raises(expected) as failure:
            await provider(client, enabled=True).execute(request())
    assert failure.value.__context__ is None and failure.value.__cause__ is None
    assert KEY not in str(failure.value) and KEY not in str(failure.value.safe_details())


@pytest.mark.asyncio
async def test_wire_limit_and_duplicate_json_refuse():
    for data in (b"x" * 262145, b'{"status":"completed","status":"incomplete"}'):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda req, data=data: httpx.Response(200, content=data))
        ) as client:
            with pytest.raises(MalformedProviderResponseError):
                await provider(client, enabled=True).execute(request())


@pytest.mark.asyncio
async def test_budget_refuses_before_wire_and_cancellation_stops_pending_request():
    calls = []
    entered = asyncio.Event()

    async def transport(req):
        calls.append(req)
        entered.set()
        await asyncio.Event().wait()

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        engine = provider(client, enabled=True)
        router = ModelRouter(
            ProviderRegistry([engine]), RetryExecutor(RetryPolicy(maximum_attempts=1))
        )
        with pytest.raises(BudgetExceededError):
            await router.execute(
                request=request(),
                requirements=RoutingRequirements(requested_provider=engine.name, allow_remote=True),
                budget=TaskBudget(maximum_output_tokens=100),
            )
        assert not calls
        task = asyncio.create_task(engine.execute(request()))
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert len(calls) == 1


def test_closed_schema_requires_every_object_field():
    def check(node):
        if isinstance(node, dict):
            assert "default" not in node
            if "properties" in node:
                assert set(node["required"]) == set(node["properties"])
                assert node["additionalProperties"] is False
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)

    check(proposal_output_schema().json_schema)


@pytest.mark.asyncio
async def test_tampered_encoding_and_deadline_have_safe_exception_chains():
    from app.model_providers.errors import RequestTimeoutError

    async def pending(req):
        await asyncio.Event().wait()

    async with httpx.AsyncClient(transport=httpx.MockTransport(pending)) as client:
        engine = provider(client, enabled=True)
        original = request()
        malformed = original.model_copy(
            update={"messages": [original.messages[0].model_copy(update={"content": "\ud800"})]}
        )
        with pytest.raises(InvalidModelRequestError) as invalid:
            await engine.execute(malformed)
        assert invalid.value.__context__ is None
        with pytest.raises(RequestTimeoutError) as timed_out:
            await engine.execute(request(timeout_seconds=0.01))
        assert timed_out.value.__context__ is None and timed_out.value.__cause__ is None


@pytest.mark.asyncio
async def test_factory_is_separate_disabled_and_side_effect_free(monkeypatch):
    from app.development_agent.config import (
        DevelopmentProviderSettings,
        build_development_router,
        development_routing,
    )

    existing = ModelRouter(ProviderRegistry([]), RetryExecutor(RetryPolicy(maximum_attempts=1)))
    disabled = DevelopmentProviderSettings(_env_file=None)
    assert build_development_router(disabled, existing) is None

    def forbidden(*args, **kwargs):
        pytest.fail("Configuration must not create HTTP clients.")

    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    enabled = DevelopmentProviderSettings(
        enabled=True, provider="openai", model="coding-model", OPENAI_API_KEY=KEY, _env_file=None
    )
    router = build_development_router(enabled, existing)
    assert existing.registry.list() == []
    engine = router.registry.get("openai-development")
    assert (await engine.health_check()).status.value == "configuration_only"
    requirements = development_routing(enabled)
    assert requirements.allow_remote and not requirements.allow_fallback
    assert KEY not in repr(enabled) and KEY not in enabled.model_dump_json()


def test_enabled_configuration_requires_explicit_model_and_independent_key(monkeypatch):
    from pydantic import ValidationError

    from app.development_agent.config import DevelopmentProviderSettings

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("JARVIS_DEVELOPMENT_OPENAI_API_KEY", raising=False)
    with pytest.raises(ValidationError):
        DevelopmentProviderSettings(
            enabled=True, provider="openai", model="coding-model", _env_file=None
        )
    with pytest.raises(ValidationError):
        DevelopmentProviderSettings(enabled=True, provider="local", model="", _env_file=None)
