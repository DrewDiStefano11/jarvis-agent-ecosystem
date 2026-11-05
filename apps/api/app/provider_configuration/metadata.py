"""Explicit fixed-endpoint Groq metadata access, never inference or eligibility evidence."""

import asyncio
import json
import re
from uuid import uuid4

import httpx

from app.core.errors import DomainError
from app.model_providers.contracts import validate_safe_identifier
from app.models.provider_configuration import ProviderMetadataView
from app.provider_configuration.protection import unprotect


def _profile(service, actor, session, name, revision, expected_digest=None):
    service.authorize(actor, session)
    result = service.repository.read(session, name)
    if (
        result is None
        or result[0].revision != revision
        or (expected_digest is not None and result[0].private_digest != expected_digest)
    ):
        raise DomainError(
            "MODEL_CONFIGURATION_CHANGED",
            "Refresh the current provider profile before checking it.",
            409,
        )
    return result


async def _catalog(client, credential):
    async with client.stream(
        "GET",
        "https://api.groq.com/openai/v1/models",
        headers={
            "Authorization": f"Bearer {credential.get_secret_value()}",
            "Accept": "application/json",
            "Accept-Encoding": "identity",
        },
        timeout=5,
        follow_redirects=False,
    ) as response:
        if response.status_code in {401, 403}:
            return "authentication_failed", []
        if response.status_code == 429:
            return "rate_limited", []
        if response.status_code != 200:
            return "unavailable", []
        if response.headers.get("content-encoding", "identity").lower() != "identity":
            return "invalid_catalog", []
        body = bytearray()
        async for chunk in response.aiter_bytes():
            if len(body) + len(chunk) > 262144:
                return "invalid_catalog", []
            body.extend(chunk)
    decoded = json.loads(body)
    records = decoded.get("data") if isinstance(decoded, dict) else None
    if not isinstance(records, list) or len(records) > 256:
        return "invalid_catalog", []
    models = []
    for record in records:
        model = record.get("id") if isinstance(record, dict) else None
        if (
            not isinstance(model, str)
            or not re.fullmatch(r"[A-Za-z0-9_.:/@-]{1,200}", model)
            or credential.get_secret_value() in model
        ):
            return "invalid_catalog", []
        validate_safe_identifier(model)
        models.append(model)
    return "catalog_confirmed", sorted(set(models))


async def check_groq_metadata(service, actor, name, revision, *, client=None):
    check_id = uuid4().hex
    with service.app.state.task_leases._write() as session:
        row, configuration = _profile(service, actor, session, name, revision)
        if configuration.integration != "groq":
            raise DomainError(
                "MODEL_METADATA_UNSUPPORTED",
                "Metadata checking is currently implemented for Groq only.",
                409,
            )
        if not row.encrypted_credential:
            raise DomainError(
                "MODEL_CREDENTIAL_UNAVAILABLE", "Store a protected credential first.", 409
            )
        credential = unprotect(row.encrypted_credential)
        expected_digest = row.private_digest
        selected_model = configuration.selected_model
        service.app.state.task_leases._add_event(
            session,
            "model.provider.metadata_requested",
            "Operator requested Groq model metadata",
            actor_identity_id=actor.actor_id,
            payload={"checkId": check_id, "providerName": name, "profileRevision": revision},
        )
    service.app.state.repository.refresh_event_cursor()
    # Recheck current authorization/configuration immediately before external access.
    with service.repository.sessions() as session:
        _profile(service, actor, session, name, revision, expected_digest)
    owned = client is None
    if owned:
        client = httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=5)
    try:
        async with asyncio.timeout(8):
            with service.repository.sessions() as session:
                _profile(service, actor, session, name, revision, expected_digest)
            try:
                state, models = await _catalog(client, credential)
            except httpx.HTTPError:
                state, models = "unavailable", []
            except (ValueError, RecursionError):
                state, models = "invalid_catalog", []
    except (TimeoutError, OSError):
        state, models = "unavailable", []
    finally:
        if owned:
            await client.aclose()
    result = ProviderMetadataView(
        provider_name=name,
        profile_revision=revision,
        state=state,
        models=models,
        exact_model_available=selected_model in models if state == "catalog_confirmed" else None,
    )
    with service.app.state.task_leases._write() as session:
        _profile(service, actor, session, name, revision, expected_digest)
        service.app.state.task_leases._add_event(
            session,
            "model.provider.metadata_completed",
            "Groq model metadata check completed",
            actor_identity_id=actor.actor_id,
            payload={
                "checkId": check_id,
                "providerName": name,
                "profileRevision": revision,
                "status": state,
                "modelCount": len(models),
            },
        )
    service.app.state.repository.refresh_event_cursor()
    return result
