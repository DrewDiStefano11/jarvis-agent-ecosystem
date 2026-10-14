"""Pure exact commit planning; no subprocesses, object writes, staging or authority.

Native services must retrieve source records/checkpoints and verify live ownership,
separate operator approval and immutable evidence before durable preparation.
This module can only propose exact objects for those services to review.
"""

import json
from hashlib import sha1, sha256
from pathlib import Path

from app.core.errors import DomainError
from app.models import git_commit as contracts
from app.models.git_changes import GitChangeObservation, InventoryFile
from app.models.git_commit import CommitLineage, CommitMetadata, CommitPreparationPlan
from app.models.source_change import SourceChangePlan, SourceObservation
from app.self_build.change_verifier import VERIFICATION_POLICY_DIGEST, path_nodes
from app.self_build.policy import digest
from app.source_change.planner import POLICY_DIGEST as SOURCE_POLICY_DIGEST
from app.source_change.planner import verify_source_plan

PREPARATION_POLICY_DIGEST = digest(
    {
        "schema": "git-commit-preparation/1.0",
        "implementation": sha256(
            (
                Path(__file__).read_text(encoding="utf-8")
                + Path(contracts.__file__).read_text(encoding="utf-8")
                + VERIFICATION_POLICY_DIGEST
                + SOURCE_POLICY_DIGEST
            ).encode()
        ).hexdigest(),
    }
)


def refuse(code):
    raise DomainError(code, "Commit proposal failed a required verification check.", 409)


def object_sha(kind, payload):
    return sha1(
        kind.encode() + b" " + str(len(payload)).encode() + b"\0" + payload, usedforsecurity=False
    ).hexdigest()


def tree_sha(entries):
    """Serialize real Git trees, including Git's directory-sensitive byte ordering."""
    path_nodes([(entry.path, True) for entry in entries])
    root = {}
    for entry in entries:
        segments = entry.path.split("/")
        parent = root
        for leaf in segments[:-1]:
            parent = parent.setdefault(leaf, {})
        if segments[-1] in parent:
            refuse("GIT_COMMIT_TREE_COLLISION")
        parent[segments[-1]] = entry.identity

    def serialize(nodes):
        payload = bytearray()
        for name, value in sorted(
            nodes.items(),
            key=lambda item: item[0].encode("utf-8") + (b"/" if isinstance(item[1], dict) else b""),
        ):
            directory = isinstance(value, dict)
            mode = "40000" if directory else value.mode
            blob = serialize(value) if directory else value.blob_sha
            payload.extend(
                mode.encode() + b" " + name.encode("utf-8") + b"\0" + bytes.fromhex(blob)
            )
        return object_sha("tree", payload)

    return serialize(root)


def commit_payload(tree, parent, metadata):
    return metadata.payload(tree, parent)


def propose_commit(observation, source_plan, original_observation, lineage, metadata, selection):
    """Verify shapes and immutable before/after identities without granting authority.

    Reference identities are retained for a native service to resolve, never
    accepted as proof that an edit/checkpoint or operator approval actually exists.
    """
    try:
        observation = GitChangeObservation.model_validate(observation.model_dump(mode="json"))
        source_plan = SourceChangePlan.model_validate(source_plan.model_dump(mode="json"))
        original_observation = SourceObservation.model_validate(
            original_observation.model_dump(mode="json")
        )
        lineage = CommitLineage.model_validate(lineage.model_dump(mode="json"))
        metadata = CommitMetadata.model_validate(metadata.model_dump(mode="json"))
        selected = tuple(sorted(selection))
        if not selected or len(selected) > 64 or len(set(selected)) != len(selected):
            refuse("GIT_COMMIT_SELECTION_INVALID")
        path_nodes([(path, True) for path in selected])
    except (ValueError, TypeError, AttributeError):
        refuse("GIT_COMMIT_CONTRACT_INVALID")
    verify_source_plan(source_plan, original_observation)
    if not observation.index_clean:
        refuse("GIT_COMMIT_INDEX_DIRTY")
    if observation.verification_policy_digest != VERIFICATION_POLICY_DIGEST:
        refuse("GIT_COMMIT_REINSPECTION_REQUIRED")
    if (
        source_plan.source.repository_identity != observation.repository_identity
        or source_plan.source.head_sha != observation.head_sha
        or source_plan.source.base_sha != observation.original_base_sha
        or lineage.source_edit_plan_hash != source_plan.plan_hash
        or lineage.original_references != source_plan.references
    ):
        refuse("GIT_COMMIT_SOURCE_LINEAGE_INVALID")
    actual = {change.path: change for change in observation.changes}
    planned = {change.path: change for change in source_plan.changes}
    # All source-plan changes must match native measurements; subsets cannot hide
    # a failed/partially applied edit. Unrelated changes may remain unselected.
    for path, change in planned.items():
        measured = actual.get(path)
        if measured is None:
            refuse("GIT_COMMIT_SOURCE_MISMATCH")
        for proposed, observed in (
            (change.before, measured.before),
            (change.after, measured.after),
        ):
            if (proposed is None) != (observed is None) or (
                proposed is not None
                and (
                    proposed.digest != observed.sha256
                    or proposed.mode != observed.mode
                    or proposed.byte_count != observed.byte_count
                )
            ):
                refuse("GIT_COMMIT_SOURCE_MISMATCH")
    if any(path not in actual or path not in planned for path in selected):
        refuse("GIT_COMMIT_SELECTION_INVALID")
    if tree_sha(observation.original_inventory) != observation.original_tree_sha:
        refuse("GIT_COMMIT_BASE_TREE_INVALID")
    candidate = {entry.path: entry for entry in observation.original_inventory}
    for path in selected:
        change = actual[path]
        if change.after is None:
            candidate.pop(path, None)
        else:
            candidate[path] = InventoryFile(path=path, identity=change.after)
    prospective_tree = tree_sha(tuple(candidate.values()))
    raw_commit = commit_payload(prospective_tree, observation.head_sha, metadata)
    payload = dict(
        schema_version="1.0",
        operation="git.commit.prepare",
        state="proposed",
        workspace_id=observation.workspace_id,
        creation_operation_id=observation.creation_operation_id,
        creation_plan_hash=observation.creation_plan_hash,
        ownership_digest=observation.ownership_digest,
        repository_identity=observation.repository_identity,
        branch=observation.branch,
        original_base_sha=observation.original_base_sha,
        parent_sha=observation.head_sha,
        observation_fingerprint=observation.change_fingerprint,
        verification_policy_digest=observation.verification_policy_digest,
        preparation_policy_digest=PREPARATION_POLICY_DIGEST,
        tool_sha256=observation.tool_sha256,
        lineage=lineage.model_dump(mode="json"),
        files=[actual[path].model_dump(mode="json") for path in selected],
        metadata=metadata.model_dump(mode="json"),
        prospective_tree_sha=prospective_tree,
        prospective_commit_sha=object_sha("commit", raw_commit),
        commit_payload_sha256=sha256(raw_commit).hexdigest(),
        maximum_output_bytes=65536,
        commit_creation_supported=False,
    )
    if len(json.dumps(payload).encode()) > 65536:
        refuse("GIT_COMMIT_OUTPUT_LIMIT")
    return CommitPreparationPlan(**payload, plan_hash=digest(payload))
