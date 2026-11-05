"""One exact operator-approved Groq call; never normal activation or automatic retry."""

import asyncio
import json
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import select

from app.autonomous_worker.provisioning import _find_permission
from app.core.errors import DomainError
from app.db.models import AgentPermissionAssignmentRow, IdentityAgentRow, SystemStateRow
from app.model_providers.groq_rate_limits import bootstrap_state, complete, parse_headers, rejected
from app.models.groq_acceptance import GroqAcceptancePlan, GroqAcceptanceView
from app.models.identity import AssignPermissionRequest, CreatePermissionRequest
from app.provider_configuration.groq_repository import (
    GroqAcceptanceRow,
    GroqRateStateRow,
    GroqRepository,
    rate_scope,
)
from app.provider_configuration.protection import unprotect
from app.self_build.policy import digest


def policy_digest():
    root = Path(__file__).resolve().parents[1]
    # This rare operator action pins the full application closure, including
    # authorization, transactions and configuration; it never runs while idle.
    return digest(
        {
            path.relative_to(root).as_posix(): sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*.py"))
        }
    )


def request_body(model):
    return {
        "model": model,
        "messages": [{"role": "user", "content": 'Return exactly the JSON object {"ok":true}.'}],
        "stream": False,
        "temperature": 0,
        "reasoning_effort": "low",
        "max_completion_tokens": 512,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "jarvis_acceptance",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {"ok": {"type": "boolean"}},
                    "required": ["ok"],
                    "additionalProperties": False,
                },
            },
        },
    }


class GroqAcceptanceService:
    def __init__(self, app):
        self.app = app
        self.repository = GroqRepository(app)
        self.configuration = app.state.provider_configuration_service
        self.loaded_policy_digest = policy_digest()

    def _fence(self, session, actor, plan, approved=False):
        self.configuration.authorize(actor, session)
        now = self.repository.now()
        evidence = plan.evidence
        if (
            self.app.state.settings.verified_cloud_execution_allowed
            or session.get(SystemStateRow, 1).emergency_stop
        ):
            raise DomainError(
                "GROQ_ACCEPTANCE_DISABLED",
                "Acceptance requires emergency stop off and ongoing inference disabled.",
                409,
            )
        existing = self.configuration.repository.read(session, plan.provider_name)
        if (
            not existing
            or existing[0].revision != plan.profile_revision
            or existing[0].private_digest != plan.profile_digest
            or existing[1].enabled
        ):
            raise DomainError(
                "GROQ_ACCEPTANCE_BINDING_CHANGED",
                "The approved provider configuration changed.",
                409,
            )
        row, configuration = existing
        if (
            configuration.integration != "groq"
            or configuration.selected_model != evidence.model
            or not row.encrypted_credential
        ):
            raise DomainError(
                "GROQ_ACCEPTANCE_BINDING_CHANGED",
                "The Groq credential or model binding is unavailable.",
                409,
            )
        if not evidence.verified_at <= now < min(
            evidence.valid_until, plan.expires_at
        ) or now - evidence.verified_at > timedelta(minutes=15):
            raise DomainError(
                "GROQ_ACCEPTANCE_EXPIRED",
                "Current independently verified Free-account evidence is required.",
                409,
            )
        if (
            plan.policy_digest != self.loaded_policy_digest
            or self.loaded_policy_digest != policy_digest()
            or plan.request_body != request_body(evidence.model)
        ):
            raise DomainError(
                "GROQ_ACCEPTANCE_BINDING_CHANGED",
                "Acceptance policy or exact request changed.",
                409,
            )
        key = unprotect(row.encrypted_credential)
        if sha256(key.get_secret_value().encode()).hexdigest() != evidence.credential_fingerprint:
            raise DomainError(
                "GROQ_ACCEPTANCE_BINDING_CHANGED",
                "Credential fingerprint differs from verified evidence.",
                409,
            )
        if approved:
            decision = self.app.state.identity_service.check_permission_resource_access(
                actor.actor_id, "models.accept_once", "model_acceptance", plan.id, session=session
            )
            if not decision.allowed:
                raise DomainError(
                    "GROQ_ACCEPTANCE_PERMISSION_DENIED",
                    "Exact-request acceptance permission is required.",
                    403,
                )
        return key

    def prepare(self, actor, body):
        now = self.repository.now()
        with self.app.state.task_leases._write() as session:
            self.configuration.authorize(actor, session)
            if self.loaded_policy_digest != policy_digest():
                raise DomainError(
                    "GROQ_ACCEPTANCE_BINDING_CHANGED",
                    "Restart the service after runtime policy changes.",
                    409,
                )
            existing = self.configuration.repository.read(session, body.provider_name)
            if not existing or existing[0].revision != body.expected_revision:
                raise DomainError(
                    "GROQ_ACCEPTANCE_BINDING_CHANGED", "Refresh the provider configuration.", 409
                )
            scope = rate_scope(body.account_evidence.account_id, body.account_evidence.model)
            row = session.scalar(
                select(GroqAcceptanceRow).where(GroqAcceptanceRow.scope_key == scope)
            )
            if row is not None:
                _, prior = self.repository.acceptance(session, row.id)
                if prior.status not in {"prepared", "approved"} or now < prior.plan.expires_at:
                    return prior
            identifier = row.id if row is not None else "groq-accept-" + uuid4().hex
            plan = GroqAcceptancePlan(
                id=identifier,
                provider_name=body.provider_name,
                profile_revision=existing[0].revision,
                profile_digest=existing[0].private_digest,
                evidence=body.account_evidence,
                created_at=now,
                expires_at=min(now + timedelta(minutes=5), body.account_evidence.valid_until),
                token_reservation=min(
                    body.account_evidence.limits.tpm, body.account_evidence.limits.tpd
                ),
                request_body=request_body(body.account_evidence.model),
                policy_digest=self.loaded_policy_digest,
            )
            self._fence(session, actor, plan)
            if plan.token_reservation < 1024:
                raise DomainError(
                    "GROQ_ACCEPTANCE_TOKEN_BOUND",
                    "Verified capacity cannot bound the fixed acceptance request.",
                    409,
                )
            view = GroqAcceptanceView(
                id=identifier,
                plan=plan,
                plan_hash=digest(plan.model_dump(mode="json")),
                status="prepared",
            )
            if row is None:
                row = GroqAcceptanceRow(id=identifier, scope_key=scope)
            self.repository.write(session, row, view, "acceptance", actor.actor_id)
        self.app.state.repository.refresh_event_cursor()
        return view

    def get(self, actor, identifier):
        with self.repository.sessions() as session:
            self.configuration.authorize(actor, session)
            return self.repository.acceptance(session, identifier)[1]

    def approve(self, actor, identifier, expected_hash):
        with self.repository.sessions() as session:
            _, view = self.repository.acceptance(session, identifier)
            self._fence(session, actor, view.plan)
            if expected_hash != view.plan_hash:
                raise DomainError(
                    "GROQ_ACCEPTANCE_HASH_MISMATCH", "Approve the current exact request hash.", 409
                )
            if view.status != "prepared":
                return view
        identity = self.app.state.identity_service
        permission = _find_permission(identity, "models.accept_once")
        if permission is None:
            permission = identity.create_definition(
                "permission",
                CreatePermissionRequest(
                    stable_key="models.accept_once",
                    display_name="Approve one exact Groq request",
                    resource_type="model_acceptance",
                    action="accept_once",
                ),
            )
        if (
            not permission.is_enabled
            or permission.resource_type != "model_acceptance"
            or permission.action != "accept_once"
        ):
            raise DomainError(
                "PERMISSION_SCOPE_MISMATCH",
                "Acceptance permission definition is incompatible.",
                409,
            )
        try:
            identity.assign_permission(
                actor.actor_id,
                AssignPermissionRequest(
                    permission_id=permission.id,
                    effect="allow",
                    resource_type="model_acceptance",
                    resource_id=identifier,
                    expires_at=view.plan.expires_at,
                    assigned_by=actor.actor_id,
                    reason="Explicit approval of exact one-request plan " + expected_hash,
                ),
            )
        except DomainError as error:
            if error.code != "DUPLICATE_ASSIGNMENT":
                raise
        with self.app.state.task_leases._write() as session:
            row, current = self.repository.acceptance(session, identifier)
            self._fence(session, actor, current.plan, approved=True)
            if current.plan_hash != expected_hash or current.status != "prepared":
                raise DomainError(
                    "GROQ_ACCEPTANCE_BINDING_CHANGED",
                    "Acceptance changed before approval committed.",
                    409,
                )
            grant = session.scalar(
                select(AgentPermissionAssignmentRow).where(
                    AgentPermissionAssignmentRow.agent_id == actor.actor_id,
                    AgentPermissionAssignmentRow.permission_id == permission.id,
                    AgentPermissionAssignmentRow.resource_type == "model_acceptance",
                    AgentPermissionAssignmentRow.resource_id == identifier,
                    AgentPermissionAssignmentRow.revoked_at.is_(None),
                    AgentPermissionAssignmentRow.effect == "allow",
                )
            )
            approved = current.model_copy(
                update={
                    "status": "approved",
                    "approved_actor_id": actor.actor_id,
                    "approved_actor_version": session.get(IdentityAgentRow, actor.actor_id).version,
                    "approval_grant_id": grant.id,
                }
            )
            self.repository.write(session, row, approved, "acceptance", actor.actor_id)
        self.app.state.repository.refresh_event_cursor()
        return approved

    def _approved_fence(self, session, actor, view):
        if (
            view.approved_actor_id != actor.actor_id
            or session.get(IdentityAgentRow, actor.actor_id).version != view.approved_actor_version
        ):
            raise DomainError("GROQ_ACCEPTANCE_BINDING_CHANGED", "Approving identity changed.", 409)
        key = self._fence(session, actor, view.plan, approved=True)
        decision = self.app.state.identity_service.check_permission_resource_access(
            actor.actor_id, "models.accept_once", "model_acceptance", view.id, session=session
        )
        if f"direct:{view.approval_grant_id}" not in decision.matched_grants:
            raise DomainError(
                "GROQ_ACCEPTANCE_PERMISSION_DENIED",
                "The exact approval grant is no longer active.",
                403,
            )
        return key

    async def execute(self, actor, identifier, expected_hash, *, client=None):
        with self.app.state.task_leases._write() as session:
            self.configuration.authorize(actor, session)
            row, view = self.repository.acceptance(session, identifier)
            if view.plan_hash != expected_hash:
                raise DomainError(
                    "GROQ_ACCEPTANCE_HASH_MISMATCH", "Exact request hash differs.", 409
                )
            if view.status in {"dispatch_started", "succeeded", "failed", "uncertain"}:
                return view
            if view.status != "approved":
                raise DomainError(
                    "GROQ_ACCEPTANCE_APPROVAL_REQUIRED",
                    "Explicit exact-request approval is required.",
                    403,
                )
            key = self._approved_fence(session, actor, view)
            started = view.model_copy(update={"status": "dispatch_started"})
            state = bootstrap_state(
                view.plan.evidence.limits,
                identifier,
                self.repository.now(),
                view.plan.token_reservation,
            )
            rate = GroqRateStateRow(scope_key=row.scope_key, acceptance_id=identifier)
            self.repository.write(session, rate, state, "rate", actor.actor_id)
            self.repository.write(session, row, started, "acceptance", actor.actor_id)
        self.app.state.repository.refresh_event_cursor()
        owned = client is None
        if owned:
            client = httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=20)
        outcome = "uncertain"
        code = "outcome_unknown"
        initialized = False
        quota_observation = None
        try:
            async with asyncio.timeout(30):
                with self.repository.sessions() as session:
                    self._approved_fence(session, actor, started)
                async with client.stream(
                    "POST",
                    view.plan.endpoint,
                    json=view.plan.request_body,
                    headers={
                        "Authorization": "Bearer " + key.get_secret_value(),
                        "Accept-Encoding": "identity",
                    },
                    follow_redirects=False,
                    timeout=20,
                ) as response:
                    observed = self.repository.now()
                    raw = bytearray()
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise ValueError
                    async for chunk in response.aiter_bytes():
                        if len(raw) + len(chunk) > 262144:
                            raise ValueError
                        raw.extend(chunk)
                    if response.status_code == 429:
                        state = rejected(state, identifier, observed, response.headers)
                        outcome, code = "failed", "provider_429"
                    elif response.status_code != 200:
                        outcome, code = "failed", "provider_rejected"
                    else:
                        body = json.loads(raw)
                        if key.get_secret_value() in json.dumps(body):
                            raise ValueError
                        usage = body["usage"]
                        numbers = [
                            usage[name]
                            for name in ("prompt_tokens", "completion_tokens", "total_tokens")
                        ]
                        if (
                            any(type(number) is not int or number < 0 for number in numbers)
                            or numbers[2] != sum(numbers[:2])
                            or numbers[2] > view.plan.token_reservation
                            or numbers[1] > view.plan.request_body["max_completion_tokens"]
                        ):
                            raise ValueError
                        if (
                            body["model"] != view.plan.evidence.model
                            or len(body["choices"]) != 1
                            or body["choices"][0]["finish_reason"] != "stop"
                        ):
                            raise ValueError
                        answer = json.loads(body["choices"][0]["message"]["content"])
                        if (
                            not isinstance(answer, dict)
                            or set(answer) != {"ok"}
                            or answer["ok"] is not True
                        ):
                            raise ValueError
                        try:
                            headers = parse_headers(response.headers, observed)
                            quota_observation = headers
                        except ValueError:
                            headers = None
                        state = complete(state, identifier, observed, numbers[2], headers)
                        initialized = not state.unknown_provider_capacity
                        outcome, code = (
                            "succeeded",
                            "acceptance_ok" if initialized else "quota_headers_missing_or_invalid",
                        )
        except DomainError:
            outcome, code = "failed", "authority_revoked_before_send"
        except (ValueError, TypeError, KeyError, IndexError, RecursionError):
            outcome, code = "failed", "provider_response_invalid"
        except (httpx.HTTPError, TimeoutError, OSError):
            outcome, code = "uncertain", "outcome_unknown"
        finally:
            if owned:
                await client.aclose()
        result = started.model_copy(
            update={
                "status": outcome,
                "result_code": code,
                "initialized_capacity": initialized,
                "quota_observation": quota_observation,
            }
        )
        with self.app.state.task_leases._write() as session:
            row, current = self.repository.acceptance(session, identifier)
            if current.status != "dispatch_started" or current.plan_hash != expected_hash:
                raise DomainError(
                    "GROQ_ACCEPTANCE_BINDING_CHANGED",
                    "Acceptance outcome cannot replace different authority.",
                    409,
                )
            rate, _ = self.repository.rate(session, row.scope_key)
            self.repository.write(session, rate, state, "rate", actor.actor_id)
            self.repository.write(session, row, result, "acceptance", actor.actor_id)
        self.app.state.repository.refresh_event_cursor()
        return result
