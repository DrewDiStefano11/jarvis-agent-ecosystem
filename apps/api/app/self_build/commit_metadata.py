"""Strong read-only metadata evidence retained under native namespace/file pins."""

import os
from contextlib import ExitStack
from hashlib import sha256
from pathlib import Path

from app.models.git_commit_metadata import CommitMetadataNode, CommitMetadataSnapshot
from app.self_build.checkout_driver import pin_read_tree
from app.self_build.commit_object_install import compressed_object
from app.self_build.commit_process import refuse
from app.self_build.policy import digest
from app.tool_execution.filesystem import open_directory, open_file


def reserve_metadata(snapshot, program):
    """Refuse before approval/effects if the full transition cannot stay bounded.

    Packed-object reuse may reduce actual growth. Reserve missing loose objects
    conservatively plus temporary object/index/ref names and possible reflogs.
    """
    paths = {node.path for node in snapshot.nodes}
    missing = [
        item
        for item in program.objects
        if "objects/" + item.sha[:2] + "/" + item.sha[2:] not in paths
    ]
    directories = {"objects/" + item.sha[:2] for item in missing} - paths
    encoded_sizes = [len(compressed_object(item)) for item in missing]
    if len(snapshot.nodes) + len(missing) + len(directories) + 7 > 16384:
        refuse("GIT_COMMIT_METADATA_CAPACITY_REQUIRED")
    # Keep the original index backup, one candidate index, object staging and
    # bounded retry reflogs within the strong snapshot's total byte budget.
    total = sum(node.byte_count or 0 for node in snapshot.nodes)
    additional = (
        sum(encoded_sizes) + max(encoded_sizes, default=0) + len(program.index_payload) + 65536
    )
    if total + additional > 268435456:
        refuse("GIT_COMMIT_METADATA_CAPACITY_REQUIRED")


def measure_metadata(policy, authority_check):
    """Caller resolves current inspection permission before invoking this read.

    Bytes are hashed through pinned ordinary handles; no content is persisted or
    returned. Reparse, aliases, hard links, missing parents or changed names
    refuse. This snapshot cannot authorize a later mutation by itself.
    """
    policy = policy.checked()
    authority_check()
    path = Path(policy.primary_root) / ".git"
    try:
        with ExitStack() as stack:
            parent = open_directory(stack, Path(path.anchor), internal=True)
            for part in path.parts[1:]:
                parent = open_directory(stack, parent.path / part, parent, part, internal=True)
            check = pin_read_tree(stack, path, authority_check)
            root = path.stat()
            nodes, total = [], 0

            def walk_error(error):
                raise error

            for directory, directories, files in os.walk(
                path, followlinks=False, onerror=walk_error
            ):
                check()
                authority_check()
                for name in directories:
                    target = Path(directory) / name
                    info = target.stat()
                    nodes.append(
                        CommitMetadataNode(
                            path=target.relative_to(path).as_posix(),
                            kind="directory",
                            device=info.st_dev,
                            inode=info.st_ino,
                        )
                    )
                for name in files:
                    authority_check()
                    target = Path(directory) / name
                    relative = target.relative_to(path).as_posix()
                    with open_file(parent, relative, os.O_RDONLY, internal=True) as fd:
                        info = os.fstat(fd)
                        if info.st_size > 67108864 or total + info.st_size > 268435456:
                            refuse("GIT_COMMIT_METADATA_BYTE_LIMIT")
                        hasher, observed = sha256(), 0
                        while block := os.read(fd, 65536):
                            authority_check()
                            observed += len(block)
                            if observed > info.st_size:
                                refuse("GIT_COMMIT_METADATA_CHANGED")
                            hasher.update(block)
                        if observed != info.st_size:
                            refuse("GIT_COMMIT_METADATA_CHANGED")
                    total += observed
                    nodes.append(
                        CommitMetadataNode(
                            path=relative,
                            kind="file",
                            device=info.st_dev,
                            inode=info.st_ino,
                            byte_count=observed,
                            sha256=hasher.hexdigest(),
                        )
                    )
            payload = dict(
                schema_version="1.0",
                root_device=root.st_dev,
                root_inode=root.st_ino,
                nodes=[
                    node.model_dump(mode="json")
                    for node in sorted(nodes, key=lambda node: node.path)
                ],
            )
            result = CommitMetadataSnapshot(**payload, snapshot_digest=digest(payload))
            authority_check()
            check()
            return result
    except OSError:
        refuse("GIT_COMMIT_METADATA_UNAVAILABLE")


def reserve_history(plan, snapshot, metadata):
    """Reserve every maximum-size progress claim before native intent/effects.

    Windows file identities are bounded to 128 bits in the progress contract.
    512 bytes covers the longest claim, ordinal, operation ID and two hashes.
    Both original/current records retain the complete original plan.
    """
    maximum = (
        2 * (len(plan.model_dump_json().encode()) + 4096)
        + len(snapshot.model_dump_json().encode())
        + len(metadata.model_dump_json().encode())
        + (5 * plan.object_count + 6) * 512
        + 4096
    )
    if maximum > 16777216:
        refuse("GIT_COMMIT_HISTORY_CAPACITY_REQUIRED")
