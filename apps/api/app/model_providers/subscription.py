"""Supported ChatGPT OAuth Responses and Codex app-server integrations.

No API-key fallback, consumer backend endpoints, browser credentials, or credit
operations. Account-specific nonbillable evidence is required by both adapters.
"""

from __future__ import annotations

import json
from time import perf_counter
from typing import Any

import httpx
from pydantic import ValidationError

from app.model_providers.base import ProviderBase
from app.model_providers.chatgpt_auth import ChatGPTSession
from app.model_providers.contracts import (
    HealthStatus,
    ModelCapability,
    ModelExecutionRequest,
    ModelExecutionResponse,
    ProviderHealth,
    ProviderType,
)
from app.model_providers.errors import (
    AuthenticationError,
    InvalidModelRequestError,
    MalformedProviderResponseError,
    ProviderUnavailableError,
    QuotaExhaustedError,
    RequestTimeoutError,
)
from app.model_providers.free_capacity import FreeCapacityRepository, require_allowance
from app.model_providers.http import translate_http_error
from app.models.ai_capacity import FreeAllowance


def checked_usage(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    raise MalformedProviderResponseError("subscription usage is malformed")


class ChatGPTPlanProvider(ProviderBase):
    provider_type = ProviderType.CHATGPT_PLAN
    is_local = False
    capabilities = frozenset({ModelCapability.CHAT, ModelCapability.TEXT_GENERATION})

    def __init__(
        self,
        grant: FreeAllowance,
        capacity: FreeCapacityRepository,
        session: ChatGPTSession,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        session.require_plan_usage(grant.session_identity or grant.account_id)
        if grant.integration != "chatgpt":
            raise AuthenticationError("ChatGPT plan usage requires an authorized OAuth session")
        self.grant = grant
        self.capacity = capacity
        self.session = session
        self.access = session.access_token
        self.name = grant.provider
        self.default_model = sorted(grant.models)[0]
        self.client = client

    def free_eligible(self, model: str) -> bool:
        try:
            self.session.require_plan_usage(self.grant.session_identity or self.grant.account_id)
        except AuthenticationError:
            return False
        return self.capacity.available(self.grant, model)

    async def model_available(self, model: str) -> bool:
        return self.free_eligible(model)

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.name,
            healthy=self.free_eligible(self.default_model),
            status=HealthStatus.CONFIGURATION_ONLY,
            latency_ms=0,
            detail="account-specific eligibility evidence; no inference probe",
        )

    async def list_models(self) -> tuple[str, ...] | None:
        require_allowance(self.grant, self.default_model)
        client = self.client or httpx.AsyncClient(
            trust_env=False, follow_redirects=False, timeout=30
        )
        error = None
        try:
            response = await client.get("https://api.openai.com/v1/models", headers=self._headers())
            response.raise_for_status()
            body = response.json()
            return tuple(
                item["slug"]
                for item in body["models"]
                if item.get("visibility") == "list" and isinstance(item.get("slug"), str)
            )
        except httpx.HTTPError as exc:
            error = translate_http_error(exc, provider=self.name, model=self.default_model)
        except (ValueError, KeyError, TypeError, AttributeError):
            error = MalformedProviderResponseError("invalid account model catalog")
        finally:
            if self.client is None:
                await client.aclose()
        if error is not None:
            raise error

    def _headers(self) -> dict[str, str]:
        return {"Authorization": "Bearer " + self.access.get_secret_value()}

    async def execute(self, request: ModelExecutionRequest) -> ModelExecutionResponse:
        model = request.model or self.default_model
        self.session.require_plan_usage(self.grant.session_identity or self.grant.account_id)
        require_allowance(self.grant, model)
        if request.tools:
            raise InvalidModelRequestError("subscription adapter supports text inference only")
        if self.access.get_secret_value().startswith("sk-"):
            raise AuthenticationError("API keys are forbidden on the subscription route")
        self.capacity.reserve(self.grant, model)
        payload = {
            "model": model,
            "store": False,
            "stream": True,
            "input": [
                {
                    "role": "developer" if message.role.value == "system" else message.role.value,
                    "content": message.content,
                }
                for message in request.messages
            ],
        }
        # Unsupported preview controls are deliberately omitted; the runtime
        # still checks normalized token usage, deadline and final output schema.
        client = self.client or httpx.AsyncClient(
            trust_env=False, follow_redirects=False, timeout=request.timeout_seconds or 30
        )
        started = perf_counter()
        error = None
        completed = None
        size = 0
        event_lines: list[str] = []
        try:
            async with client.stream(
                "POST",
                "https://api.openai.com/v1/responses",
                headers=self._headers(),
                json=payload,
                timeout=request.timeout_seconds or 30,
            ) as response:
                if response.status_code != 200:
                    await response.aread()
                    response.raise_for_status()
                async for line in response.aiter_lines():
                    size += len(line.encode())
                    if size > 4_000_000:
                        raise MalformedProviderResponseError("subscription stream exceeds limit")
                    if line.startswith("data:"):
                        event_lines.append(line[5:].lstrip())
                    if not line and event_lines:
                        event = json.loads("\n".join(event_lines))
                        event_lines.clear()
                        if event.get("type") == "response.completed":
                            completed = event.get("response")
                        elif event.get("type") in {
                            "error",
                            "response.failed",
                            "response.incomplete",
                        }:
                            failure = (
                                (event.get("response") or {}).get("error")
                                or event.get("error")
                                or event
                            )
                            if isinstance(failure, dict) and failure.get("code") in {
                                "insufficient_quota",
                                "usage_limit_exceeded",
                                "quota_exceeded",
                            }:
                                raise QuotaExhaustedError("ChatGPT included allowance exhausted")
                            # Fail closed; no output is accepted from a failed stream.
                            raise MalformedProviderResponseError(
                                "subscription stream did not complete"
                            )
        except QuotaExhaustedError as exc:
            error = exc
        except httpx.HTTPError as exc:
            error = translate_http_error(exc, provider=self.name, model=model)
        except (ValueError, TypeError, AttributeError):
            error = MalformedProviderResponseError("invalid subscription event stream")
        finally:
            if self.client is None:
                await client.aclose()
        if isinstance(error, QuotaExhaustedError):
            self.capacity.exhaust(self.grant)
        if error is not None:
            raise error
        if not isinstance(completed, dict) or completed.get("status") != "completed":
            raise MalformedProviderResponseError("missing completed subscription response")
        from app.model_providers.openai_compatible import _contains_credential

        if any(
            _contains_credential(completed, secret.get_secret_value())
            for secret in (
                self.session.access_token,
                self.session.refresh_token,
                self.session.id_token,
            )
            if secret.get_secret_value()
        ):
            raise MalformedProviderResponseError("subscription response contains a credential")
        try:
            text = "".join(
                part["text"]
                for item in completed["output"]
                if item.get("type") == "message"
                for part in item["content"]
                if part.get("type") == "output_text"
            )
            returned_model = completed.get("model", model)
            if returned_model not in self.grant.models or not text:
                raise ValueError("invalid model or output")
            usage = completed.get("usage") or {}
            return ModelExecutionResponse(
                content=text,
                provider=self.name,
                model=returned_model,
                input_tokens=checked_usage(usage.get("input_tokens")),
                output_tokens=checked_usage(usage.get("output_tokens")),
                latency_ms=(perf_counter() - started) * 1000,
                task_id=request.task_id,
                correlation_id=request.correlation_id,
                finish_reason="completed",
                estimated_cost_usd=0,
            )
        except (ValueError, KeyError, TypeError, AttributeError, ValidationError):
            error = MalformedProviderResponseError("invalid completed subscription response")
        raise error


class CodexProvider(ProviderBase):
    provider_type = ProviderType.CODEX
    is_local = False  # A local CLI uses hosted inference, not a local model.
    capabilities = frozenset({ModelCapability.CHAT, ModelCapability.TEXT_GENERATION})

    def __init__(
        self,
        grant: FreeAllowance,
        capacity: FreeCapacityRepository,
        *,
        transport: Any = None,
    ) -> None:
        if grant.integration != "codex":
            raise AuthenticationError("Codex requires an included subscription allowance")
        self.grant = grant
        self.capacity = capacity
        self.name = grant.provider
        self.default_model = sorted(grant.models)[0]
        self.transport = transport

    def free_eligible(self, model: str) -> bool:
        return self.capacity.available(self.grant, model)

    async def model_available(self, model: str) -> bool:
        return self.free_eligible(model)

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.name,
            healthy=self.free_eligible(self.default_model),
            status=HealthStatus.CONFIGURATION_ONLY,
            latency_ms=0,
        )

    async def execute(self, request: ModelExecutionRequest) -> ModelExecutionResponse:
        from app.model_providers.codex_transport import CodexStdioTransport

        model = request.model or self.default_model
        require_allowance(self.grant, model)
        if request.tools:
            raise InvalidModelRequestError("Codex adapter has no workspace/tool authority")
        transport = self.transport or CodexStdioTransport()
        self.capacity.reserve(self.grant, model)
        started = perf_counter()
        try:
            result = await transport.infer(request, self.grant)
        except QuotaExhaustedError:
            self.capacity.exhaust(self.grant)
            raise
        except TimeoutError:
            error = RequestTimeoutError("Codex inference timed out")
        except OSError:
            error = ProviderUnavailableError("Codex CLI or configuration is unavailable")
        except (ValueError, KeyError, TypeError):
            error = MalformedProviderResponseError("Codex protocol or configuration is malformed")
        else:
            error = None
        if error is not None:
            raise error
        if isinstance(result, str):
            result = {"content": result}
        return ModelExecutionResponse(
            content=result["content"],
            input_tokens=checked_usage(result.get("input_tokens")),
            output_tokens=checked_usage(result.get("output_tokens")),
            provider=self.name,
            model=model,
            latency_ms=(perf_counter() - started) * 1000,
            task_id=request.task_id,
            correlation_id=request.correlation_id,
            finish_reason="completed",
            estimated_cost_usd=0,
        )
