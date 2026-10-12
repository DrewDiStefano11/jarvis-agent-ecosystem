"""Deterministic, side-effect-free source conflict and topology validation."""

from hashlib import sha256
from pathlib import Path

from app.core.errors import DomainError
from app.development_validation.planner import POLICY_DIGEST as VALIDATION_POLICY_DIGEST
from app.development_validation.planner import digest, plan_validation
from app.models import source_change as contracts
from app.models.development_validation import CodeState, FileChange
from app.models.source_change import (
    ChangePreview,
    FileIdentity,
    SourceChangeIntent,
    SourceChangePlan,
)
from app.tool_execution import filesystem as filesystem_policy

POLICY_DIGEST = digest(
    {
        "version": "source-change-v1",
        "validation_policy_digest": VALIDATION_POLICY_DIGEST,
        "max_files": contracts.MAX_FILES,
        "max_file_bytes": contracts.MAX_FILE_BYTES,
        "max_total_bytes": contracts.MAX_TOTAL_BYTES,
        "implementation": sha256(
            Path(__file__).read_text(encoding="utf-8").encode()
            + Path(contracts.__file__).read_text(encoding="utf-8").encode()
            + Path(filesystem_policy.__file__).read_text(encoding="utf-8").encode()
        ).hexdigest(),
    }
)


def refuse(code):
    # Never echo source, paths, digests or native exception details in failures.
    raise DomainError(code, "Source proposal failed a required safety check.", 409) from None


def identity(content):
    return FileIdentity(**content.model_dump(exclude={"text"})) if content else None


def observation_identity(observation):
    return contracts.SourceIdentity(
        repository_identity=observation.repository_identity,
        head_sha=observation.head_sha,
        base_sha=observation.base_sha,
        observation_digest=digest(observation.model_dump(mode="json")),
    )


def safe_directory(node):
    return node.kind == "directory" and not node.reparse_point


def plan_source_changes(intent, observation):
    """Validate trusted immutable observations, never read or write a workspace.

    Revalidate at the boundary to reject tampered model_copy/model_construct data.
    A future adapter MUST obtain fenced native evidence and repeat verification
    immediately before side effects; accepting this proposal grants no authority.
    """
    try:
        intent = SourceChangeIntent.model_validate(intent.model_dump(mode="json", warnings=False))
        observation = contracts.SourceObservation.model_validate(
            observation.model_dump(mode="json", warnings=False)
        )
    except (ValueError, TypeError, AttributeError):
        refuse("SOURCE_CONTRACT_INVALID")
    if intent.source != observation_identity(observation):
        refuse("SOURCE_IDENTITY_CONFLICT")
    if not safe_directory(observation.root):
        refuse("SOURCE_ROOT_UNSAFE")
    entries = {entry.path: entry for entry in observation.entries}
    # Sibling aliases cannot evade exact spelling by changing case at any level.
    spellings = {}
    for path in entries:
        segments = path.split("/")
        for depth in range(1, len(segments) + 1):
            prefix = "/".join(segments[:depth])
            spellings[prefix.casefold()] = prefix
    for change in intent.changes:
        segments = change.path.split("/")
        for depth in range(1, len(segments) + 1):
            prefix = "/".join(segments[:depth])
            if prefix.casefold() in spellings and spellings[prefix.casefold()] != prefix:
                refuse("SOURCE_PATH_COLLISION")
        for depth in range(1, len(segments)):
            parent = entries.get("/".join(segments[:depth]))
            if parent is None:
                refuse("SOURCE_OBSERVATION_INCOMPLETE")
            if not safe_directory(parent):
                refuse("SOURCE_PARENT_UNSAFE")
        current = entries.get(change.path)
        if current is None:
            refuse("SOURCE_OBSERVATION_INCOMPLETE")
        if current.reparse_point or current.kind in {"symlink", "special"}:
            refuse("SOURCE_PATH_UNSAFE")
        if change.operation == "add":
            if current.kind != "absent":
                refuse("SOURCE_ALREADY_EXISTS")
        else:
            if current.kind == "absent":
                refuse("SOURCE_NOT_FOUND")
            if current.kind != "file" or current.link_count != 1:
                refuse("SOURCE_PATH_UNSAFE")
            if current.content is None:
                refuse("SOURCE_OBSERVATION_INCOMPLETE")
            if identity(current.content) != change.before:
                refuse("SOURCE_BEFORE_CONFLICT")
    # Reuse the production validation policy for denied outputs and credential paths.
    state = CodeState(
        repository_identity=intent.source.repository_identity,
        head_sha=intent.source.head_sha,
        base_sha=intent.source.base_sha,
        changes=tuple(
            FileChange(
                path=change.path,
                before_hash=change.before.digest if change.before else None,
                before_mode=change.before.mode if change.before else None,
                after_hash=change.after.digest if change.after else None,
                after_mode=change.after.mode if change.after else None,
            )
            for change in intent.changes
        ),
    )
    if plan_validation(state).blocked_paths:
        refuse("SOURCE_PATH_DENIED")
    preview = tuple(
        ChangePreview(
            path_digest=sha256(change.path.encode("utf-8")).hexdigest(),
            operation=change.operation,
            before=change.before,
            after=identity(change.after),
            change_fingerprint=digest(change.model_dump(mode="json")),
        )
        for change in intent.changes
    )
    payload = intent.model_dump(mode="json") | dict(
        policy_digest=POLICY_DIGEST,
        change_fingerprint=digest([item.change_fingerprint for item in preview]),
        preview=[item.model_dump(mode="json") for item in preview],
        mutation_supported=False,
    )
    return SourceChangePlan(**payload, plan_hash=digest(payload))


def verify_source_plan(plan, observation):
    """Reject altered plans, references, fingerprints, policy, preview or source."""
    try:
        expected = plan_source_changes(
            SourceChangeIntent.model_validate(
                plan.model_dump(
                    mode="json",
                    warnings=False,
                    include={
                        "schema_version",
                        "source",
                        "references",
                        "changes",
                    },
                )
            ),
            observation,
        )
    except (ValueError, TypeError, AttributeError):
        refuse("SOURCE_CONTRACT_INVALID")
    if expected != plan:
        refuse("SOURCE_PLAN_CHANGED")
    return expected
