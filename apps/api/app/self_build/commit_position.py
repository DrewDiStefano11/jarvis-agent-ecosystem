"""Read actual native recovery positions; journal phases alone never prove effects."""

import os
from contextlib import ExitStack
from hashlib import sha256
from pathlib import Path

from app.models.git_execution import CommitRecoveryPosition
from app.self_build.commit_index_install import index_names
from app.self_build.commit_process import refuse
from app.self_build.git_observer import pinned_metadata_file
from app.tool_execution.filesystem import open_directory, open_file, read_bytes


def index_position(policy, record, program, process):
    """Requires the caller's complete metadata/source pins and live journal guard.

    Exact compiled bytes without the durable stage inode are foreign. The saved
    original index remains required after installation. Missing or unexpected
    paths are classified conservatively; no replacement or cleanup is attempted.
    """
    record, policy, _ = process._validated(policy, record, program)
    before, after = index_names(record)
    path = Path(policy.primary_root) / ".git/worktrees" / record.workspace_id
    process.guard(record)
    try:
        with ExitStack() as stack:
            parent = open_directory(stack, Path(path.anchor), internal=True)
            for part in path.parts[1:]:
                parent = open_directory(stack, parent.path / part, parent, part, internal=True)
            entries = {}
            for name in ("index", before, after):
                try:
                    stack.enter_context(pinned_metadata_file(parent.path / name))
                except FileNotFoundError:
                    entries[name] = None
                    continue
                with open_file(parent, name, os.O_RDONLY, internal=True) as fd:
                    info = os.fstat(fd)
                    entries[name] = ((info.st_dev, info.st_ino), read_bytes(fd, 8388608))
            result = classify_index(
                entries["index"],
                entries[before],
                entries[after],
                original_identity=(
                    record.plan.original_index_device,
                    record.plan.original_index_inode,
                ),
                original_hash=record.plan.original_index_sha256,
                stage_identity=(record.index_stage_device, record.index_stage_inode),
                candidate=program.index_payload,
            )
            process.guard(record)
            return result
    except OSError:
        refuse("GIT_COMMIT_INDEX_OBSERVATION_UNAVAILABLE")


def classify_index(
    index, backup, stage, *, original_identity, original_hash, stage_identity, candidate
):
    """Private pure interpretation of independently pinned native measurements."""

    def original(entry):
        return (
            entry is not None
            and entry[0] == original_identity
            and sha256(entry[1]).hexdigest() == original_hash
        )

    def owned_stage(entry, *, partial=False):
        return (
            None not in stage_identity
            and entry is not None
            and entry[0] == stage_identity
            and (candidate.startswith(entry[1]) if partial else entry[1] == candidate)
        )

    if (
        original(index)
        and backup is None
        and ((stage is None and stage_identity == (None, None)) or owned_stage(stage, partial=True))
    ):
        return "original"
    if index is None and original(backup) and owned_stage(stage):
        return "installing"
    if owned_stage(index) and original(backup) and stage is None:
        return "exact_tree"
    return "foreign"


def measure_position(policy, record, program, process):
    record, policy, proposal = process._validated(policy, record, program)
    reference = process.reference_value(policy, record, program)
    branch = (
        "parent"
        if reference == proposal.parent_sha
        else "exact_commit"
        if reference == program.commit_sha
        else "foreign"
    )
    index = index_position(policy, record, program, process)
    objects = (
        "complete"
        if all(
            process.object_available(policy, record, program, position)
            for position in range(len(program.objects))
        )
        else "incomplete"
    )
    if branch == "foreign" or index == "foreign":
        next_step = "blocked"
    elif branch == "exact_commit":
        next_step = "checkpoint" if index == "exact_tree" and objects == "complete" else "blocked"
    elif index == "exact_tree":
        next_step = "reference" if objects == "complete" else "blocked"
    elif index == "installing":
        next_step = "index" if objects == "complete" else "blocked"
    else:
        next_step = "index" if objects == "complete" else "objects"
    process.guard(record)
    return CommitRecoveryPosition(branch=branch, index=index, objects=objects, next_step=next_step)
