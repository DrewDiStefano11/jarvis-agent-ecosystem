"""Native wire mapping and validation only. No tool dispatch or authorization."""

from __future__ import annotations

import json

import httpx
from pydantic import ValidationError

from app.model_providers.contracts import (
    ModelCapability,
    ModelExecutionRequest,
    ModelExecutionResponse,
)
from app.model_providers.errors import InvalidModelRequestError, MalformedProviderResponseError
from app.models.model_tools import MAX_ARGUMENT_BYTES, ModelToolRequest, tool_call_identity

MAX_TOOL_WIRE_BYTES = 262_144


async def post_model_response(
    client: httpx.AsyncClient,
    path: str,
    *,
    tools_enabled: bool,
    provider: str,
    model: str,
    **kwargs,
) -> httpx.Response:
    if not tools_enabled:
        return await client.post(path, **kwargs)
    # Read decompressed chunks with a hard cap before JSON parsing/buffering.
    data = bytearray()
    async with client.stream("POST", path, **kwargs) as response:
        async for chunk in response.aiter_bytes(chunk_size=16_384):
            if len(data) + len(chunk) > MAX_TOOL_WIRE_BYTES:
                raise MalformedProviderResponseError(
                    "provider tool response exceeds the wire byte limit",
                    provider=provider,
                    model=model,
                )
            data.extend(chunk)
        headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() not in {"content-encoding", "content-length"}
        }
        return httpx.Response(
            response.status_code,
            headers=headers,
            content=bytes(data),
            request=response.request,
        )


def validate_tool_support(
    request: ModelExecutionRequest, capabilities: frozenset, provider: str
) -> None:
    if request.tools and ModelCapability.TOOL_CALLING not in capabilities:
        raise InvalidModelRequestError(
            "provider has not declared native tool calling", provider=provider
        )


def _unique_object(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON property")
        result[key] = value
    return result


def reject_json_constant(value: str) -> None:
    raise ValueError("non-finite JSON number")


def decode_model_response(response: httpx.Response, *, tools_enabled: bool) -> object:
    if not tools_enabled:
        return response.json()
    if len(response.content) > MAX_TOOL_WIRE_BYTES:
        raise ValueError("tool response wire byte limit exceeded")
    return json.loads(
        response.content, object_pairs_hook=_unique_object, parse_constant=reject_json_constant
    )


def parse_native_calls(
    raw: object, *, request: ModelExecutionRequest, provider: str, model: str, compatible: bool
) -> list[ModelToolRequest]:
    error = None
    try:
        if raw is None:
            return []
        if not isinstance(raw, list) or len(raw) > request.maximum_tool_calls:
            raise ValueError("invalid tool call list")
        if raw and not request.tools:
            raise ValueError("unsolicited tool call")
        definitions = {item.name: item for item in request.tools}
        calls = []
        provider_ids = set()
        for ordinal, item in enumerate(raw):
            if not isinstance(item, dict) or set(item) - {"type", "function", "id"}:
                raise ValueError("invalid native call")
            if compatible:
                if item.get("type") != "function" or not isinstance(item.get("id"), str):
                    raise ValueError("compatible call requires function type and ID")
            elif item.get("type", "function") != "function" or "id" in item:
                raise ValueError("unsupported Ollama call type")
            function = item.get("function")
            allowed = {"name", "arguments"} if compatible else {"name", "arguments", "index"}
            if not isinstance(function, dict) or set(function) - allowed:
                raise ValueError("invalid native function")
            if "index" in function and (
                type(function["index"]) is not int or function["index"] < 0
            ):
                raise ValueError("invalid Ollama function index")
            name = function.get("name")
            if not isinstance(name, str) or name not in definitions:
                raise ValueError("unknown requested tool")
            arguments = function.get("arguments")
            if compatible:
                if not isinstance(arguments, str) or len(arguments.encode()) > MAX_ARGUMENT_BYTES:
                    raise ValueError("invalid argument JSON")
                arguments = json.loads(
                    arguments, object_pairs_hook=_unique_object, parse_constant=reject_json_constant
                )
            if not isinstance(arguments, dict):
                raise ValueError("tool arguments must be an object")
            definitions[name].validate_arguments(arguments)
            provider_id = item.get("id")
            if provider_id is not None:
                if provider_id in provider_ids:
                    raise ValueError("duplicate provider tool call ID")
                provider_ids.add(provider_id)
            calls.append(
                ModelToolRequest(
                    call_id=tool_call_identity(
                        request_id=request.tool_request_id,
                        provider=provider,
                        model=model,
                        ordinal=ordinal,
                        name=name,
                        arguments=arguments,
                    ),
                    ordinal=ordinal,
                    name=name,
                    arguments=arguments,
                    provider_call_id=provider_id,
                )
            )
        return calls
    except (ValueError, TypeError, RecursionError, ValidationError):
        error = MalformedProviderResponseError(
            "provider returned invalid structured tool calls", provider=provider, model=model
        )
    # Raise outside the handler so untrusted arguments never survive in exception chains.
    raise error


def validate_tool_response(
    response: ModelExecutionResponse, request: ModelExecutionRequest
) -> None:
    error = None
    try:
        if response.tool_request_id != request.tool_request_id:
            raise ValueError("tool request identity mismatch")
        ModelExecutionResponse.model_validate(response.model_dump())
        if len(response.tool_requests) > request.maximum_tool_calls:
            raise ValueError("tool call limit exceeded")
        definitions = {item.name: item for item in request.tools}
        for ordinal, call in enumerate(response.tool_requests):
            definitions[call.name].validate_arguments(call.arguments)
            expected = tool_call_identity(
                request_id=request.tool_request_id,
                provider=response.provider,
                model=response.model,
                ordinal=ordinal,
                name=call.name,
                arguments=call.arguments,
            )
            if call.ordinal != ordinal or call.call_id != expected:
                raise ValueError("tool call identity mismatch")
    except (ValueError, KeyError, TypeError, RecursionError):
        error = MalformedProviderResponseError(
            "provider tool calls violate the routed request",
            provider=response.provider,
            model=response.model,
        )
    if error is not None:
        raise error
