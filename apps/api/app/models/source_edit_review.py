"""Protected source-text review is a proposal view, never an execution grant."""

from typing import Literal

from pydantic import Field, field_validator

from app.models.development_validation import Contract, Digest
from app.models.source_change import MAX_FILES, FileIdentity, PlanReferences, source_path

MAX_FILE_LINES = 4096
MAX_REVIEW_LINES = 8192


class ReviewLine(Contract):
    kind: Literal["context", "remove", "add"]
    # Preserve exact UTF-8 text including CRLF and missing final newline. Do not
    # include code in debug repr or validation errors.
    text: str = Field(min_length=1, max_length=65536, repr=False)


class FileReview(Contract):
    path: str
    operation: Literal["add", "modify", "delete"]
    before: FileIdentity | None
    after: FileIdentity | None
    before_line_count: int = Field(ge=0, le=MAX_FILE_LINES, strict=True)
    after_line_count: int = Field(ge=0, le=MAX_FILE_LINES, strict=True)
    before_final_newline: bool | None = Field(strict=True)
    after_final_newline: bool | None = Field(strict=True)
    old_start: int = Field(ge=0, le=MAX_FILE_LINES, strict=True)
    new_start: int = Field(ge=0, le=MAX_FILE_LINES, strict=True)
    old_count: int = Field(ge=0, le=MAX_FILE_LINES, strict=True)
    new_count: int = Field(ge=0, le=MAX_FILE_LINES, strict=True)
    lines: tuple[ReviewLine, ...] = Field(max_length=MAX_REVIEW_LINES, repr=False)

    _path = field_validator("path", mode="before")(source_path)


class SourceReview(Contract):
    schema_version: Literal["1.0"] = "1.0"
    review_policy_digest: Digest
    source_plan_hash: Digest
    references: PlanReferences
    files: tuple[FileReview, ...] = Field(min_length=1, max_length=MAX_FILES, repr=False)
    review_fingerprint: Digest
    current_source_measured: Literal[False] = False
    execution_authorized: Literal[False] = False


class EditingReview(Contract):
    editing_record_id: Digest
    editing_plan_hash: Digest
    review: SourceReview
    execution_authorized: Literal[False] = False
