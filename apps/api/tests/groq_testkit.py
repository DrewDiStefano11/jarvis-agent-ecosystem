"""Synthetic account/HTTP fixtures; never admissible operator account evidence."""

import asyncio
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import httpx

from app.models.groq_acceptance import GroqAccountEvidence, PrepareGroqAcceptance


def save_with_intercepted_acceptance(service, actor, body):
    """Exercise disabled-save → exact approval → intercepted receipt → enabled-save."""
    app = service.app
    disabled = service.save(actor, body.model_copy(update={"enabled": False}))
    grant = body.allowance
    if not app.state.groq_capacity.available(grant, body.selected_model, 1):
        now = datetime.now(UTC)
        evidence = GroqAccountEvidence(
            account_id=grant.account_id,
            model=body.selected_model,
            credential_fingerprint=grant.credential_fingerprint,
            plan="Free",
            nonbillable=True,
            billing_enabled=False,
            credit_consumption=False,
            automatic_recharge=False,
            limits={"rpm": 30, "rpd": 1000, "tpm": 8000, "tpd": 200000},
            verified_at=now,
            valid_until=now + timedelta(minutes=10),
            evidence_references=["fixture://synthetic-console"],
            evidence_digest=sha256(b"synthetic account fixture; not live evidence").hexdigest(),
            account_key_association_confirmed=True,
        )
        acceptance = app.state.groq_acceptance_service
        view = acceptance.prepare(
            actor,
            PrepareGroqAcceptance(
                provider_name=body.provider_name,
                expected_revision=disabled.revision,
                account_evidence=evidence,
            ),
        )
        acceptance.approve(actor, view.id, view.plan_hash)

        async def receive():
            def response(request):
                return httpx.Response(
                    200,
                    headers={
                        "x-ratelimit-limit-requests": "1000",
                        "x-ratelimit-remaining-requests": "999",
                        "x-ratelimit-reset-requests": "1m26s",
                        "x-ratelimit-limit-tokens": "8000",
                        "x-ratelimit-remaining-tokens": "7980",
                        "x-ratelimit-reset-tokens": "1s",
                    },
                    json={
                        "model": body.selected_model,
                        "choices": [
                            {"finish_reason": "stop", "message": {"content": '{"ok":true}'}}
                        ],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
                    },
                )

            async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
                result = await acceptance.execute(actor, view.id, view.plan_hash, client=client)
            assert result.status == "succeeded" and result.initialized_capacity

        asyncio.run(receive())
    return service.save(actor, body.model_copy(update={"expected_revision": disabled.revision}))
