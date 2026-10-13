"""Exact prospective Git commits. A proposed plan never grants execution authority."""

import re
from hashlib import sha1, sha256
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.models.git_changes import FileChange
from app.models.self_build import Commit, Contract, Digest, Identifier
from app.models.source_change import PlanReferences, source_path
from app.self_build.policy import digest


class CommitPerson(Contract):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=200)
    timestamp: int = Field(ge=946684800, le=4102444800, strict=True)
    timezone: str = Field(pattern=r"^[+-][0-9]{4}$")

    @model_validator(mode="after")
    def native_identity(self):
        if (
            self.name != self.name.strip()
            or any(ord(char) < 32 or char in "<>" for char in self.name)
            or not re.fullmatch(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+", self.email)
        ):
            raise ValueError("bounded single-line Git identity required")
        hours, minutes = int(self.timezone[1:3]), int(self.timezone[3:])
        if minutes > 59 or hours * 60 + minutes > 14 * 60:
            raise ValueError("invalid Git identity timezone")
        return self


class CommitMetadata(Contract):
    author: CommitPerson
    committer: CommitPerson
    message: str = Field(min_length=2, max_length=8192, repr=False)

    @field_validator("message")
    @classmethod
    def exact_message(cls, value):
        if (
            len(value.encode("utf-8")) > 8192
            or not value.endswith("\n")
            or not value.strip()
            or "\0" in value
            or "\r" in value
        ):
            raise ValueError("bounded exact UTF-8 commit message ending in LF required")
        return value

    def payload(self, tree, parent):
        def person(value):
            return f"{value.name} <{value.email}> {value.timestamp} {value.timezone}"

        return (
            f"tree {tree}\nparent {parent}\nauthor {person(self.author)}\n"
            f"committer {person(self.committer)}\n\n{self.message}"
        ).encode()


class CommitLineage(Contract):
    actor_id: Identifier
    runtime_run_id: Identifier
    attempt_id: Identifier
    worker_id: Identifier
    # References are immutable native evidence identities, never authority strings.
    source_edit_record_id: Identifier
    source_edit_checkpoint_id: Identifier | None = None
    source_edit_plan_hash: Digest
    original_references: PlanReferences


class CommitPreparationPlan(Contract):
    schema_version: Literal["1.0"] = "1.0"
    operation: Literal["git.commit.prepare"] = "git.commit.prepare"
    state: Literal["proposed"] = "proposed"
    workspace_id: Identifier
    creation_operation_id: Identifier
    creation_plan_hash: Digest
    ownership_digest: Digest
    repository_identity: str
    branch: str
    original_base_sha: Commit
    parent_sha: Commit
    observation_fingerprint: Digest
    verification_policy_digest: Digest
    preparation_policy_digest: Digest
    tool_sha256: Digest
    lineage: CommitLineage
    files: tuple[FileChange, ...] = Field(min_length=1, max_length=64)
    metadata: CommitMetadata
    prospective_tree_sha: Commit
    prospective_commit_sha: Commit
    commit_payload_sha256: Digest
    maximum_output_bytes: Literal[65536] = 65536
    commit_creation_supported: Literal[False] = False
    plan_hash: Digest

    @model_validator(mode="after")
    def exact_proposal(self):
        raw = self.metadata.payload(self.prospective_tree_sha, self.parent_sha)
        commit_sha = sha1(
            b"commit " + str(len(raw)).encode() + b"\0" + raw, usedforsecurity=False
        ).hexdigest()
        if (
            self.prospective_commit_sha != commit_sha
            or self.commit_payload_sha256 != sha256(raw).hexdigest()
        ):
            raise ValueError("prospective commit differs from exact metadata/tree/parent")
        if (
            not re.fullmatch(r"jarvis-[a-f0-9]{32}", self.workspace_id)
            or self.branch != "codex/" + self.workspace_id
            or self.parent_sha != self.original_base_sha
        ):
            raise ValueError("only the original generated development branch is supported")
        paths = [file.path for file in self.files]
        for path in paths:
            source_path(path)
        if paths != sorted(paths) or len(set(map(str.casefold, paths))) != len(paths):
            raise ValueError("exact deterministic file selection required")
        if self.plan_hash != digest(self.model_dump(mode="json", exclude={"plan_hash"})):
            raise ValueError("commit preparation plan hash differs from its exact payload")
        return self


class ApproveCommitPreparation(Contract):
    workspace_id: Identifier
    expected_plan_hash: Digest
    valid_for_seconds: int = Field(default=900, ge=1, le=3600, strict=True)


class PrepareCommitRequest(Contract):
    expected_plan_hash: Digest
    approval_id: Identifier
    worker_id: Identifier
    lease_token: str = Field(min_length=1, max_length=200, repr=False)
