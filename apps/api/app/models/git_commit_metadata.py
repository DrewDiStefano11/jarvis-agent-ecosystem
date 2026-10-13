"""Bounded strong metadata snapshot; no paths or model records grant authority."""

from pathlib import PurePosixPath
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.models.self_build import Contract, Digest
from app.self_build.policy import digest
from app.tool_execution.filesystem import relative_parts


class CommitMetadataNode(Contract):
    path: str = Field(min_length=1, max_length=240)
    kind: Literal["file", "directory"]
    device: int = Field(ge=0, strict=True)
    inode: int = Field(ge=0, strict=True)
    byte_count: int | None = Field(default=None, ge=0, le=67108864, strict=True)
    sha256: Digest | None = None

    @field_validator("path")
    @classmethod
    def canonical(cls, value):
        if not relative_parts(value) or "~" in value:
            raise ValueError("metadata paths must be exact ordinary relative names")
        return value

    @model_validator(mode="after")
    def exact(self):
        if (self.kind == "file") != (self.byte_count is not None and self.sha256 is not None):
            raise ValueError("only a metadata file has a byte identity")
        if self.kind == "directory" and (self.byte_count is not None or self.sha256 is not None):
            raise ValueError("metadata directories have only their native identity")
        return self


class CommitMetadataSnapshot(Contract):
    schema_version: Literal["1.0"] = "1.0"
    root_device: int = Field(ge=0, strict=True)
    root_inode: int = Field(ge=0, strict=True)
    nodes: tuple[CommitMetadataNode, ...] = Field(max_length=16384)
    snapshot_digest: Digest

    @model_validator(mode="after")
    def exact(self):
        paths = [node.path for node in self.nodes]
        by_path = {node.path: node for node in self.nodes}
        if paths != sorted(paths) or len({path.casefold() for path in paths}) != len(paths):
            raise ValueError("metadata snapshot requires a complete unique ordered namespace")
        for node in self.nodes:
            for parent in PurePosixPath(node.path).parents:
                if str(parent) != "." and (
                    str(parent) not in by_path or by_path[str(parent)].kind != "directory"
                ):
                    raise ValueError("metadata parent directories must be present and ordinary")
        if sum(node.byte_count or 0 for node in self.nodes) > 268435456:
            raise ValueError("metadata snapshot exceeds its total byte bound")
        if len(self.model_dump_json().encode()) > 2097152:
            raise ValueError("metadata snapshot exceeds its record bound")
        if self.snapshot_digest != digest(
            self.model_dump(mode="json", exclude={"snapshot_digest"})
        ):
            raise ValueError("metadata snapshot differs from its exact strong fingerprint")
        return self
