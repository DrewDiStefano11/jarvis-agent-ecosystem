"""Read-only source pins that remain valid across authorized Git metadata effects."""

import json
import os
from contextlib import ExitStack, contextmanager
from hashlib import sha256
from pathlib import Path

from app.models.git_changes import GitChangeObservation
from app.self_build.atomic_directory import pinned_target_directory
from app.self_build.change_verifier import MAX_NODES, path_nodes
from app.self_build.checkout_driver import pin_read_tree
from app.self_build.commit_process import refuse
from app.self_build.creation_service import ownership
from app.self_build.git_observer import pinned_metadata_file
from app.self_build.policy import digest
from app.tool_execution.filesystem import open_directory, open_file, read_bytes


@contextmanager
def source_namespace(policy, creation, nonce, snapshot, authority_check):
    """Caller resolves native creation/checkpoint and execution approval first.

    No creation, source replay, lock creation, artifact adoption or deletion. A
    current matching backup is read evidence, never durable cleanup ownership.
    Git index/objects/ref effects are fenced by a separate metadata namespace.
    """
    policy = policy.checked()
    snapshot = GitChangeObservation.model_validate(snapshot.model_dump(mode="json"))
    if (
        snapshot.schema_version != "1.1"
        or creation.state != "ready"
        or creation.checkpoint_id is None
        or creation.workspace_id != snapshot.workspace_id
        or creation.ownership_digest != snapshot.ownership_digest
        or creation.plan.plan_hash != snapshot.creation_plan_hash
        or creation.operation_id != snapshot.creation_operation_id
        or creation.ownership_digest != digest(ownership(creation, nonce))
        or creation.plan.workspace.policy_digest != digest(policy.model_dump())
    ):
        refuse("GIT_COMMIT_SOURCE_OWNERSHIP_INVALID")
    authority_check()
    with ExitStack() as stack:
        root_path = Path(policy.worktree_root)
        root = open_directory(stack, Path(root_path.anchor), internal=True)
        for leaf in root_path.parts[1:]:
            root = open_directory(stack, root.path / leaf, root, leaf, internal=True)
        private_name = ".jarvis-owner-" + creation.workspace_id
        private = open_directory(stack, root.path / private_name, root, private_name, internal=True)
        for leaf in ("owner.json", "target.json"):
            stack.enter_context(pinned_metadata_file(private.path / leaf))
        with open_file(private, "owner.json", os.O_RDONLY, internal=True) as fd:
            if json.loads(read_bytes(fd, 8192)) != ownership(creation, nonce):
                refuse("GIT_COMMIT_SOURCE_OWNERSHIP_INVALID")
        target, info = pinned_target_directory(stack, root, creation.workspace_id, create=False)
        with open_file(private, "target.json", os.O_RDONLY, internal=True) as fd:
            if json.loads(read_bytes(fd, 4096)) != dict(
                workspaceId=creation.workspace_id,
                ownershipDigest=creation.ownership_digest,
                device=info.st_dev,
                inode=info.st_ino,
            ):
                refuse("GIT_COMMIT_SOURCE_OWNERSHIP_INVALID")
        directory = open_directory(stack, target, root, creation.workspace_id, internal=True)
        source_check = pin_read_tree(stack, target, authority_check, maximum=MAX_NODES)
        internal = {item.path: item for item in snapshot.internal_inventory}
        expected = {item.path: item.identity for item in snapshot.current_inventory}
        if (
            set(internal) & set(expected)
            or not {".git", ".jarvis-workspace.json"} <= internal.keys()
        ):
            refuse("GIT_COMMIT_SOURCE_SNAPSHOT_INVALID")
        files, directories = set(), []

        def walk_error(error):
            raise error

        for parent, children, leaves in os.walk(target, followlinks=False, onerror=walk_error):
            authority_check()
            directories.extend(
                (Path(parent) / name).relative_to(target).as_posix() for name in children
            )
            files.update((Path(parent) / name).relative_to(target).as_posix() for name in leaves)
        # The current canonical read-tool boundary creates this exact
        # coordination file. Match ChangeVerifier's byte check; it is neither
        # source content nor cleanup ownership, and the tree pin retains it.
        if ".jarvis-workspace.lock" in files:
            with open_file(directory, ".jarvis-workspace.lock", os.O_RDONLY, internal=True) as fd:
                if os.fstat(fd).st_size != 1 or read_bytes(fd, 1) != b"0":
                    refuse("GIT_COMMIT_SOURCE_COORDINATION_CHANGED")
            files.remove(".jarvis-workspace.lock")
        if files != expected.keys() | internal.keys():
            refuse("GIT_COMMIT_SOURCE_CHANGED")
        path_nodes(
            [(name, True) for name in files] + [(name, False) for name in directories],
            artifacts=internal,
        )
        for name, identity in {**expected, **internal}.items():
            authority_check()
            with open_file(directory, name, os.O_RDONLY, internal=True) as fd:
                data = read_bytes(fd, identity.byte_count)
            if len(data) != identity.byte_count or sha256(data).hexdigest() != identity.sha256:
                refuse("GIT_COMMIT_SOURCE_CHANGED")

        def check(*, check_authority=True):
            if check_authority:
                authority_check()
            source_check()

        check()
        yield target, check
        check()
