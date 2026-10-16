"""Install only an inode-owned exact index, preserving the original on interruption.

No directory creation, stage creation, byte adoption, replacement or cleanup. The
native service must persist stage ownership before writing approved index bytes.
"""

import os
from contextlib import ExitStack
from hashlib import sha256
from pathlib import Path

from app.models.git_execution import NativeCommitRecord
from app.self_build.commit_namespace_watch import effect_namespace_watch
from app.self_build.commit_process import refuse
from app.self_build.git_observer import pinned_metadata_file
from app.source_mutation.native import pinned_file, rename_pinned
from app.tool_execution.filesystem import open_directory, read_bytes


def index_names(record):
    return (
        ".jarvis-" + record.operation_id + ".index.before",
        ".jarvis-" + record.operation_id + ".index.after",
    )


def exact_file(fd, identity, expected_hash):
    info = os.fstat(fd)
    if (info.st_dev, info.st_ino) != identity or sha256(
        read_bytes(fd, 8388608)
    ).hexdigest() != expected_hash:
        refuse("GIT_COMMIT_INDEX_OWNERSHIP_INVALID")


def install_index(policy, record, authority_check):
    record = NativeCommitRecord.model_validate(record.model_dump(mode="json"))
    if record.state != "index_prepared" or record.index_stage_inode is None:
        refuse("GIT_COMMIT_INDEX_INTENT_REQUIRED")
    policy = policy.checked()
    if policy.repository_identity != record.plan.preparation.plan.proposal.repository_identity:
        refuse("GIT_COMMIT_INDEX_SCOPE_CHANGED")
    authority_check(record)
    expected_original = (record.plan.original_index_device, record.plan.original_index_inode)
    expected_new = (record.index_stage_device, record.index_stage_inode)
    before, after = index_names(record)
    path = Path(policy.primary_root) / ".git/worktrees" / record.workspace_id
    with ExitStack() as stack:
        parent = open_directory(stack, Path(path.anchor), internal=True)
        for leaf in path.parts[1:]:
            parent = open_directory(stack, parent.path / leaf, parent, leaf, internal=True)
        allowed = frozenset({"index", before, after})
        namespace_check = stack.enter_context(effect_namespace_watch(path, allowed))
        count = 0

        def walk_error(error):
            raise error

        for root, directories, files in os.walk(path, followlinks=False, onerror=walk_error):
            namespace_check()
            authority_check(record)
            count += len(directories) + len(files)
            if count > 16384:
                refuse("GIT_COMMIT_METADATA_LIMIT")
            for name in directories:
                open_directory(stack, Path(root) / name, internal=True)
            for name in files:
                entry = Path(root) / name
                if entry.relative_to(path).as_posix() not in allowed:
                    stack.enter_context(pinned_metadata_file(entry))

        def guard():
            namespace_check()
            authority_check(record)
            namespace_check()

        guard()
        try:
            index_fd = stack.enter_context(pinned_file(path / "index"))
        except FileNotFoundError:
            index_fd = None
        if index_fd is not None:
            info = os.fstat(index_fd)
            if (info.st_dev, info.st_ino) == expected_new:
                exact_file(index_fd, expected_new, record.plan.index_payload_sha256)
                original_fd = stack.enter_context(pinned_file(path / before))
                exact_file(original_fd, expected_original, record.plan.original_index_sha256)
                try:
                    stack.enter_context(pinned_file(path / after))
                except FileNotFoundError:
                    guard()
                    return
                refuse("GIT_COMMIT_INDEX_STAGE_UNCERTAIN")
            exact_file(index_fd, expected_original, record.plan.original_index_sha256)
            try:
                stack.enter_context(pinned_file(path / before))
            except FileNotFoundError:
                pass
            else:
                refuse("GIT_COMMIT_INDEX_BACKUP_UNCERTAIN")
        else:
            original_fd = stack.enter_context(pinned_file(path / before))
            exact_file(original_fd, expected_original, record.plan.original_index_sha256)
        stage_fd = stack.enter_context(pinned_file(path / after))
        exact_file(stage_fd, expected_new, record.plan.index_payload_sha256)
        guard()
        if index_fd is not None:
            rename_pinned(index_fd, before)
            guard()
        # Destination absence is enforced atomically by the native rename. An
        # intervening foreign index is preserved and refuses; never replace it.
        rename_pinned(stage_fd, "index")
        guard()
