"""Operator-configured repository policy; no model-supplied paths or shell access."""

import json
import re
from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.errors import DomainError
from app.tool_execution.filesystem import check_stat


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class RepositoryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    repository_identity: str = Field(
        pattern=r"^github\.com/[a-z0-9][a-z0-9_-]{0,99}/[a-z0-9][a-z0-9._-]{0,99}$"
    )
    primary_root: str = Field(min_length=1, max_length=1000)
    worktree_root: str = Field(min_length=1, max_length=1000)
    base_branch: str = Field(default="main", min_length=1, max_length=120)

    @field_validator("base_branch")
    @classmethod
    def branch_name(cls, value):
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]*", value)
            or ".." in value
            or "//" in value
            or "@{" in value
            or value.endswith(("/", ".", ".lock"))
            or any(part.startswith(".") or part.endswith(".lock") for part in value.split("/"))
        ):
            raise ValueError("invalid base branch")
        return value

    def checked(self):
        roots = [Path(self.primary_root), Path(self.worktree_root)]
        for path in roots:
            if not path.is_absolute() or path == Path(path.anchor) or ".." in path.parts:
                raise ValueError("dedicated absolute roots required")
            # lstat every ancestor before resolve: do not silently trust junction aliases.
            for parent in [*reversed(path.parents), path]:
                check_stat(parent.lstat(), directory=True, internal=True)
        primary, worktrees = [path.resolve(strict=True) for path in roots]
        if primary == worktrees or primary in worktrees.parents or worktrees in primary.parents:
            raise ValueError("primary and worktree roots must be disjoint")
        return self.model_copy(
            update={"primary_root": str(primary), "worktree_root": str(worktrees)}
        )


class RepositoryPolicies:
    def __init__(self, settings):
        self.settings = settings

    def get(self, repository_id):
        if not self.settings.self_build_enabled:
            raise DomainError("SELF_BUILD_DISABLED", "Self-Build is disabled.", 409)
        try:
            entries = json.loads(self.settings.self_build_repositories_json)
            if not isinstance(entries, dict) or len(entries) > 16:
                raise ValueError("invalid registry")
            policies = {}
            roots, identities = set(), set()
            for alias, value in entries.items():
                if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,119}", alias):
                    raise ValueError("invalid alias")
                policy = RepositoryPolicy.model_validate(value).checked()
                for root in (policy.primary_root, policy.worktree_root):
                    candidate = Path(root)
                    if any(
                        candidate == other
                        or candidate in other.parents
                        or other in candidate.parents
                        for other in roots
                    ):
                        raise ValueError("overlapping registry roots")
                    roots.add(candidate)
                if policy.repository_identity in identities:
                    raise ValueError("duplicate repository identity")
                identities.add(policy.repository_identity)
                policies[alias] = policy
            if repository_id not in policies:
                raise DomainError(
                    "SELF_BUILD_REPOSITORY_NOT_FOUND", "Repository is not registered.", 404
                )
            return policies[repository_id]
        except (ValueError, TypeError, OSError, DomainError) as error:
            if isinstance(error, DomainError) and error.code == "SELF_BUILD_REPOSITORY_NOT_FOUND":
                raise
            raise DomainError(
                "SELF_BUILD_POLICY_INVALID", "Repository policy is invalid or unsafe.", 409
            ) from None
