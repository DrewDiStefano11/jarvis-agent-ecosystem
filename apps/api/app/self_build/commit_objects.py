"""Compile exact bounded Git objects; payloads are private, never execution grants."""

from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Literal

from app.models.git_changes import GitChangeObservation, InventoryFile
from app.models.git_commit import CommitPreparationPlan
from app.self_build.change_verifier import VERIFICATION_POLICY_DIGEST
from app.self_build.commit_index import INDEX_POLICY_DIGEST, compile_index
from app.self_build.commit_planner import PREPARATION_POLICY_DIGEST, object_sha, tree_sha
from app.self_build.policy import digest

MAX_OBJECTS = 8192
MAX_BYTES = 8388608
OBJECT_POLICY_DIGEST = sha256(
    (
        Path(__file__).read_text(encoding="utf-8") + PREPARATION_POLICY_DIGEST + INDEX_POLICY_DIGEST
    ).encode()
).hexdigest()


@dataclass(frozen=True)
class GitObject:
    kind: Literal["blob", "tree", "commit"]
    payload: bytes = field(repr=False)

    @property
    def sha(self):
        return object_sha(self.kind, self.payload)

    @property
    def descriptor(self):
        return [self.kind, self.sha, sha256(self.payload).hexdigest(), len(self.payload)]


@dataclass(frozen=True)
class ObjectProgram:
    objects: tuple[GitObject, ...] = field(repr=False)
    index_payload: bytes = field(repr=False)
    preparation_plan_hash: str
    observation_fingerprint: str
    tree_sha: str
    commit_sha: str
    compiler_policy_digest: str = OBJECT_POLICY_DIGEST

    @property
    def fingerprint(self):
        return digest(
            [
                self.preparation_plan_hash,
                self.observation_fingerprint,
                self.tree_sha,
                self.commit_sha,
                self.compiler_policy_digest,
                sha256(self.index_payload).hexdigest(),
                [item.descriptor for item in self.objects],
            ]
        )


def compile_objects(plan, observation, selected_content):
    """Require full matching evidence and exact selected bytes before compiling.

    Native callers must separately resolve durable preparation/applied checkpoints,
    obtain exact execution approval, retain source/metadata pins and fence effects.
    This function performs no filesystem, subprocess, index or reference operations.
    """
    plan = CommitPreparationPlan.model_validate(plan.model_dump(mode="json"))
    observation = GitChangeObservation.model_validate(observation.model_dump(mode="json"))
    if (
        plan.preparation_policy_digest != PREPARATION_POLICY_DIGEST
        or observation.verification_policy_digest != VERIFICATION_POLICY_DIGEST
        or plan.observation_fingerprint != observation.change_fingerprint
        or plan.workspace_id != observation.workspace_id
        or plan.creation_operation_id != observation.creation_operation_id
        or plan.creation_plan_hash != observation.creation_plan_hash
        or plan.ownership_digest != observation.ownership_digest
        or plan.repository_identity != observation.repository_identity
        or plan.branch != observation.branch
        or plan.parent_sha != observation.head_sha
        or plan.original_base_sha != observation.original_base_sha
        or plan.verification_policy_digest != observation.verification_policy_digest
        or plan.tool_sha256 != observation.tool_sha256
        or not observation.index_clean
        or tree_sha(observation.original_inventory) != observation.original_tree_sha
    ):
        raise ValueError("object program requires the exact original measured preparation")
    changes = {item.path: item for item in observation.changes}
    if any(changes.get(item.path) != item for item in plan.files):
        raise ValueError("selected changes differ from measured evidence")
    expected = {item.path for item in plan.files if item.after is not None}
    if set(selected_content) != expected:
        raise ValueError("object program accepts only exact selected present source bytes")
    objects = {}
    total = 0
    original_blobs = {}
    for item in observation.original_inventory:
        prior = original_blobs.setdefault(item.identity.blob_sha, item.identity)
        if (prior.sha256, prior.byte_count) != (item.identity.sha256, item.identity.byte_count):
            raise ValueError("original shared blob IDs have conflicting strong identities")

    def add(kind, payload):
        nonlocal total
        item = GitObject(kind, payload)
        original = original_blobs.get(item.sha) if kind == "blob" else None
        if original is not None and (
            original.sha256 != sha256(payload).hexdigest() or original.byte_count != len(payload)
        ):
            raise ValueError("selected blob collides with an unchanged original object identity")
        prior = objects.get(item.sha)
        if prior is not None and prior != item:
            raise ValueError("distinct object payloads collide")
        if prior is None:
            total += len(payload)
            if len(objects) >= MAX_OBJECTS or total > MAX_BYTES:
                raise ValueError("object program exceeds its bounded payload inventory")
            objects[item.sha] = item
        return item.sha

    candidate = {item.path: item for item in observation.original_inventory}
    for item in plan.files:
        if item.after is None:
            candidate.pop(item.path, None)
            continue
        content = selected_content[item.path]
        if (
            not isinstance(content, bytes)
            or len(content) != item.after.byte_count
            or sha256(content).hexdigest() != item.after.sha256
            or add("blob", content) != item.after.blob_sha
        ):
            raise ValueError("selected source bytes differ from approved identities")
        candidate[item.path] = InventoryFile(path=item.path, identity=item.after)
    # Also exercise canonical source namespace and component collision checks.
    expected_tree = tree_sha(tuple(candidate.values()))
    root = {}
    for item in candidate.values():
        parts = item.path.split("/")
        parent = root
        for name in parts[:-1]:
            parent = parent.setdefault(name, {})
        parent[parts[-1]] = item.identity

    def serialize(nodes):
        payload = bytearray()
        for name, value in sorted(
            nodes.items(),
            key=lambda item: item[0].encode("utf-8") + (b"/" if isinstance(item[1], dict) else b""),
        ):
            directory = isinstance(value, dict)
            mode = "40000" if directory else value.mode
            sha = serialize(value) if directory else value.blob_sha
            payload.extend(mode.encode() + b" " + name.encode("utf-8") + b"\0" + bytes.fromhex(sha))
        return add("tree", bytes(payload))

    tree = serialize(root)
    commit = add("commit", plan.metadata.payload(tree, plan.parent_sha))
    if commit == plan.parent_sha:
        raise ValueError("a prospective commit cannot replace its own parent identity")
    if (
        tree != expected_tree
        or tree != plan.prospective_tree_sha
        or commit != plan.prospective_commit_sha
    ):
        raise ValueError("compiled tree or commit differs from the exact proposal")
    return ObjectProgram(
        tuple(objects.values()),
        compile_index(candidate.values()),
        plan.plan_hash,
        observation.change_fingerprint,
        tree,
        commit,
    )
