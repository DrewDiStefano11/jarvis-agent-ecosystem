"""Historical direct-child publication reference; never correction admission authority."""

from typing import Literal

from pydantic import Field, model_validator

from app.models.self_build import Commit, Contract, Digest, Identifier
from app.self_build.policy import digest


class CorrectionPublicationLink(Contract):
    schema_version: Literal["1.0"] = "1.0"
    task_id: Identifier
    original_workspace_id: Identifier
    original_publication_id: Identifier
    original_source_link_hash: Digest
    workspace_id: Identifier
    native_command_id: Identifier
    native_operation_id: Identifier
    native_plan_hash: Digest
    native_checkpoint_id: Identifier
    native_checkpoint_integrity: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    source_snapshot_digest: Digest
    repository_identity: str = Field(max_length=200)
    remote_branch: str = Field(max_length=200)
    local_branch: str = Field(max_length=200)
    pr_number: int = Field(strict=True, gt=0)
    approved_pr_base_sha: Commit
    source_base_sha: Commit
    expected_remote_sha: Commit
    commit_sha: Commit
    tree_sha: Commit
    historical_only: Literal[True] = True
    publication_authority: Literal[False] = False
    lineage_admission_verified: Literal[False] = False
    link_hash: Digest

    @model_validator(mode="after")
    def exact(self):
        from app.github_publication.transport import safe_branch

        if (
            self.workspace_id == self.original_workspace_id
            or self.source_base_sha != self.expected_remote_sha
            or self.commit_sha == self.expected_remote_sha
            or self.local_branch == self.remote_branch
            or any(
                not safe_branch(branch) or not branch.startswith("codex/")
                for branch in (self.local_branch, self.remote_branch)
            )
            or self.link_hash != digest(self.model_dump(mode="json", exclude={"link_hash"}))
        ):
            raise ValueError(
                "correction publication link requires a distinct direct-child workspace"
            )
        return self
