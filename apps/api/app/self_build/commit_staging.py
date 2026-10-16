"""Exact private stage creation and prefix recovery; no Git or cleanup effects."""

import os
from contextlib import ExitStack
from hashlib import sha256
from pathlib import Path

from app.models.git_execution import NativeCommitRecord
from app.self_build.commit_index_install import index_names
from app.self_build.commit_objects import OBJECT_POLICY_DIGEST
from app.self_build.commit_process import refuse
from app.source_mutation.native import staging_file
from app.tool_execution.filesystem import open_directory, read_bytes


def stage_payload(policy, record, program, persist_identity, authority_check):
    """Retain an exclusive handle through durable inode claim and bounded writes.

    The caller resolves current journal/approval/checkpoint and pins complete
    source/metadata scope. persist_identity must commit the exact observed inode
    before returning; failed/uncertain claims preserve the newly created stage.
    A persisted missing inode or an existing unclaimed stage always refuses.
    """
    record = NativeCommitRecord.model_validate(record.model_dump(mode="json"))
    policy = policy.checked()
    plan = record.plan
    proposal = plan.preparation.plan.proposal
    if (
        record.intent_checkpoint_id is None
        or policy.repository_identity != proposal.repository_identity
        or program.fingerprint != plan.object_program_fingerprint
        or program.compiler_policy_digest != OBJECT_POLICY_DIGEST
        or plan.compiler_policy_digest != OBJECT_POLICY_DIGEST
        or program.preparation_plan_hash != proposal.plan_hash
        or program.observation_fingerprint != proposal.observation_fingerprint
        or program.tree_sha != proposal.prospective_tree_sha
        or program.commit_sha != proposal.prospective_commit_sha
        or len(program.objects) != plan.object_count
        or sum(len(item.payload) for item in program.objects) != plan.object_payload_bytes
        or sha256(program.index_payload).hexdigest() != plan.index_payload_sha256
        or len(program.index_payload) != plan.index_payload_bytes
    ):
        refuse("GIT_COMMIT_STAGE_PROGRAM_CHANGED")
    if record.state == "prepared" and record.pending_object_index is not None:
        item = program.objects[record.pending_object_index]
        payload = item.payload
        directory_path = Path(policy.worktree_root) / (".jarvis-owner-" + record.workspace_id)
        leaf = record.operation_id + "-" + item.sha + ".payload"
        identity = (record.staging_device, record.staging_inode)
        fields = ("staging_device", "staging_inode")
    elif record.state == "index_prepared":
        payload = program.index_payload
        directory_path = Path(policy.primary_root) / ".git/worktrees" / record.workspace_id
        leaf = index_names(record)[1]
        identity = (record.index_stage_device, record.index_stage_inode)
        fields = ("index_stage_device", "index_stage_inode")
    else:
        refuse("GIT_COMMIT_STAGE_INTENT_REQUIRED")
    authority_check(record)
    try:
        with ExitStack() as stack:
            parent = open_directory(stack, Path(directory_path.anchor), internal=True)
            for part in directory_path.parts[1:]:
                parent = open_directory(stack, parent.path / part, parent, part, internal=True)
            with staging_file(parent.path / leaf, None if identity[0] is None else identity) as (
                fd,
                observed,
            ):
                if identity[0] is None:
                    claimed = persist_identity(record, *observed)
                    claimed = NativeCommitRecord.model_validate(claimed.model_dump(mode="json"))
                    if claimed != record.model_copy(
                        update=dict(zip(fields, observed, strict=True))
                    ):
                        refuse("GIT_COMMIT_STAGE_CLAIM_INVALID")
                    record = claimed
                authority_check(record)
                existing = read_bytes(fd, len(payload))
                if payload[: len(existing)] != existing:
                    refuse("GIT_COMMIT_STAGE_PREFIX_INVALID")
                position = len(existing)
                os.lseek(fd, position, os.SEEK_SET)
                while position < len(payload):
                    authority_check(record)
                    written = os.write(fd, payload[position : position + 4096])
                    if written <= 0:
                        refuse("GIT_COMMIT_STAGE_WRITE_UNCERTAIN")
                    position += written
                os.fsync(fd)
                authority_check(record)
                os.lseek(fd, 0, os.SEEK_SET)
                if read_bytes(fd, len(payload)) != payload:
                    refuse("GIT_COMMIT_STAGE_CONTENT_INVALID")
            authority_check(record)
            return record
    except OSError:
        refuse("GIT_COMMIT_STAGE_OWNERSHIP_UNCERTAIN")
