"""Proposal-only Responses API adapter; no model tools or autonomous worker fallback."""

import asyncio
import json
import math
import re
from time import perf_counter

import httpx
from pydantic import SecretStr

from app.model_providers.base import ProviderBase
from app.model_providers.contracts import (
    HealthStatus,
    ModelCapability,
    ModelExecutionRequest,
    ModelExecutionResponse,
    ModelOutputSchema,
    ProviderHealth,
    ProviderType,
    UsageQuality,
)
from app.model_providers.errors import (
    InvalidModelRequestError,
    MalformedProviderResponseError,
    ProviderConfigurationError,
    ProviderExecutionDisabledError,
    RequestTimeoutError,
)
from app.model_providers.http import translate_http_error
from app.model_providers.security import redact_secrets
from app.model_providers.tool_calls import decode_model_response, post_model_response
from app.models.development_agent import DevelopmentProposal

ENDPOINT = "https://api.openai.com/v1/responses"
SCHEMA_NAME = "development_proposal_v1"


def proposal_output_schema():
    """All fields are required for strict generation; native validation still applies."""

    def closed(node):
        if isinstance(node, list):
            return [closed(item) for item in node]
        if not isinstance(node, dict):
            return node
        result = {
            key: closed(value) for key, value in node.items() if key not in {"default", "title"}
        }
        if "properties" in result:
            result["required"] = list(result["properties"])
            result["additionalProperties"] = False
        return result

    return ModelOutputSchema(
        name=SCHEMA_NAME, json_schema=closed(DevelopmentProposal.model_json_schema())
    )


class OpenAIProposalProvider(ProviderBase):
    """A separate opt-in provider, never registered with the local planning worker."""

    provider_type = ProviderType.OPENAI_RESPONSES
    is_local = False
    capabilities = frozenset(
        {
            ModelCapability.CHAT,
            ModelCapability.CODE_GENERATION,
            ModelCapability.CODE_EDITING,
            ModelCapability.STRUCTURED_OUTPUT,
        }
    )

    def __init__(self, *, api_key, default_model, enabled=False, timeout_seconds=120, client=None):
        if (
            not isinstance(api_key, SecretStr)
            or not api_key.get_secret_value()
            or not isinstance(default_model, str)
            or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", default_model)
            or redact_secrets(default_model) != default_model
            or api_key.get_secret_value() in default_model
            or type(enabled) is not bool
            or type(timeout_seconds) not in {int, float}
            or not math.isfinite(timeout_seconds)
            or not 1 <= timeout_seconds <= 300
        ):
            raise ProviderConfigurationError(
                "Invalid development provider configuration."
            ) from None
        self.name = "openai-development"
        self.api_key = api_key
        self.default_model = default_model
        self.enabled = enabled
        self.timeout_seconds = timeout_seconds
        self._client = client

    async def health_check(self):
        # Configuration only. Does not imply live availability or qualification.
        return ProviderHealth(
            provider=self.name,
            healthy=self.enabled,
            status=HealthStatus.CONFIGURATION_ONLY,
            latency_ms=0,
            model_available=None,
            detail="Explicit development proposal configuration only.",
        )

    def _validate_request(self, request):
        if not self.enabled:
            raise ProviderExecutionDisabledError(
                "Development proposal inference is disabled.", provider=self.name
            )
        failure = None
        try:
            request = ModelExecutionRequest.model_validate(request.model_dump(warnings=False))
            if self.api_key.get_secret_value() in json.dumps(
                request.model_dump(mode="json", warnings=False), ensure_ascii=False
            ):
                raise ValueError
            if (
                len(request.messages) > 16
                or sum(len(message.content.encode("utf-8")) for message in request.messages)
                > 131072
            ):
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            failure = InvalidModelRequestError("Invalid development request.", provider=self.name)
        if failure is not None:
            raise failure from None
        if (
            request.tools
            or request.streaming
            or request.model not in {None, self.default_model}
            or request.output_schema != proposal_output_schema()
            or not request.task_id
            or not request.correlation_id
            or not re.fullmatch(r"[a-f0-9]{64}", str(request.metadata.get("context_hash", "")))
            or request.max_output_tokens is None
            or request.max_output_tokens > 16384
            or request.required_capability not in self.capabilities
            or any(message.role.value not in {"system", "user"} for message in request.messages)
            or any(
                self.api_key.get_secret_value() in message.content for message in request.messages
            )
        ):
            raise InvalidModelRequestError(
                "Only bounded task-bound development proposals are supported.", provider=self.name
            )
        return request

    async def execute(self, request):
        request = self._validate_request(request)
        started = perf_counter()
        timeout = min(self.timeout_seconds, request.timeout_seconds or self.timeout_seconds)
        failure = None
        try:
            response = await asyncio.wait_for(self._post(request, timeout), timeout=timeout)
        except TimeoutError:
            failure = RequestTimeoutError(
                "Development provider deadline expired.",
                provider=self.name,
                model=self.default_model,
                task_id=request.task_id,
                correlation_id=request.correlation_id,
            )
        if failure is not None:
            raise failure from None
        return self._normalize(response, request, (perf_counter() - started) * 1000)

    async def _post(self, request, timeout):
        created = self._client is None
        client = self._client or httpx.AsyncClient(
            timeout=timeout,
            trust_env=False,
            follow_redirects=False,
        )
        failure = None
        try:
            result = await post_model_response(
                client,
                ENDPOINT,
                tools_enabled=True,
                provider=self.name,
                model=self.default_model,
                timeout=timeout,
                follow_redirects=False,
                headers={"Authorization": "Bearer " + self.api_key.get_secret_value()},
                json={
                    "model": self.default_model,
                    "input": [
                        {"role": message.role.value, "content": message.content}
                        for message in request.messages
                    ],
                    "max_output_tokens": request.max_output_tokens,
                    "store": False,
                    "text": {
                        "format": {
                            "type": "json_schema",
                            "name": SCHEMA_NAME,
                            "strict": True,
                            "schema": request.output_schema.json_schema,
                        }
                    },
                },
            )
            result.raise_for_status()
        except httpx.HTTPError as error:
            failure = translate_http_error(
                error,
                provider=self.name,
                model=self.default_model,
                task_id=request.task_id,
                correlation_id=request.correlation_id,
            )
        finally:
            if created:
                await client.aclose()
        # Raise outside the handler so credential-bearing HTTP requests are not retained.
        if failure is not None:
            raise failure from None
        return result

    def _normalize(self, response, request, latency):
        failure = None
        try:
            raw = decode_model_response(response, tools_enabled=True)

            def safe(value, depth=0):
                if depth > 16:
                    raise ValueError
                if isinstance(value, str):
                    if self.api_key.get_secret_value() in value:
                        raise ValueError
                elif isinstance(value, dict):
                    for key, item in value.items():
                        safe(key, depth + 1)
                        safe(item, depth + 1)
                elif isinstance(value, list):
                    for item in value:
                        safe(item, depth + 1)

            safe(raw)
            if not isinstance(raw, dict) or raw.get("status") != "completed" or raw.get("error"):
                raise ValueError
            output = raw.get("output")
            if not isinstance(output, list) or not 1 <= len(output) <= 32:
                raise ValueError
            texts = []
            for item in output:
                if not isinstance(item, dict):
                    raise ValueError
                if item.get("type") == "reasoning":
                    continue
                if (
                    item.get("type") != "message"
                    or item.get("role") != "assistant"
                    or item.get("status") != "completed"
                ):
                    raise ValueError
                blocks = item.get("content")
                if not isinstance(blocks, list) or not blocks:
                    raise ValueError
                for block in blocks:
                    if (
                        not isinstance(block, dict)
                        or block.get("type") != "output_text"
                        or not isinstance(block.get("text"), str)
                    ):
                        raise ValueError
                    texts.append(block["text"])
            content = "".join(texts)
            if not content:
                raise ValueError
            proposal_data = decode_model_response(
                httpx.Response(200, content=content.encode("utf-8")), tools_enabled=True
            )
            safe(proposal_data)
            proposal = DevelopmentProposal.model_validate(proposal_data)
            if proposal.context_hash != request.metadata["context_hash"]:
                raise ValueError
            model = raw.get("model")
            response_id = raw.get("id")
            if (
                not isinstance(model, str)
                or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", model)
                or redact_secrets(model) != model
                or not isinstance(response_id, str)
                or not re.fullmatch(r"resp_[A-Za-z0-9_-]{1,190}", response_id)
            ):
                raise ValueError
            usage = raw.get("usage")
            if not isinstance(usage, dict):
                raise ValueError
            counts = [usage.get(key) for key in ("input_tokens", "output_tokens", "total_tokens")]
            if any(type(value) is not int or value < 0 for value in counts):
                raise ValueError
            if counts[2] != counts[0] + counts[1]:
                raise ValueError
            return ModelExecutionResponse(
                content=json.dumps(
                    proposal.model_dump(mode="json"),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ),
                provider=self.name,
                model=model,
                input_tokens=counts[0],
                output_tokens=counts[1],
                total_tokens=counts[2],
                usage_quality=UsageQuality.EXACT,
                latency_ms=latency,
                finish_reason="stop",
                task_id=request.task_id,
                correlation_id=request.correlation_id,
                request_id=response_id,
                provider_metadata={"response_id": response_id},
            )
        except (ValueError, TypeError, AttributeError, RecursionError):
            failure = MalformedProviderResponseError(
                "Development provider returned invalid proposal evidence.",
                provider=self.name,
                model=self.default_model,
                task_id=request.task_id,
                correlation_id=request.correlation_id,
            )
        if failure is not None:
            raise failure from None
