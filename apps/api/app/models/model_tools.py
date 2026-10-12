"""Bounded model requests and observations. None of these contracts grant authority."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

MAX_TOOL_CALLS = 8
MAX_ARGUMENT_BYTES = 16_384
MAX_RESULT_BYTES = 65_536
MAX_SCHEMA_BYTES = 16_384


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def bounded_json(value: object, maximum: int, *, depth: int = 0) -> None:
    """Reject credentials, non-JSON values, recursive/large/deep payloads."""
    from app.model_providers.security import SECRET_KEY, redact_secrets

    if depth > 8:
        raise ValueError("tool payload nesting limit exceeded")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or SECRET_KEY.search(key):
                raise ValueError("tool payload contains an unsafe key")
            bounded_json(item, maximum, depth=depth + 1)
    elif isinstance(value, list):
        for item in value:
            bounded_json(item, maximum, depth=depth + 1)
    elif isinstance(value, str):
        if redact_secrets(value) != value or "sk-proj-" in value or "sk-svcacct-" in value:
            raise ValueError("tool payload contains secret-bearing text")
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("tool payload numbers must be finite")
    elif value is not None and type(value) not in {bool, int, float}:
        raise ValueError("tool payload must use JSON types")
    if len(canonical_json(value).encode("utf-8")) > maximum:
        raise ValueError("tool payload byte limit exceeded")


class ModelToolContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


class ToolParameterSchema(ModelToolContract):
    """Deliberately small JSON Schema subset; refs/regex/combinators are unsupported.

    Closed objects with all properties required also fit provider strict mode.
    Global byte/depth bounds apply independently of these per-field constraints.
    """

    type: Literal["object", "array", "string", "integer", "number", "boolean", "null"]
    description: str | None = Field(default=None, max_length=1000)
    properties: dict[str, ToolParameterSchema] | None = Field(default=None, max_length=32)
    required: list[str] | None = Field(default=None, max_length=32)
    additionalProperties: Literal[False] | None = None
    items: ToolParameterSchema | None = None
    maxItems: int | None = Field(default=None, ge=0, le=64)
    maxLength: int | None = Field(default=None, ge=0, le=MAX_ARGUMENT_BYTES)
    enum: list[JsonValue] | None = Field(default=None, min_length=1, max_length=32)

    @field_validator("additionalProperties", mode="before")
    @classmethod
    def require_literal_false(cls, value):
        # Pydantic Literal[False] accepts numeric zero by equality, even in strict
        # mode. Do not let that coerce a malformed JSON Schema keyword.
        if value is not None and value is not False:
            raise ValueError("additionalProperties must be literal false")
        return value

    @model_validator(mode="after")
    def validate_shape(self) -> ToolParameterSchema:
        if self.type == "object":
            if (
                self.properties is None
                or self.required is None
                or self.additionalProperties is not False
            ):
                raise ValueError("tool object schemas must be closed and explicit")
            if len(set(self.required)) != len(self.required) or set(self.required) != set(
                self.properties
            ):
                raise ValueError("tool object properties must all be required")
        elif any(
            x is not None for x in (self.properties, self.required, self.additionalProperties)
        ):
            raise ValueError("object keywords require an object schema")
        if self.type == "array":
            if self.items is None or self.maxItems is None:
                raise ValueError("tool arrays require items and maxItems")
        elif self.items is not None or self.maxItems is not None:
            raise ValueError("array keywords require an array schema")
        if self.type == "string":
            if self.maxLength is None:
                raise ValueError("tool strings require maxLength")
        elif self.maxLength is not None:
            raise ValueError("maxLength requires a string schema")
        if self.enum is not None:
            for value in self.enum:
                self.validate_value(value, check_enum=False)
        return self

    def validate_value(self, value: object, *, check_enum: bool = True) -> None:
        types = {
            "object": dict,
            "array": list,
            "string": str,
            "integer": int,
            "boolean": bool,
            "null": type(None),
        }
        if self.type == "number":
            valid = type(value) is int or (type(value) is float and math.isfinite(value))
        else:
            valid = type(value) is types[self.type]
        if not valid:
            raise ValueError("tool argument type does not match schema")
        if check_enum and self.enum is not None:
            if canonical_json(value) not in {canonical_json(item) for item in self.enum}:
                raise ValueError("tool argument is outside the schema enum")
        if self.type == "object":
            if set(value) != set(self.properties):
                raise ValueError("tool arguments have missing or unknown properties")
            for key, schema in self.properties.items():
                schema.validate_value(value[key])
        elif self.type == "array":
            if len(value) > self.maxItems:
                raise ValueError("tool array exceeds maxItems")
            for item in value:
                self.items.validate_value(item)
        elif self.type == "string" and len(value) > self.maxLength:
            raise ValueError("tool string exceeds maxLength")


class ModelToolDefinition(ModelToolContract):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
    description: str = Field(min_length=1, max_length=1000)
    parameters: ToolParameterSchema

    @model_validator(mode="after")
    def validate_definition(self) -> ModelToolDefinition:
        if self.parameters.type != "object":
            raise ValueError("tool parameters must describe an object")
        bounded_json(self.model_dump(exclude_none=True), MAX_SCHEMA_BYTES)
        return self

    def validate_arguments(self, arguments: dict[str, JsonValue]) -> None:
        bounded_json(arguments, MAX_ARGUMENT_BYTES)
        self.parameters.validate_value(arguments)

    def wire_definition(self) -> dict:
        return {"type": "function", "function": self.model_dump(exclude_none=True)}


class ModelToolRequest(ModelToolContract):
    call_id: str = Field(pattern=r"^mtc_[a-f0-9]{64}$")
    ordinal: int = Field(ge=0, lt=MAX_TOOL_CALLS)
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
    arguments: dict[str, JsonValue] = Field(repr=False)
    provider_call_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.:-]{1,120}$")
    trust: Literal["model_request"] = "model_request"

    @field_validator("arguments")
    @classmethod
    def validate_arguments(cls, value):
        bounded_json(value, MAX_ARGUMENT_BYTES)
        return value


def tool_call_identity(
    *, request_id: str, provider: str, model: str, ordinal: int, name: str, arguments: dict
) -> str:
    payload = [request_id, provider, model, ordinal, name, arguments]
    return "mtc_" + hashlib.sha256(canonical_json(payload).encode()).hexdigest()


class ModelToolResult(ModelToolContract):
    """Untrusted observation reference, never an execution authorization."""

    call_id: str = Field(pattern=r"^mtc_[a-f0-9]{64}$")
    execution_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,120}$")
    status: Literal["completed", "failed", "uncertain"]
    output: JsonValue = Field(repr=False)
    trust: Literal["tool_observation"] = "tool_observation"

    @field_validator("output")
    @classmethod
    def validate_output(cls, value):
        bounded_json(value, MAX_RESULT_BYTES)
        return value
