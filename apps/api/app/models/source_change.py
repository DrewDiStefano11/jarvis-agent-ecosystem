"""Immutable source proposals. These contracts confer no filesystem authority."""

import re
import unicodedata
from hashlib import sha256
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.models.development_validation import Commit, Contract, Digest, checked_path
from app.models.self_build import Identifier
from app.tool_execution.filesystem import relative_parts, repository_parts

MAX_FILES = 64
MAX_FILE_BYTES = 65536
MAX_TOTAL_BYTES = 1048576
RegularMode = Literal["100644", "100755"]


def canonical_path(value):
    """Require a canonical spelling; never silently normalize reviewed authority."""
    checked_path(value)
    relative_parts(value)
    if unicodedata.normalize("NFC", value) != value or any(
        unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in value
    ):
        raise ValueError("SOURCE_PATH_INVALID")
    if any(re.search(r"~[0-9]", part) for part in value.split("/")):
        raise ValueError("SOURCE_PATH_ALIAS")
    return value


def source_path(value):
    canonical_path(value)
    repository_parts(value)
    return value


def canonical_entries(values):
    """Reject leaf and ancestor aliases, even when running on a case-sensitive OS."""
    spellings = {}
    leaves = set()
    for value in values:
        if value.path in leaves:
            raise ValueError("SOURCE_PATH_COLLISION")
        leaves.add(value.path)
        parts = value.path.split("/")
        for depth in range(1, len(parts) + 1):
            prefix = "/".join(parts[:depth])
            folded = prefix.casefold()
            if folded in spellings and spellings[folded] != prefix:
                raise ValueError("SOURCE_PATH_COLLISION")
            spellings[folded] = prefix
    return tuple(sorted(values, key=lambda entry: entry.path))


class FileIdentity(Contract):
    digest: Digest
    byte_count: int = Field(ge=0, le=MAX_FILE_BYTES, strict=True)
    mode: RegularMode


class SourceContent(FileIdentity):
    # Preserve exact UTF-8 bytes (including CRLF and final-newline state). Never scrub
    # source before hashing. Content is deliberately omitted from repr and previews.
    text: str = Field(max_length=MAX_FILE_BYTES, repr=False)

    @model_validator(mode="after")
    def exact_content(self):
        try:
            data = self.text.encode("utf-8")
        except UnicodeError:
            raise ValueError("SOURCE_ENCODING_INVALID") from None
        if b"\x00" in data:
            raise ValueError("SOURCE_ENCODING_INVALID")
        if len(data) > MAX_FILE_BYTES:
            raise ValueError("SOURCE_FILE_LIMIT")
        if self.byte_count != len(data) or self.digest != sha256(data).hexdigest():
            raise ValueError("SOURCE_CONTENT_MISMATCH")
        return self


class SourceIdentity(Contract):
    repository_identity: str = Field(
        pattern=r"^github\.com/[a-z0-9][a-z0-9_-]{0,99}/[a-z0-9][a-z0-9._-]{0,99}$"
    )
    head_sha: Commit
    base_sha: Commit
    # Digest of the complete bounded observation, including relevant untracked state.
    observation_digest: Digest


class PlanReferences(Contract):
    original_plan_hash: Digest
    authorization_reference: Identifier
    # A provenance reference is never operator approval of the resulting edit plan.


class SourceChange(Contract):
    operation: Literal["add", "modify", "delete"]
    path: str
    before: FileIdentity | None
    after: SourceContent | None

    _path = field_validator("path", mode="before")(source_path)

    @model_validator(mode="after")
    def operation_shape(self):
        if (
            (self.operation == "add" and (self.before is not None or self.after is None))
            or (self.operation == "delete" and (self.before is None or self.after is not None))
            or (self.operation == "modify" and (self.before is None or self.after is None))
        ):
            raise ValueError("SOURCE_OPERATION_INVALID")
        if (
            self.before is not None
            and self.after is not None
            and self.before == FileIdentity(**self.after.model_dump(exclude={"text"}))
        ):
            raise ValueError("SOURCE_NO_CHANGE")
        return self


class SourceChangeIntent(Contract):
    schema_version: Literal["1.0"] = "1.0"
    source: SourceIdentity
    references: PlanReferences
    changes: tuple[SourceChange, ...] = Field(min_length=1, max_length=MAX_FILES)

    _changes = field_validator("changes")(canonical_entries)

    @model_validator(mode="after")
    def aggregate(self):
        if (
            sum(
                (change.before.byte_count if change.before else 0)
                + (change.after.byte_count if change.after else 0)
                for change in self.changes
            )
            > MAX_TOTAL_BYTES
        ):
            raise ValueError("SOURCE_TOTAL_LIMIT")
        paths = {change.path.casefold() for change in self.changes}
        if any(
            "/".join(change.path.casefold().split("/")[:depth]) in paths
            for change in self.changes
            for depth in range(1, len(change.path.split("/")))
        ):
            raise ValueError("SOURCE_PATH_CONFLICT")
        return self


class Topology(Contract):
    kind: Literal["file", "directory", "absent", "symlink", "special"]
    reparse_point: bool = Field(default=False, strict=True)
    link_count: int = Field(default=1, ge=0, strict=True)


class SourceEntry(Topology):
    path: str
    content: SourceContent | None = None

    _path = field_validator("path", mode="before")(canonical_path)

    @model_validator(mode="after")
    def content_shape(self):
        if self.kind != "file" and self.content is not None:
            raise ValueError("SOURCE_OBSERVATION_INVALID")
        return self


class SourceObservation(Contract):
    """Trusted caller evidence, never a model's assertion of path safety.

    Root includes checked ancestor safety. Entries include ALL immediate siblings
    at each affected directory level, plus explicit absence at new leaves. This
    package cannot establish that completeness or close a filesystem race.
    """

    repository_identity: str = Field(
        pattern=r"^github\.com/[a-z0-9][a-z0-9_-]{0,99}/[a-z0-9][a-z0-9._-]{0,99}$"
    )
    head_sha: Commit
    base_sha: Commit
    root: Topology
    entries: tuple[SourceEntry, ...] = Field(max_length=4096)

    _entries = field_validator("entries")(canonical_entries)

    @model_validator(mode="after")
    def aggregate(self):
        if (
            sum(entry.content.byte_count for entry in self.entries if entry.content)
            > MAX_TOTAL_BYTES
        ):
            raise ValueError("SOURCE_TOTAL_LIMIT")
        return self


class ChangePreview(Contract):
    # Metadata-only deterministic diff; no raw filenames/content in public preview.
    path_digest: Digest
    operation: Literal["add", "modify", "delete"]
    before: FileIdentity | None
    after: FileIdentity | None
    change_fingerprint: Digest


class SourceChangePlan(SourceChangeIntent):
    policy_digest: Digest
    change_fingerprint: Digest
    preview: tuple[ChangePreview, ...] = Field(min_length=1, max_length=MAX_FILES)
    plan_hash: Digest
    mutation_supported: Literal[False] = False
