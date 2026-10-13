from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.db.session import create_database_engine, create_session_factory
from app.model_providers.budget import TaskBudget
from app.model_providers.chatgpt_auth import (
    ChatGPTSession,
    PendingSignIn,
    load_session,
    save_session,
)
from app.model_providers.codex_transport import require_safe_config
from app.model_providers.contracts import ModelExecutionRequest
from app.model_providers.errors import (
    AuthenticationError,
    MalformedProviderResponseError,
    ProviderExecutionDisabledError,
    QuotaExhaustedError,
)
from app.model_providers.factory import build_provider_registry
from app.model_providers.free_capacity import FreeCapacityRepository
from app.model_providers.free_endpoints import compatible_endpoint
from app.model_providers.openai_compatible import HealthCheckStrategy, OpenAICompatibleProvider
from app.model_providers.registry import ProviderRegistry
from app.model_providers.retry import RetryExecutor, RetryPolicy
from app.model_providers.router import ModelRouter, RoutingRequirements
from app.model_providers.subscription import ChatGPTPlanProvider, CodexProvider
from app.models.ai_capacity import FreeAllowance


def allowance(provider="groq", **updates) -> FreeAllowance:
    now = datetime.now(UTC)
    return FreeAllowance.model_validate(
        {
            "provider": provider,
            "integration": "groq",
            "account_id": "verified-account",
            "shared_bucket": "account-free",
            "models": ["model"],
            "credential_env": "TEST_FREE_KEY",
            "credential_fingerprint": sha256(b"test-key").hexdigest(),
            "evidence_reference": "operator-test-evidence",
            "verified_at": now - timedelta(minutes=1),
            "valid_until": now + timedelta(hours=1),
            "window_start": now - timedelta(hours=1),
            "resets_at": now + timedelta(hours=1),
            "remaining_requests": 5,
            "nonbillable": True,
            "billing_enabled": False,
            "automatic_recharge": False,
            "credit_consumption": False,
            **updates,
        }
    )


@pytest.fixture
def capacity(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'capacity.db').as_posix()}"
    monkeypatch.setenv("JARVIS_DATABASE_URL", url)
    command.upgrade(Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head")
    engine = create_database_engine(url)
    repository = FreeCapacityRepository(create_session_factory(engine))
    yield repository
    engine.dispose()


def provider(grant, capacity, handler):
    return OpenAICompatibleProvider(
        name=grant.provider,
        base_url=compatible_endpoint(grant),
        api_key=SecretStr("test-key"),
        default_model=sorted(grant.models)[0],
        execution_mode="free_only",
        health_strategy=HealthCheckStrategy.CONFIGURATION,
        free_allowance=grant,
        free_capacity=capacity,
        client=httpx.AsyncClient(
            base_url=compatible_endpoint(grant), transport=httpx.MockTransport(handler)
        ),
    )


def completion(request):
    body = json.loads(request.content)
    return httpx.Response(
        200,
        json={
            "model": body["model"],
            "choices": [
                {"message": {"role": "assistant", "content": "answer"}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        },
    )


@pytest.mark.parametrize("mode", ["disabled", "free_only"])
def test_factory_requires_mode_and_credentials_even_with_allowance(capacity, monkeypatch, mode):
    grant = allowance()
    monkeypatch.delenv(grant.credential_env, raising=False)
    settings = Settings(
        _env_file=None,
        JARVIS_MODEL_EXECUTION_MODE=mode,
        JARVIS_MODEL_FREE_ALLOWANCES_JSON=json.dumps([grant.model_dump(mode="json")]),
    )
    assert build_provider_registry(settings, capacity.sessions).list() == []


@pytest.mark.parametrize("leak", ["content", "header", "model"])
async def test_hosted_completion_rejects_credential_echo_and_unverified_model(capacity, leak):
    grant = allowance()
    capacity.configure([grant])

    def unsafe(request):
        response = completion(request)
        body = response.json()
        headers = {}
        if leak == "content":
            body["choices"][0]["message"]["content"] = "test-key"
        elif leak == "header":
            headers["x-request-id"] = "test-key"
        else:
            body["model"] = "unverified-model"
        return httpx.Response(200, json=body, headers=headers)

    adapter = provider(grant, capacity, unsafe)
    try:
        with pytest.raises(MalformedProviderResponseError) as raised:
            await adapter.execute(ModelExecutionRequest(prompt="test"))
        assert "test-key" not in str(raised.value)
        assert capacity.snapshot()[0]["remaining_requests"] == 4
    finally:
        await adapter._client.aclose()


@pytest.mark.parametrize("field", ["billing_enabled", "automatic_recharge", "credit_consumption"])
def test_billable_or_credit_boundaries_are_rejected(field):
    with pytest.raises(ValidationError):
        allowance(**{field: True})


def test_missing_quota_and_zero_prices_never_authorize_cloud(monkeypatch):
    value = allowance().model_dump(mode="json")
    del value["remaining_requests"]
    with pytest.raises(ValidationError):
        FreeAllowance.model_validate(value)
    monkeypatch.setenv("TEST_FREE_KEY", "test-key")
    settings = Settings(
        _env_file=None,
        JARVIS_MODEL_EXECUTION_MODE="free_only",
        JARVIS_MODEL_PRICING_JSON='{"groq":{"model":{"input_per_million_usd":0,"output_per_million_usd":0}}}',
    )
    assert build_provider_registry(settings).list() == []


def test_unknown_execution_modes_and_paid_openrouter_models_are_rejected():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, JARVIS_MODEL_EXECUTION_MODE="paid_fallback")
    with pytest.raises(ValidationError):
        allowance(integration="openrouter", models=["paid-model"])


def test_configuration_prohibits_account_rotation():
    first = allowance("one")
    second = allowance("two", account_id="another-account")
    with pytest.raises(ValidationError, match="account rotation"):
        Settings(
            _env_file=None,
            JARVIS_MODEL_FREE_ALLOWANCES_JSON=json.dumps(
                [first.model_dump(mode="json"), second.model_dump(mode="json")]
            ),
        )


def test_shared_models_and_integrations_do_not_refill_a_bucket(capacity):
    first = allowance(remaining_requests=2)
    second = first.model_copy(update={"provider": "other", "models": frozenset({"other-model"})})
    capacity.configure([first, second])
    capacity.reserve(first, "model")
    capacity.reserve(second, "other-model")
    capacity.configure([first, second])
    assert not capacity.available(first, "model")
    assert not capacity.available(second, "other-model")
    with pytest.raises(QuotaExhaustedError):
        capacity.reserve(first, "model")


def test_concurrent_reservations_cannot_oversubscribe(capacity):
    grant = allowance(remaining_requests=3)
    capacity.configure([grant])

    def attempt(_):
        try:
            FreeCapacityRepository(capacity.sessions).reserve(grant, "model")
            return 1
        except QuotaExhaustedError:
            return 0

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(20))) == 3


def test_concurrent_startup_and_reload_never_refund_reservations(capacity):
    grant = allowance(remaining_requests=7)

    def start_and_reserve(_):
        repository = FreeCapacityRepository(capacity.sessions)
        repository.configure([grant])
        try:
            repository.reserve(grant, "model")
            return 1
        except QuotaExhaustedError:
            return 0

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(start_and_reserve, range(30))) == 7
    assert capacity.snapshot()[0]["remaining_requests"] == 0


def test_shifted_window_cannot_refill_before_verified_reset(capacity):
    grant = allowance()
    capacity.configure([grant])
    capacity.exhaust(grant)
    shifted = grant.model_copy(
        update={
            "window_start": datetime.now(UTC) - timedelta(minutes=1),
            "verified_at": datetime.now(UTC),
        }
    )
    capacity.configure([shifted])
    assert not capacity.available(shifted, "model")
    assert capacity.snapshot()[0]["remaining_requests"] == 0


def test_verified_actual_reset_allows_a_new_window(capacity):
    now = datetime.now(UTC)
    expired = allowance(
        window_start=now - timedelta(hours=2),
        resets_at=now - timedelta(seconds=1),
        verified_at=now - timedelta(minutes=1),
    )
    capacity.configure([expired])
    capacity.exhaust(expired)
    renewed = allowance(window_start=expired.resets_at, verified_at=now)
    capacity.configure([renewed])
    assert capacity.available(renewed, "model")


def test_expiry_and_unverified_models_block_before_reservation(capacity):
    grant = allowance()
    capacity.configure([grant])
    with pytest.raises(ProviderExecutionDisabledError):
        capacity.reserve(grant, "unknown")
    with pytest.raises(ProviderExecutionDisabledError):
        capacity.reserve(
            grant.model_copy(update={"valid_until": datetime.now(UTC) - timedelta(seconds=1)}),
            "model",
        )
    assert capacity.snapshot()[0]["remaining_requests"] == 5


def test_shared_evidence_must_agree(capacity):
    grant = allowance()
    with pytest.raises(ValueError):
        capacity.configure(
            [grant, grant.model_copy(update={"provider": "other", "remaining_requests": 50})]
        )


@pytest.mark.parametrize("integration", ["gemini", "groq", "openrouter", "cloudflare"])
async def test_compatible_free_integrations_complete_mock_requests(capacity, integration):
    grant = allowance(
        integration=integration,
        account_id="a" * 32 if integration == "cloudflare" else "verified-account",
        models=["model:free"] if integration == "openrouter" else ["model"],
    )
    capacity.configure([grant])
    adapter = provider(grant, capacity, completion)
    response = await adapter.execute(ModelExecutionRequest(prompt="test"))
    assert response.content == "answer"
    assert capacity.snapshot()[0]["remaining_requests"] == 4
    await adapter._client.aclose()


async def test_hard_quota_switches_accounts_and_persists_all_exhaustion(capacity):
    first = allowance("first")
    same = first.model_copy(update={"provider": "same-account"})
    second = allowance("second", integration="gemini", account_id="second-account")
    capacity.configure([first, same, second])
    calls = []

    def exhausted(request):
        calls.append(request.url.host)
        return httpx.Response(429, json={"error": {"code": "insufficient_quota"}})

    adapters = [
        provider(first, capacity, exhausted),
        provider(same, capacity, completion),
        provider(second, capacity, completion),
    ]
    router = ModelRouter(ProviderRegistry(adapters), RetryExecutor(RetryPolicy(maximum_attempts=1)))
    requirements = RoutingRequirements(
        allow_remote=True,
        prefer_local=False,
        allow_fallback=True,
        allow_quota_fallback=True,
        maximum_fallbacks=2,
    )
    dispatches = []

    async def dispatch(provider_name, model):
        dispatches.append("persisted-dispatch")

    response = await router.execute(
        request=ModelExecutionRequest(prompt="test"),
        requirements=requirements,
        budget=TaskBudget(maximum_requests=2),
        before_dispatch=dispatch,
    )
    assert response.provider == "second"
    assert len(calls) == 1
    assert not capacity.available(same, "model")
    assert len(dispatches) == 2  # The stale shared-account candidate consumes no dispatch.
    capacity.exhaust(second)
    restarted = FreeCapacityRepository(capacity.sessions)
    restarted.configure([first, same, second])
    with pytest.raises(QuotaExhaustedError):
        await router.execute(
            request=ModelExecutionRequest(prompt="test"),
            requirements=requirements,
            budget=TaskBudget(maximum_requests=3),
        )
    for adapter in adapters:
        await adapter._client.aclose()


async def test_unverified_or_wrong_endpoint_never_creates_client(capacity):
    with pytest.raises(Exception, match="allowance does not match"):
        OpenAICompatibleProvider(
            name="groq",
            base_url="https://api.openai.com/v1",
            api_key=SecretStr("test-key"),
            default_model="model",
            execution_mode="free_only",
            free_allowance=allowance(),
            free_capacity=capacity,
        )
    adapter = OpenAICompatibleProvider(
        name="paid",
        base_url="https://api.openai.com/v1",
        api_key=SecretStr("test-key"),
        default_model="model",
        execution_mode="free_only",
    )
    with pytest.raises(ProviderExecutionDisabledError):
        await adapter.execute(ModelExecutionRequest(prompt="test"))
    assert adapter._client is None


def chatgpt_session(**updates):
    return ChatGPTSession(
        subject="verified-account",
        client_id="oaiapp_test",
        host_id="host",
        scopes=frozenset({"resource.invoke", "chatgpt.tokens.use.direct"}),
        expires_at=time.time() + 3600,
        access_token="oauth-access",
        refresh_token="oauth-refresh",
        id_token="signed-id",
        **updates,
    )


async def test_chatgpt_plan_responses_use_oauth_and_require_completed_event(capacity):
    grant = allowance(integration="chatgpt")
    capacity.configure([grant])
    calls = []

    def stream(request):
        calls.append(request)
        payload = json.loads(request.content)
        assert payload["store"] is False and payload["stream"] is True
        assert "max_output_tokens" not in payload and "temperature" not in payload
        assert request.headers["authorization"] == "Bearer oauth-access"
        event = {
            "type": "response.completed",
            "response": {
                "status": "completed",
                "model": "model",
                "output": [
                    {"type": "message", "content": [{"type": "output_text", "text": "answer"}]}
                ],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        }
        return httpx.Response(200, text="data: " + json.dumps(event) + "\n\n")

    async with httpx.AsyncClient(transport=httpx.MockTransport(stream)) as client:
        adapter = ChatGPTPlanProvider(grant, capacity, chatgpt_session(), client=client)
        assert (
            await adapter.execute(ModelExecutionRequest(prompt="test", max_output_tokens=20))
        ).content == "answer"
        assert str(calls[0].url) == "https://api.openai.com/v1/responses"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text="data: {}\n\n"))
    ) as client:
        adapter = ChatGPTPlanProvider(grant, capacity, chatgpt_session(), client=client)
        with pytest.raises(MalformedProviderResponseError):
            await adapter.execute(ModelExecutionRequest(prompt="test"))


def test_identity_without_plan_scope_cannot_infer(capacity):
    session = chatgpt_session()
    session.scopes = frozenset({"openid"})
    with pytest.raises(AuthenticationError):
        ChatGPTPlanProvider(allowance(integration="chatgpt"), capacity, session)


async def test_subscription_stream_quota_failure_disables_shared_bucket(capacity):
    grant = allowance(integration="chatgpt")
    capacity.configure([grant])
    event = {"type": "response.failed", "response": {"error": {"code": "insufficient_quota"}}}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, text="data: " + json.dumps(event) + "\n\n")
        )
    ) as client:
        adapter = ChatGPTPlanProvider(grant, capacity, chatgpt_session(), client=client)
        with pytest.raises(QuotaExhaustedError):
            await adapter.execute(ModelExecutionRequest(prompt="test"))
    assert not capacity.available(grant, "model")


def test_credentials_roundtrip_in_protected_storage(tmp_path):
    path = tmp_path / "session.bin"
    session = chatgpt_session()
    save_session(path, session)
    assert load_session(path).access_token == session.access_token
    assert "oauth-access" not in repr(session)
    import os

    if os.name == "nt":
        assert b"oauth-access" not in path.read_bytes()


async def test_oauth_bad_state_and_replay_never_exchange_tokens():
    pending = PendingSignIn("host", "http://127.0.0.1:1455/auth/callback")
    calls = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: calls.append(r))
    ) as client:
        with pytest.raises(AuthenticationError):
            await pending.complete({"state": "bad", "code": "code"}, client)
        with pytest.raises(AuthenticationError):
            await pending.complete({"state": pending.state, "error": "access_denied"}, client)
        with pytest.raises(AuthenticationError):
            await pending.complete(
                {"state": pending.state, "code": "code", "client_id": "oaiapp_test"}, client
            )
    assert not calls


async def test_codex_included_subscription_path_with_mock_transport(capacity):
    grant = allowance(integration="codex")
    capacity.configure([grant])

    class Transport:
        async def infer(self, request, verified_grant):
            assert verified_grant.credit_consumption is False
            return "included response"

    adapter = CodexProvider(grant, capacity, transport=Transport())
    assert (
        await adapter.execute(ModelExecutionRequest(prompt="test"))
    ).content == "included response"
    assert not adapter.is_local


def test_codex_inherited_tools_and_paid_providers_are_rejected():
    safe = {
        "features": {
            "shell_tool": False,
            "unified_exec": False,
            "code_mode": False,
            "apply_patch_freeform": False,
            "shell_snapshot": False,
            "view_image": False,
            "code_mode_host": False,
            "hooks": False,
        },
        "apps": {"_default": {"enabled": False}},
    }
    require_safe_config(safe)
    for extra in [
        {"mcp_servers": {"external": {}}},
        {"hooks": ["command"]},
        {"plugins": {"plugin": {}}},
        {"model_provider": "paid"},
    ]:
        with pytest.raises(ProviderExecutionDisabledError):
            require_safe_config({**safe, **extra})


async def test_no_verified_capacity_pauses_real_worker_with_recovery_checkpoint(tmp_path):
    from tests.test_autonomous_worker import FakeRouter, worker_fixture

    app, client, _, worker = worker_fixture(
        tmp_path, router=FakeRouter([]), run_id="run-free-pause"
    )
    try:
        service = app.state.autonomous_worker_service
        service.settings.model_execution_mode = "free_only"
        service.router = ModelRouter(
            ProviderRegistry(), RetryExecutor(RetryPolicy(maximum_attempts=1))
        )
        result = await service.run_once(worker.id)
        assert result.failureCode == "free_capacity_unavailable"
        runtime = app.state.agent_runtime_service.repository.load_run("run-free-pause")
        assert runtime.state == "paused"
        assert result.requestCount == 0
        assert app.state.repository.tasks["task-demo"].status == "under_review"
        assert runtime.latest_checkpoint_id is not None
    finally:
        client.__exit__(None, None, None)


@pytest.mark.parametrize("invalid", ["nonce", "aud", "signature", "scope"])
async def test_oauth_cryptographically_rejects_untrusted_identity_and_permissions(invalid):
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    public["kid"] = "test-key-id"
    pending = PendingSignIn("host", "http://127.0.0.1:1455/auth/callback")
    claims = {
        "iss": "https://auth.openai.com",
        "aud": "oaiapp_test",
        "sub": "verified-account",
        "exp": int(time.time()) + 3600,
        "nonce": pending.nonce,
    }
    if invalid in {"nonce", "aud"}:
        claims[invalid] = "untrusted"
    signing = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        if invalid == "signature"
        else key
    )
    signed = jwt.encode(claims, signing, algorithm="RS256", headers={"kid": "test-key-id"})

    def exchange(request):
        if request.url.path.endswith("/oauth/token"):
            return httpx.Response(
                200,
                json={
                    "id_token": signed,
                    "access_token": "private-access",
                    "refresh_token": "private-refresh",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "scope": "openid"
                    if invalid == "scope"
                    else "resource.invoke chatgpt.tokens.use.direct",
                },
            )
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(
                200, json={"jwks_uri": "https://auth.openai.com/.well-known/jwks.json"}
            )
        return httpx.Response(200, json={"keys": [public]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(exchange)) as client:
        with pytest.raises(AuthenticationError) as raised:
            await pending.complete(
                {"code": "authorization", "state": pending.state, "client_id": "oaiapp_test"},
                client,
            )
        assert "private-access" not in str(raised.value)


@pytest.mark.parametrize("credit_status", ["absent", "unknown", "paid", "unlimited", "safe"])
async def test_native_codex_rpc_checks_subscription_then_collects_final_usage(credit_status):
    from app.model_providers.codex_transport import CodexStdioTransport

    messages = [
        {"id": 1, "result": {}},
        {
            "id": 2,
            "result": {
                "config": {
                    "features": {
                        "shell_tool": False,
                        "unified_exec": False,
                        "code_mode": False,
                        "apply_patch_freeform": False,
                        "shell_snapshot": False,
                        "view_image": False,
                        "code_mode_host": False,
                        "hooks": False,
                    },
                    "apps": {"_default": {"enabled": False}},
                }
            },
        },
        {
            "id": 3,
            "result": {
                "account": {"type": "chatgpt", "email": "verified-account", "planType": "plus"}
            },
        },
        {
            "id": 4,
            "result": {
                "rateLimitsByLimitId": {
                    "account-free": {
                        "limitId": "account-free",
                        "primary": {"usedPercent": 5},
                        **(
                            {}
                            if credit_status == "absent"
                            else {
                                "credits": {
                                    "hasCredits": None
                                    if credit_status == "unknown"
                                    else credit_status == "paid",
                                    "unlimited": credit_status == "unlimited",
                                }
                            }
                        ),
                    }
                }
            },
        },
        {"id": 5, "result": {"thread": {"id": "thread"}}},
        {"id": 6, "result": {"turn": {"id": "turn"}}},
        {
            "method": "item/agentMessage/delta",
            "params": {"threadId": "thread", "turnId": "turn", "delta": "commentary"},
        },
        {
            "method": "item/completed",
            "params": {
                "threadId": "thread",
                "turnId": "turn",
                "item": {"type": "agentMessage", "phase": "final_answer", "text": "final"},
            },
        },
        {
            "method": "thread/tokenUsage/updated",
            "params": {
                "threadId": "thread",
                "tokenUsage": {"last": {"inputTokens": 10, "outputTokens": 20}},
            },
        },
        {
            "method": "turn/completed",
            "params": {"threadId": "thread", "turn": {"status": "completed"}},
        },
    ]
    sent = []

    class Input:
        def write(self, data):
            sent.append(json.loads(data))

        async def drain(self):
            pass

    class Output:
        async def readline(self):
            return (json.dumps(messages.pop(0)) + "\n").encode()

    class Process:
        stdin = Input()
        stdout = Output()

    if credit_status != "safe":
        with pytest.raises(ProviderExecutionDisabledError, match="credit status"):
            await CodexStdioTransport()._conversation(
                Process(),
                ModelExecutionRequest(prompt="test"),
                allowance(integration="codex"),
                "/tmp/isolated",
            )
        assert "turn/start" not in [message["method"] for message in sent]
        return
    result = await CodexStdioTransport()._conversation(
        Process(),
        ModelExecutionRequest(prompt="test"),
        allowance(integration="codex"),
        "/tmp/isolated",
    )
    assert result == {"content": "final", "input_tokens": 10, "output_tokens": 20}
    assert [message["method"] for message in sent] == [
        "initialize",
        "initialized",
        "config/read",
        "account/read",
        "account/rateLimits/read",
        "thread/start",
        "turn/start",
    ]


async def test_real_worker_switches_free_providers_without_local_model(tmp_path):
    from tests.test_autonomous_worker import VALID_RESULT, FakeRouter, worker_fixture

    app, client, _, worker = worker_fixture(
        tmp_path, router=FakeRouter([]), run_id="run-free-switch"
    )
    adapters = []
    try:
        service = app.state.autonomous_worker_service
        service.settings.model_execution_mode = "free_only"
        first = allowance("local-fake", integration="groq", models=["fixture-model"])
        second = allowance(
            "second", integration="gemini", account_id="second-account", models=["fixture-model"]
        )
        repository = FreeCapacityRepository(service.executions.sessions)
        repository.configure([first, second])

        def plan(request):
            return httpx.Response(
                200,
                json={
                    "model": "fixture-model",
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": json.dumps(VALID_RESULT)},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 20},
                },
            )

        adapters = [
            provider(
                first,
                repository,
                lambda _: httpx.Response(429, json={"error": {"code": "insufficient_quota"}}),
            ),
            provider(second, repository, plan),
        ]
        service.router = ModelRouter(
            ProviderRegistry(adapters), RetryExecutor(RetryPolicy(maximum_attempts=1))
        )
        result = await service.run_once(worker.id)
        assert result.stage == "completed"
        assert result.requestCount == 2
        assert result.provider == "second"
        assert not repository.available(first, "fixture-model")
        assert (
            app.state.agent_runtime_service.repository.load_run("run-free-switch").state
            == "succeeded"
        )
        assert all(not adapter.is_local for adapter in adapters)
    finally:
        for adapter in adapters:
            await adapter._client.aclose()
        client.__exit__(None, None, None)


async def test_all_failed_dispatches_keep_distinct_durable_evidence(tmp_path):
    from sqlalchemy import select

    from app.db.models import OutboxEventRow
    from tests.test_autonomous_worker import FakeRouter, worker_fixture

    app, client, _, worker = worker_fixture(
        tmp_path, router=FakeRouter([]), run_id="run-free-failures"
    )
    adapters = []
    try:
        service = app.state.autonomous_worker_service
        service.settings.model_execution_mode = "free_only"
        first = allowance("local-fake", models=["fixture-model"])
        second = allowance(
            "second", integration="gemini", account_id="second-account", models=["fixture-model"]
        )
        repository = FreeCapacityRepository(service.executions.sessions)
        repository.configure([first, second])
        adapters = [
            provider(
                grant,
                repository,
                lambda _: httpx.Response(429, json={"error": {"code": "insufficient_quota"}}),
            )
            for grant in (first, second)
        ]
        service.router = ModelRouter(
            ProviderRegistry(adapters), RetryExecutor(RetryPolicy(maximum_attempts=1))
        )
        result = await service.run_once(worker.id)
        assert result.failureCode == "free_capacity_unavailable" and result.requestCount == 2
        with service.executions.sessions() as session:
            failures = list(
                session.scalars(
                    select(OutboxEventRow)
                    .where(OutboxEventRow.event_type == "model.call.failed")
                    .order_by(OutboxEventRow.sequence_number)
                )
            )
            assert [
                (row.envelope["payload"]["provider"], row.envelope["payload"]["requestCount"])
                for row in failures
            ] == [("local-fake", 1), ("second", 2)]
            assert all(
                row.envelope["payload"]["failureCategory"] == "hard_quota_exhausted"
                for row in failures
            )
    finally:
        for adapter in adapters:
            await adapter._client.aclose()
        client.__exit__(None, None, None)
