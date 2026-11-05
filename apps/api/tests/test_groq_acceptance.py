"""Native approval/one-dispatch tests; every provider response is intercepted."""

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import httpx
import pytest

from app.core.errors import DomainError
from tests.test_provider_configuration import SECRET, draft

pytest_plugins = ["tests.test_provider_configuration"]


def test_preparation_refuses_source_changed_since_service_initialization(acceptance, monkeypatch):
    import app.provider_configuration.groq_acceptance as implementation
    from app.models.groq_acceptance import PrepareGroqAcceptance

    _, actor, service, view = acceptance
    monkeypatch.setattr(implementation, "policy_digest", lambda: "d" * 64)
    with pytest.raises(DomainError):
        service.prepare(
            actor,
            PrepareGroqAcceptance(
                provider_name="groq", expected_revision=1, account_evidence=view.plan.evidence
            ),
        )


async def test_concurrent_execute_cannot_send_a_second_physical_request(acceptance):
    import asyncio

    _, actor, service, view = acceptance
    service.approve(actor, view.id, view.plan_hash)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def held(request):
        calls.append(True)
        entered.set()
        await release.wait()
        raise httpx.ReadTimeout("intercepted uncertain outcome")

    async with httpx.AsyncClient(transport=httpx.MockTransport(held)) as client:
        first = asyncio.create_task(service.execute(actor, view.id, view.plan_hash, client=client))
        await asyncio.wait_for(entered.wait(), 5)
        second = await service.execute(actor, view.id, view.plan_hash, client=client)
        assert second.status == "dispatch_started" and calls == [True]
        release.set()
        assert (await first).status == "uncertain"
        assert (
            await service.execute(actor, view.id, view.plan_hash, client=client)
        ).status == "uncertain"
    assert calls == [True]


async def test_revoked_exact_approval_refuses_before_http(acceptance):
    from app.db.models import AgentPermissionAssignmentRow

    app, actor, service, view = acceptance
    approved = service.approve(actor, view.id, view.plan_hash)
    with app.state.task_leases._write() as session:
        grant = session.get(AgentPermissionAssignmentRow, approved.approval_grant_id)
        grant.revoked_at = datetime.now(UTC)
    calls = []

    def forbidden(request):
        calls.append(True)
        raise AssertionError("Revoked approval reached HTTP")

    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:
        with pytest.raises(DomainError) as rejected:
            await service.execute(actor, view.id, view.plan_hash, client=client)
    assert rejected.value.code == "GROQ_ACCEPTANCE_PERMISSION_DENIED" and not calls


async def test_durable_capacity_reservations_are_atomic_shared_and_do_not_activate(acceptance):
    from concurrent.futures import ThreadPoolExecutor

    from app.model_providers.groq_rate_limits import RateBlocked
    from app.models.ai_capacity import FreeAllowance
    from app.provider_configuration.groq_capacity import GroqCapacityStore

    app, actor, service, view = acceptance
    service.approve(actor, view.id, view.plan_hash)

    def response(request):
        return httpx.Response(
            200,
            headers={
                "x-ratelimit-limit-requests": "1000",
                "x-ratelimit-remaining-requests": "999",
                "x-ratelimit-reset-requests": "1m",
                "x-ratelimit-limit-tokens": "8000",
                "x-ratelimit-remaining-tokens": "7980",
                "x-ratelimit-reset-tokens": "1s",
            },
            json={
                "model": "vendor/model",
                "choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        result = await service.execute(actor, view.id, view.plan_hash, client=client)
    assert result.initialized_capacity
    now = datetime.now(UTC)
    grant = FreeAllowance(
        provider="groq",
        integration="groq",
        account_id="fixture-account",
        shared_bucket="fixture",
        models={"vendor/model"},
        credential_env="FIXTURE_GROQ_KEY",
        credential_fingerprint=view.plan.evidence.credential_fingerprint,
        evidence_reference="fixture://response-headers",
        verified_at=now,
        valid_until=now + timedelta(minutes=1),
        window_start=now,
        resets_at=now + timedelta(minutes=1),
        remaining_requests=999,
        nonbillable=True,
        billing_enabled=False,
        automatic_recharge=False,
        credit_consumption=False,
    )

    expired_window = grant.model_copy(
        update={
            "window_start": now - timedelta(minutes=2),
            "verified_at": now - timedelta(minutes=2),
            "resets_at": now - timedelta(seconds=1),
        }
    )
    store = GroqCapacityStore(app)
    account_only = FreeAllowance.model_validate(
        grant.model_dump(exclude={"window_start", "resets_at", "remaining_requests"})
    )
    assert store.available(account_only, "vendor/model", 1)
    from copy import deepcopy

    from app.provider_configuration.groq_repository import GroqRateStateRow, rate_scope

    scope = rate_scope(grant.account_id, "vendor/model")
    with store.repository.sessions() as session:
        row = session.get(GroqRateStateRow, scope)
        before_rate = {
            name: deepcopy(getattr(row, name))
            for name in ("payload", "revision", "private_digest", "last_audit_id")
        }
    assert store.available(expired_window, "vendor/model", 1)
    assert not store.available(
        grant.model_copy(update={"valid_until": now - timedelta(seconds=1)}), "vendor/model", 1
    )
    assert not store.available(
        grant.model_copy(update={"billing_enabled": True}), "vendor/model", 1
    )
    from pydantic import SecretStr

    from app.model_providers.contracts import ModelExecutionRequest
    from app.model_providers.openai_compatible import OpenAICompatibleProvider

    async with httpx.AsyncClient(
        base_url="https://api.groq.com/openai/v1/", transport=httpx.MockTransport(response)
    ) as client:
        adapter = OpenAICompatibleProvider(
            name="groq",
            base_url=str(client.base_url),
            api_key=SecretStr(SECRET),
            default_model="vendor/model",
            execution_mode="free_only",
            free_allowance=grant,
            groq_capacity=store,
            client=client,
        )
        ordinary = await adapter.execute(ModelExecutionRequest(prompt="ordinary"))
    assert ordinary.total_tokens == 20

    def reserve_one(index):
        store = GroqCapacityStore(app)
        alias = grant.model_copy(
            update={"provider": f"groq_{index}", "shared_bucket": f"alias_{index}"}
        )
        try:
            return store.reserve(alias, "vendor/model", f"dispatch-{index}", 2000)
        except RateBlocked:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        permits = list(pool.map(reserve_one, range(8)))
    assert sum(permit is not None for permit in permits) == 3
    store = GroqCapacityStore(app)
    assert not store.available(grant, "vendor/model", 2000)
    from app.model_providers.errors import ProviderExecutionDisabledError

    live = [True]
    reserve_before_revocation = store.reserve

    def revoke_during_reservation(*args):
        permit = reserve_before_revocation(*args)
        live[0] = False
        return permit

    store.reserve = revoke_during_reservation
    escaped = []

    def forbidden(request):
        escaped.append(True)
        raise AssertionError("A revoked profile reached physical HTTP")

    async with httpx.AsyncClient(
        base_url="https://api.groq.com/openai/v1/", transport=httpx.MockTransport(forbidden)
    ) as client:
        adapter = OpenAICompatibleProvider(
            name="groq",
            base_url=str(client.base_url),
            api_key=SecretStr(SECRET),
            default_model="vendor/model",
            execution_mode="free_only",
            free_allowance=grant,
            groq_capacity=store,
            client=client,
            configuration_guard=lambda: live[0],
        )
        with pytest.raises(ProviderExecutionDisabledError):
            await adapter.execute(ModelExecutionRequest(prompt="revocation", max_output_tokens=64))
    assert not escaped
    with app.state.task_leases._write() as session:
        row = session.get(GroqRateStateRow, scope)
        for name, value in before_rate.items():
            setattr(row, name, value)
    assert not GroqCapacityStore(app).available(grant, "vendor/model", 1)
    assert not app.state.settings.verified_cloud_execution_allowed
    assert not app.state.provider_configuration_service.list(actor)[0].enabled


def test_http_approval_requires_local_origin_and_exact_confirmation(acceptance):
    from fastapi.testclient import TestClient

    app, actor, _, view = acceptance
    path = f"/api/model-configuration/groq/acceptance/{view.id}/approve"
    headers = {"Origin": app.state.settings.web_origin, "X-Jarvis-Actor-Id": actor.actor_id}
    body = {
        "expected_plan_hash": view.plan_hash,
        "confirmation": "approve_exactly_one_groq_request",
    }
    with TestClient(app, raise_server_exceptions=False) as client:
        assert (
            client.post(
                path, json=body, headers={**headers, "Forwarded": "for=127.0.0.1"}
            ).status_code
            == 403
        )
        assert (
            client.post(
                path, json={"expected_plan_hash": view.plan_hash}, headers=headers
            ).status_code
            == 422
        )
        response = client.post(path, json=body, headers=headers)
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "approved"
    assert not app.state.settings.verified_cloud_execution_allowed


@pytest.mark.parametrize("content,completion", [('{"ok":1}', 10), ('{"ok":true}', 513)])
async def test_invalid_success_cannot_initialize_capacity(acceptance, content, completion):
    _, actor, service, view = acceptance
    service.approve(actor, view.id, view.plan_hash)

    def response(request):
        return httpx.Response(
            200,
            headers={
                "x-ratelimit-limit-requests": "1000",
                "x-ratelimit-remaining-requests": "999",
                "x-ratelimit-reset-requests": "1m",
                "x-ratelimit-limit-tokens": "8000",
                "x-ratelimit-remaining-tokens": "7000",
                "x-ratelimit-reset-tokens": "1s",
            },
            json={
                "model": "vendor/model",
                "choices": [{"finish_reason": "stop", "message": {"content": content}}],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": completion,
                    "total_tokens": 10 + completion,
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        result = await service.execute(actor, view.id, view.plan_hash, client=client)
    assert result.status == "failed" and not result.initialized_capacity
    assert result.result_code == "provider_response_invalid"


@pytest.fixture
def acceptance(profile_app):
    from app.models.groq_acceptance import GroqAccountEvidence, PrepareGroqAcceptance
    from app.provider_configuration.groq_acceptance import GroqAcceptanceService

    app, actor = profile_app
    app.state.provider_configuration_service.save(actor, draft())
    now = datetime.now(UTC)
    evidence = GroqAccountEvidence(
        account_id="fixture-account",
        model="vendor/model",
        credential_fingerprint=sha256(SECRET.encode()).hexdigest(),
        plan="Free",
        nonbillable=True,
        billing_enabled=False,
        credit_consumption=False,
        automatic_recharge=False,
        limits={"rpm": 30, "rpd": 1000, "tpm": 8000, "tpd": 200000},
        verified_at=now,
        valid_until=now + timedelta(minutes=10),
        evidence_references=["fixture://independent-console-proof"],
        evidence_digest="a" * 64,
        account_key_association_confirmed=True,
    )
    service = GroqAcceptanceService(app)
    view = service.prepare(
        actor,
        PrepareGroqAcceptance(provider_name="groq", expected_revision=1, account_evidence=evidence),
    )
    return app, actor, service, view


async def test_preparation_never_grants_inference_or_normal_activation(acceptance):
    app, actor, service, view = acceptance
    assert view.status == "prepared" and view.plan.maximum_requests == 1
    assert not app.state.settings.verified_cloud_execution_allowed
    assert not app.state.provider_configuration_service.list(actor)[0].enabled
    with pytest.raises(DomainError) as rejected:
        await service.execute(actor, view.id, view.plan_hash)
    assert rejected.value.code == "GROQ_ACCEPTANCE_APPROVAL_REQUIRED"


async def test_approved_request_runs_once_captures_headers_and_never_activates(acceptance):
    app, actor, service, view = acceptance
    approved = service.approve(actor, view.id, view.plan_hash)
    assert approved.status == "approved"
    calls = []

    def response(request):
        assert request.method == "POST" and str(request.url) == view.plan.endpoint
        assert request.headers["Authorization"] == f"Bearer {SECRET}"
        body = json.loads(request.content)
        assert body == view.plan.request_body and "tools" not in body
        calls.append(True)
        return httpx.Response(
            200,
            headers={
                "x-ratelimit-limit-requests": "1000",
                "x-ratelimit-remaining-requests": "999",
                "x-ratelimit-reset-requests": "1m26.4s",
                "x-ratelimit-limit-tokens": "8000",
                "x-ratelimit-remaining-tokens": "7980",
                "x-ratelimit-reset-tokens": "1s",
            },
            json={
                "model": "vendor/model",
                "choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        result = await service.execute(actor, view.id, view.plan_hash, client=client)
        replay = await service.execute(actor, view.id, view.plan_hash, client=client)
    assert result == replay and result.status == "succeeded" and result.initialized_capacity
    assert result.quota_observation.rpd_remaining == 999
    assert result.quota_observation.tpm_remaining == 7980
    assert calls == [True]
    assert not app.state.settings.verified_cloud_execution_allowed
    assert not app.state.provider_configuration_service.list(actor)[0].enabled
    assert SECRET not in result.model_dump_json()


async def test_uncertain_outcome_cannot_be_replayed(acceptance):
    _, actor, service, view = acceptance
    service.approve(actor, view.id, view.plan_hash)
    calls = []

    def timeout(request):
        calls.append(True)
        raise httpx.ReadTimeout("untrusted detail must not escape", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        first = await service.execute(actor, view.id, view.plan_hash, client=client)
        second = await service.execute(actor, view.id, view.plan_hash, client=client)
    assert first == second and first.status == "uncertain" and calls == [True]


async def test_profile_change_revokes_approved_request_before_network(acceptance):
    app, actor, service, view = acceptance
    service.approve(actor, view.id, view.plan_hash)
    app.state.provider_configuration_service.save(
        actor, draft(expected_revision=1, api_key=None, priority=10)
    )
    with pytest.raises(DomainError) as rejected:
        await service.execute(actor, view.id, view.plan_hash)
    assert rejected.value.code == "GROQ_ACCEPTANCE_BINDING_CHANGED"
