"""Measured change evidence; descriptions and fingerprints grant no authority."""

from typing import Literal

from pydantic import Field, field_validator, model_serializer, model_validator

from app.models.self_build import Commit, Contract, Digest, Identifier
from app.models.source_change import canonical_path, source_path
from app.self_build.policy import digest


class FileIdentity(Contract):
    mode: Literal["100644", "100755"]
    blob_sha: Commit
    sha256: Digest
    byte_count: int = Field(ge=0, le=8388608)


class FileChange(Contract):
    path: str
    kind: Literal["added", "modified", "deleted"]
    before: FileIdentity | None
    after: FileIdentity | None
    tracked: bool

    @model_validator(mode="after")
    def coherent(self):
        source_path(self.path)
        expected = (
            "added" if self.before is None else "deleted" if self.after is None else "modified"
        )
        if self.kind != expected or self.before == self.after:
            raise ValueError("change requires distinct measured identities")
        return self


class InventoryFile(Contract):
    path: str
    identity: FileIdentity

    _path = field_validator("path")(source_path)


class IndexFile(Contract):
    path: str
    mode: Literal["100644", "100755"]
    blob_sha: Commit

    _path = field_validator("path")(source_path)


class NativeArtifact(Contract):
    path: str = Field(max_length=512)
    kind: Literal["git_pointer", "workspace_marker", "source_backup"]
    sha256: Digest
    byte_count: int = Field(ge=0, le=8388608)
    provenance_digest: Digest
    device: int | None = Field(default=None, ge=0, strict=True)
    inode: int | None = Field(default=None, ge=1, strict=True)

    @model_serializer(mode="wrap")
    def original_shape(self, handler):
        value = handler(self)
        if self.device is None and self.inode is None:
            value.pop("device", None)
            value.pop("inode", None)
        return value

    @property
    def parts(self):
        # The model validates ordinary parents and the derived leaf separately.
        return tuple(self.path.split("/"))

    @model_validator(mode="after")
    def exact_path(self):
        import re

        if (self.device is None) != (self.inode is None):
            raise ValueError("native identity requires both device and inode")
        if self.kind != "source_backup" and self.device is not None:
            raise ValueError("only source backups carry checkpointed identity")
        segments = self.parts
        if self.kind == "source_backup":
            canonical_path(segments[-1])
        else:
            canonical_path(self.path)
        exact = {
            "git_pointer": ".git",
            "workspace_marker": ".jarvis-workspace.json",
        }
        if self.kind in exact and self.path != exact[self.kind]:
            raise ValueError("native artifact must use its exact root path")
        if self.kind == "source_backup" and (
            not segments or not re.fullmatch(r"\.jarvis-edit-[a-f0-9]{64}\.before", segments[-1])
        ):
            raise ValueError("source backup requires a deterministic native path")
        if len(segments) > 16:
            raise ValueError("native artifact exceeds path depth")
        if len(segments) > 1:
            source_path("/".join(segments[:-1]) + "/x")
            if any(part.casefold().startswith(".jarvis-") for part in segments[:-1]):
                raise ValueError("native backup parents must remain ordinary source directories")
        return self


class GitChangeObservation(Contract):
    schema_version: Literal["1.0", "1.1"] = "1.0"
    workspace_id: Identifier
    creation_operation_id: Identifier
    creation_plan_hash: Digest
    ownership_digest: Digest
    repository_identity: str
    branch: str
    original_base_sha: Commit
    head_sha: Commit
    original_tree_sha: Commit
    tool_sha256: Digest
    verification_policy_digest: Digest
    # Windows has no Unix executable-bit measurement. Modes come from the
    # original tree/index; added files use the fixed regular-file mode.
    mode_source: Literal["git_index_windows"] = "git_index_windows"
    index_clean: bool
    worktree_clean: bool
    tracked_file_count: int = Field(ge=0, le=4096)
    untracked_file_count: int = Field(ge=0, le=4096)
    inventory_digest: Digest
    index_digest: Digest
    original_inventory: tuple[InventoryFile, ...] = Field(max_length=4096)
    current_inventory: tuple[InventoryFile, ...] = Field(max_length=4096)
    index_inventory: tuple[IndexFile, ...] = Field(max_length=4096)
    internal_inventory: tuple[NativeArtifact, ...] = Field(default=(), max_length=66)
    changes: tuple[FileChange, ...] = Field(max_length=4096)
    change_fingerprint: Digest
    commit_preview_fingerprint: Digest

    @model_serializer(mode="wrap")
    def original_shape(self, handler):
        value = handler(self)
        if self.schema_version == "1.0":
            value.pop("internal_inventory", None)
        return value

    @model_validator(mode="after")
    def fingerprints(self):
        if self.schema_version == "1.0" and self.internal_inventory:
            raise ValueError("native artifacts require observation schema 1.1")
        internal = [item.path for item in self.internal_inventory]
        if internal != sorted(internal) or len(set(map(str.casefold, internal))) != len(internal):
            raise ValueError("native artifact inventory must be unique and ordered")
        for inventory in (self.original_inventory, self.current_inventory, self.index_inventory):
            names = [item.path for item in inventory]
            if names != sorted(names) or len(set(map(str.casefold, names))) != len(names):
                raise ValueError("inventories require unique deterministic paths")
        before = {item.path: item.identity for item in self.original_inventory}
        after = {item.path: item.identity for item in self.current_inventory}
        indexed = [[item.mode, item.blob_sha, item.path] for item in self.index_inventory]
        original_index = [
            [item.identity.mode, item.identity.blob_sha, item.path]
            for item in self.original_inventory
        ]
        tracked = {item.path for item in self.index_inventory}
        expected_changes = []
        for path in sorted(before.keys() | after.keys()):
            left, right = before.get(path), after.get(path)
            if left != right:
                expected_changes.append(
                    FileChange(
                        path=path,
                        before=left,
                        after=right,
                        kind="added"
                        if left is None
                        else "deleted"
                        if right is None
                        else "modified",
                        tracked=path in before or path in tracked,
                    )
                )
        if (
            tuple(expected_changes) != self.changes
            or self.index_clean != (indexed == original_index)
            or self.tracked_file_count != len(indexed)
            or self.untracked_file_count != len(after.keys() - tracked)
            or self.index_digest != digest(indexed)
            or self.inventory_digest
            != digest(
                [[path, item.model_dump(mode="json")] for path, item in sorted(after.items())]
            )
        ):
            raise ValueError("change evidence differs from its complete inventories")
        paths = [item.path for item in self.changes]
        if paths != sorted(paths) or len(set(map(str.casefold, paths))) != len(paths):
            raise ValueError("changes require a unique deterministic path order")
        if self.branch != "codex/" + self.workspace_id or self.head_sha != self.original_base_sha:
            raise ValueError("inspection requires the original generated development branch")
        if self.worktree_clean != (not self.changes):
            raise ValueError("clean state differs from measured changes")
        payload = self.model_dump(
            mode="json", exclude={"change_fingerprint", "commit_preview_fingerprint"}
        )
        if self.change_fingerprint != digest(payload):
            raise ValueError("change fingerprint differs from evidence")
        if self.commit_preview_fingerprint != digest(
            [
                "git.commit.preview/1.0",
                self.change_fingerprint,
                self.head_sha,
                [item.model_dump(mode="json") for item in self.changes],
            ]
        ):
            raise ValueError("commit preview differs from measured changes")
        return self
