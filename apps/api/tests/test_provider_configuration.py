"""Operator configuration, private credentials and native authorization boundaries."""

import json
import os

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.agent_runtime.authorization import RuntimeActorContext
from app.agent_runtime.errors import RuntimeActorInactiveError
from app.core.errors import DomainError
from app.db.models import AuditEventRow, OutboxEventRow
from app.main import create_app
from app.models.identity import AssignPermissionRequest, CreateAgentRequest, CreatePermissionRequest
from tests.test_persistence import database_url

SECRET = "gsk_fixture_private_provider_key_1102"


def draft(**changes):
    from app.models.provider_configuration import SaveProviderProfile

    values = dict(
        provider_name="groq",
        integration="groq",
        selected_model="vendor/model",
        api_key=SECRET,
        expected_revision=0,
    )
    values.update(changes)
    return SaveProviderProfile(**values)


def test_secret_draft_is_write_only_and_cannot_grant_activation():
    value = draft()
    assert SECRET not in repr(value)
    assert SECRET not in value.model_dump_json()
    with pytest.raises(ValidationError):
        draft(model_verified_cloud_enabled=True)
    with pytest.raises(ValidationError):
        draft(api_key="key\nwith-header-injection")


@pytest.fixture
def profile_app(tmp_path):
    app = create_app(database_url=database_url(tmp_path / "providers.db"))
    identity = app.state.identity_service
    operator = identity.create_agent(
        CreateAgentRequest(
            stable_key="model-operator",
            display_name="Model operator",
            agent_type="worker",
        )
    )
    identity.transition(operator.id, "active")
    permission = identity.create_definition(
        "permission",
        CreatePermissionRequest(
            stable_key="models.configure",
            display_name="Configure model providers",
            resource_type="administrative_function",
            action="configure_models",
        ),
    )
    identity.assign_permission(
        operator.id,
        AssignPermissionRequest(
            permission_id=permission.id,
            resource_type="administrative_function",
            resource_id="model_configuration",
            effect="allow",
        ),
    )
    yield app, RuntimeActorContext(operator.id, operator.stable_key)
    app.state.engine.dispose()


@pytest.mark.skipif(os.name != "nt", reason="actual Windows user-bound credential protection")
def test_profile_roundtrip_keeps_credentials_out_of_views_audit_and_outbox(profile_app):
    from app.provider_configuration.repository import ModelProviderProfileRow
    from app.provider_configuration.service import ProviderConfigurationService

    app, actor = profile_app
    service = ProviderConfigurationService(app)
    result = service.save(actor, draft())
    assert result.revision == 1 and result.credential_configured
    assert result.state == "evidence_required" and result.enabled is False
    reopened = ProviderConfigurationService(app)
    assert reopened.list(actor) == [result]
    assert reopened.repository.resolve_key("groq").get_secret_value() == SECRET
    assert not app.state.settings.verified_cloud_execution_allowed
    with app.state.repository.session_factory() as session:
        row = session.get(ModelProviderProfileRow, "groq")
        assert SECRET.encode() not in row.encrypted_credential
        serialized = json.dumps(row.payload)
        serialized += json.dumps(
            [event.payload for event in session.scalars(select(AuditEventRow))]
        )
        serialized += json.dumps(
            [event.envelope for event in session.scalars(select(OutboxEventRow))]
        )
    assert SECRET not in serialized
    assert SECRET not in result.model_dump_json()


@pytest.mark.skipif(os.name != "nt", reason="actual Windows user-bound credential protection")
def test_revision_fence_and_revoked_operator_cannot_replace_credentials(profile_app):
    from app.provider_configuration.service import ProviderConfigurationService

    app, actor = profile_app
    service = ProviderConfigurationService(app)
    first = service.save(actor, draft())
    with pytest.raises(DomainError) as error:
        service.save(actor, draft(api_key="gsk_other_private_fixture_1102"))
    assert error.value.code == "MODEL_CONFIGURATION_CHANGED"
    app.state.identity_service.transition(actor.actor_id, "suspended")
    with pytest.raises(RuntimeActorInactiveError):
        service.save(actor, draft(expected_revision=first.revision))
    assert service.repository.resolve_key("groq").get_secret_value() == SECRET


def test_enabled_profile_requires_separate_current_evidence():
    with pytest.raises(ValidationError):
        draft(enabled=True)


def test_task_actor_without_configuration_permission_cannot_store_a_key(profile_app):
    from app.provider_configuration.service import ProviderConfigurationService

    app, _ = profile_app
    identity = app.state.identity_service
    row = identity.create_agent(
        CreateAgentRequest(
            stable_key="unprivileged-worker",
            display_name="Unprivileged",
            agent_type="worker",
        )
    )
    identity.transition(row.id, "active")
    service = ProviderConfigurationService(app)
    with pytest.raises(DomainError) as error:
        service.save(RuntimeActorContext(row.id, row.stable_key), draft())
    assert error.value.code == "MODEL_CONFIGURATION_PERMISSION_DENIED"
    with service.repository.sessions() as session:
        assert service.repository.names(session) == []


@pytest.mark.skipif(os.name != "nt", reason="actual Windows user-bound credential protection")
def test_rehashed_profile_cannot_replace_its_native_audit_proof(profile_app):
    from app.provider_configuration.repository import ModelProviderProfileRow, private_digest
    from app.provider_configuration.service import ProviderConfigurationService

    app, actor = profile_app
    service = ProviderConfigurationService(app)
    service.save(actor, draft())
    with app.state.repository.session_factory() as session, session.begin():
        row = session.get(ModelProviderProfileRow, "groq")
        row.payload = {**row.payload, "selected_model": "different/model"}
        row.private_digest = private_digest(row.payload, row.encrypted_credential)
    with pytest.raises(DomainError) as error:
        service.repository.resolve_key("groq")
    assert error.value.code == "MODEL_CONFIGURATION_CORRUPT"


@pytest.mark.skipif(os.name != "nt", reason="actual Windows user-bound credential protection")
def test_restart_uses_saved_evidence_without_exporting_a_key_to_environment(
    profile_app, monkeypatch
):
    from hashlib import sha256

    from app.provider_configuration.service import ProviderConfigurationService
    from tests.test_free_ai_capacity import allowance

    app, actor = profile_app
    grant = allowance(
        models={"vendor/model"}, credential_fingerprint=sha256(SECRET.encode()).hexdigest()
    )
    service = ProviderConfigurationService(app)
    service.save(actor, draft(enabled=True, allowance=grant))
    monkeypatch.setenv("JARVIS_MODEL_EXECUTION_MODE", "free_only")
    monkeypatch.setenv("JARVIS_MODEL_VERIFIED_CLOUD_ENABLED", "true")
    monkeypatch.delenv("JARVIS_MODEL_FREE_ALLOWANCES_JSON", raising=False)
    monkeypatch.delenv("TEST_FREE_KEY", raising=False)
    reopened = create_app(
        database_url=app.state.settings.database_url, recover_interrupted_workflow=False
    )
    try:
        provider = reopened.state.model_router.registry.get("groq")
        assert provider.api_key.get_secret_value() == SECRET
        assert os.environ.get("TEST_FREE_KEY") is None
        assert reopened.state.settings.verified_cloud_execution_allowed
        service.save(actor, draft(enabled=False, api_key=None, expected_revision=1))
        assert not provider.free_eligible("vendor/model")
        import asyncio

        from app.model_providers.contracts import ModelExecutionRequest
        from app.model_providers.errors import ProviderExecutionDisabledError

        with pytest.raises(ProviderExecutionDisabledError):
            asyncio.run(provider.execute(ModelExecutionRequest(prompt="must never send")))
    finally:
        reopened.state.engine.dispose()


@pytest.mark.skipif(os.name != "nt", reason="actual Windows user-bound credential protection")
def test_saving_verified_profile_never_activates_cloud_inference(profile_app, monkeypatch):
    from hashlib import sha256

    from app.provider_configuration.service import ProviderConfigurationService
    from tests.test_free_ai_capacity import allowance

    app, actor = profile_app
    grant = allowance(
        models={"vendor/model"}, credential_fingerprint=sha256(SECRET.encode()).hexdigest()
    )
    service = ProviderConfigurationService(app)
    service.save(actor, draft(enabled=True, allowance=grant))
    monkeypatch.setenv("JARVIS_MODEL_EXECUTION_MODE", "free_only")
    monkeypatch.setenv("JARVIS_MODEL_VERIFIED_CLOUD_ENABLED", "false")
    reopened = create_app(
        database_url=app.state.settings.database_url, recover_interrupted_workflow=False
    )
    try:
        assert reopened.state.model_router.registry.list() == []
        assert not reopened.state.settings.verified_cloud_execution_allowed
    finally:
        reopened.state.engine.dispose()


@pytest.mark.skipif(os.name != "nt", reason="actual Windows user-bound credential protection")
def test_supervisor_preflight_reads_saved_evidence_without_a_credential_environment(
    profile_app, tmp_path
):
    from hashlib import sha256

    from app.provider_configuration.service import ProviderConfigurationService
    from tests.test_free_ai_capacity import allowance
    from tests.test_runtime_supervisor import make_config

    app, actor = profile_app
    grant = allowance(
        models={"vendor/model"}, credential_fingerprint=sha256(SECRET.encode()).hexdigest()
    )
    ProviderConfigurationService(app).save(actor, draft(enabled=True, allowance=grant))
    config = make_config(
        tmp_path,
        JARVIS_MODEL_EXECUTION_MODE="free_only",
        JARVIS_MODEL_VERIFIED_CLOUD_ENABLED="true",
        JARVIS_DATABASE_URL=app.state.settings.database_url,
    )
    assert config.ollama_relevant is False
    assert "TEST_FREE_KEY" not in config.environment


def test_configuration_http_is_local_origin_and_permission_scoped(profile_app):
    from fastapi.testclient import TestClient

    app, actor = profile_app
    path = "/api/model-configuration/profiles"
    headers = {"Origin": app.state.settings.web_origin, "X-Jarvis-Actor-Id": actor.actor_id}
    with TestClient(app) as client:
        listed = client.get(path, headers=headers)
        assert listed.status_code == 200
        assert listed.headers["Cache-Control"] == "no-store"
        denied = client.get(path, headers={**headers, "Origin": "https://untrusted.example"})
        assert denied.status_code == 403
        assert client.get(path, headers={"Origin": headers["Origin"]}).status_code == 403
        saved = client.post(path, headers=headers, json=draft().model_dump(mode="json"))
        # SecretStr's JSON mask must never be accepted as a real submitted key.
        assert saved.status_code == 422
        payload = draft().model_dump(mode="json", exclude={"api_key"})
        payload["api_key"] = SECRET
        saved = client.post(path, headers=headers, json=payload)
        assert saved.status_code == 200
        assert SECRET not in saved.text
        assert "api_key" not in saved.text
        assert saved.json()["data"]["credential_configured"] is True
        from hashlib import sha256

        assert saved.json()["data"]["credential_fingerprint"] == sha256(SECRET.encode()).hexdigest()
        assert SECRET not in client.get(path, headers=headers).text


def test_model_operator_setup_is_narrow_idempotent_and_never_reactivates(profile_app):
    from app.provider_configuration.provisioning import configure_model_operator

    app, _ = profile_app
    actor_id = configure_model_operator(app)
    assert configure_model_operator(app) == actor_id
    identity = app.state.identity_service
    assert identity.check_permission_resource_access(
        actor_id, "models.configure", "administrative_function", "model_configuration"
    ).allowed
    assert not identity.check_permission_resource_access(
        actor_id, "runtime.admin", "administrative_function", "system_control"
    ).allowed
    identity.transition(actor_id, "suspended")
    with pytest.raises(DomainError, match="reactivate"):
        configure_model_operator(app)


def test_profile_cannot_shadow_local_provider_or_rotate_accounts(profile_app):
    from hashlib import sha256

    from app.provider_configuration.service import ProviderConfigurationService
    from tests.test_free_ai_capacity import allowance

    app, actor = profile_app
    service = ProviderConfigurationService(app)
    with pytest.raises(DomainError) as reserved:
        service.save(actor, draft(provider_name=app.state.settings.model_ollama_name))
    assert reserved.value.code == "MODEL_CONFIGURATION_RESERVED_PROVIDER"
    grant = allowance(
        models={"vendor/model"}, credential_fingerprint=sha256(SECRET.encode()).hexdigest()
    )
    service.save(actor, draft(enabled=True, allowance=grant))
    second = grant.model_copy(update={"provider": "groq-second", "account_id": "other-account"})
    with pytest.raises(DomainError) as rotation:
        service.save(actor, draft(provider_name="groq-second", enabled=True, allowance=second))
    assert rotation.value.code == "MODEL_CONFIGURATION_ACCOUNT_POLICY"
    assert len(service.list(actor)) == 1


@pytest.mark.parametrize("constraint", ["priority", "allowance_bytes"])
def test_aggregate_startup_bounds_reject_a_save_without_losing_prior_profiles(
    profile_app, constraint
):
    from hashlib import sha256

    from app.provider_configuration.service import ProviderConfigurationService
    from tests.test_free_ai_capacity import allowance

    app, actor = profile_app
    service = ProviderConfigurationService(app)
    models = (
        {"vendor/model"}
        if constraint == "priority"
        else {f"model-{i:03d}-" + "x" * 190 for i in range(100)}
    )
    selected = sorted(models)[0]
    count = 3 if constraint == "priority" else 4
    for index in range(count + 1):
        name = f"groq-{index}" + ("x" * 114 if constraint == "priority" else "")
        grant = allowance(
            provider=name, models=models, credential_fingerprint=sha256(SECRET.encode()).hexdigest()
        )
        body = draft(provider_name=name, selected_model=selected, enabled=True, allowance=grant)
        if index < count:
            service.save(actor, body)
        else:
            with pytest.raises(DomainError) as rejected:
                service.save(actor, body)
            assert rejected.value.code == "MODEL_CONFIGURATION_ACCOUNT_POLICY"
    assert len(service.list(actor)) == count


async def test_groq_metadata_check_is_audited_bounded_and_does_not_activate(profile_app):
    import httpx

    from app.provider_configuration.metadata import check_groq_metadata
    from app.provider_configuration.service import ProviderConfigurationService

    app, actor = profile_app
    service = ProviderConfigurationService(app)
    service.save(actor, draft())
    calls = []

    def respond(request):
        calls.append((request.method, str(request.url)))
        assert request.headers["Authorization"] == f"Bearer {SECRET}"
        return httpx.Response(200, json={"data": [{"id": "vendor/model"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await check_groq_metadata(service, actor, "groq", 1, client=client)
        with pytest.raises(DomainError):
            await check_groq_metadata(service, actor, "groq", 2, client=client)
    assert calls == [("GET", "https://api.groq.com/openai/v1/models")]
    assert result.state == "catalog_confirmed" and result.exact_model_available is True
    assert not app.state.settings.verified_cloud_execution_allowed
    assert SECRET not in result.model_dump_json()
    from sqlalchemy import select

    from app.db.models import AuditEventRow

    with service.repository.sessions() as session:
        audit = session.scalars(
            select(AuditEventRow).where(AuditEventRow.event_type.like("model.provider.metadata_%"))
        ).all()
        assert len(audit) == 2
        assert SECRET not in str([event.payload for event in audit])


@pytest.mark.parametrize(
    "status,content,expected",
    [
        (401, b"untrusted response", "authentication_failed"),
        (429, b"untrusted response", "rate_limited"),
        (302, b"redirect rejected", "unavailable"),
        (200, b"x" * 262145, "invalid_catalog"),
        (200, ('{"data":[{"id":"' + SECRET + '"}]}').encode(), "invalid_catalog"),
    ],
    ids=["unauthorized", "quota", "redirect", "oversized", "credential"],
)
async def test_metadata_never_follows_redirects_or_exposes_untrusted_error_bodies(
    status, content, expected
):
    import httpx
    from pydantic import SecretStr

    from app.provider_configuration.metadata import _catalog

    calls = []

    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(
            status, content=content, headers={"Location": "https://untrusted.example/"}
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), follow_redirects=True
    ) as client:
        state, models = await _catalog(client, SecretStr(SECRET))
    assert state == expected and models == []
    assert calls == ["https://api.groq.com/openai/v1/models"]


async def test_provider_rechecks_revocation_after_http_client_preparation():
    import httpx
    from pydantic import SecretStr

    from app.model_providers.contracts import ModelExecutionRequest
    from app.model_providers.errors import ProviderExecutionDisabledError
    from app.model_providers.openai_compatible import OpenAICompatibleProvider

    revoked = False
    provider = OpenAICompatibleProvider(
        name="guarded",
        base_url="http://127.0.0.1:9999/v1/",
        api_key=SecretStr(SECRET),
        default_model="fixture-model",
        execution_mode="local_only",
        configuration_guard=lambda: not revoked,
    )

    def forbidden(_):
        pytest.fail("A revoked provider must not send a physical HTTP request")

    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:

        def prepare():
            nonlocal revoked
            revoked = True
            return client, False

        provider._client_or_new = prepare
        with pytest.raises(ProviderExecutionDisabledError):
            await provider.execute(ModelExecutionRequest(prompt="do not send"))
        assert revoked
