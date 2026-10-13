"""Bounded model proposals and protected native context; neither grants authority."""

from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.models.development_validation import CodeState, Contract, Digest
from app.models.self_build import Identifier
from app.models.source_change import (
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_TOTAL_BYTES,
    SourceObservation,
    canonical_entries,
    source_path,
)


class ContextSelection(Contract):
    paths: tuple[str, ...] = Field(min_length=1, max_length=MAX_FILES)

    @field_validator("paths")
    @classmethod
    def canonical_paths(cls, values):
        for path in values:
            source_path(path)
        if len({path.casefold() for path in values}) != len(values):
            raise ValueError("DEVELOPMENT_PATH_COLLISION")
        return tuple(sorted(values))


class RepositoryContext(Contract):
    schema_version: Literal["1.0"] = "1.0"
    task_id: Identifier
    runtime_run_id: Identifier
    runtime_attempt_id: Identifier
    workspace_id: Identifier
    creation_operation_id: Identifier
    creation_plan_hash: Digest
    native_inventory_digest: Digest
    selection: ContextSelection
    observation: SourceObservation = Field(repr=False)
    code_state: CodeState
    # Complete topology, selected contents only. Other files require another read.
    content_scope: Literal["selected_files"] = "selected_files"
    context_hash: Digest


class ProposedFile(Contract):
    operation: Literal["add", "modify", "delete"]
    path: str
    before_digest: Digest | None
    text: str | None = Field(max_length=MAX_FILE_BYTES, repr=False)

    _path = field_validator("path", mode="before")(source_path)

    @model_validator(mode="after")
    def operation_shape(self):
        if (
            (self.operation == "add" and (self.before_digest is not None or self.text is None))
            or (self.operation == "modify" and (self.before_digest is None or self.text is None))
            or (
                self.operation == "delete" and (self.before_digest is None or self.text is not None)
            )
        ):
            raise ValueError("DEVELOPMENT_OPERATION_INVALID")
        if self.text is not None:
            try:
                raw = self.text.encode("utf-8")
            except UnicodeError:
                raise ValueError("DEVELOPMENT_ENCODING_INVALID") from None
            if len(raw) > MAX_FILE_BYTES or b"\x00" in raw:
                raise ValueError("DEVELOPMENT_CONTENT_LIMIT")
        return self


class DevelopmentProposal(Contract):
    schema_version: Literal["1.0"] = "1.0"
    context_hash: Digest
    summary: str = Field(min_length=1, max_length=2000, repr=False)
    completion_criteria: tuple[str, ...] = Field(min_length=1, max_length=16, repr=False)
    files: tuple[ProposedFile, ...] = Field(min_length=1, max_length=MAX_FILES, repr=False)

    _files = field_validator("files")(canonical_entries)

    @field_validator("summary")
    @classmethod
    def meaningful_summary(cls, value):
        if not value.strip():
            raise ValueError("DEVELOPMENT_SUMMARY_INVALID")
        return value

    @field_validator("completion_criteria")
    @classmethod
    def meaningful_criteria(cls, values):
        if any(not value.strip() or len(value) > 1000 for value in values):
            raise ValueError("DEVELOPMENT_CRITERIA_INVALID")
        return values

    @model_validator(mode="after")
    def total(self):
        if (
            sum(len(item.text.encode("utf-8")) for item in self.files if item.text)
            > MAX_TOTAL_BYTES
        ):
            raise ValueError("DEVELOPMENT_CONTENT_LIMIT")
        return self
