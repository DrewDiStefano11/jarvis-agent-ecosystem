"""Protected native recovery inspection without rerunning original Git planning.

No approval, mutation, source replay or public route. Original source/metadata
evidence remains immutable even after the owned index/reference has advanced.
"""

import os
from contextlib import ExitStack
from pathlib import Path
from time import monotonic

from app.models.git_execution import NativeCommitRecovery
from app.self_build.checkout_driver import pin_read_tree
from app.self_build.commit_journal import NativeCommitJournal
from app.self_build.commit_metadata import measure_metadata
from app.self_build.commit_metadata_rules import validate_metadata
from app.self_build.commit_objects import compile_objects
from app.self_build.commit_position import measure_position
from app.self_build.commit_process import NativeCommitProcess
from app.self_build.commit_recovery import recovery_step
from app.self_build.commit_source_namespace import source_namespace
from app.self_build.policy import digest
from app.tool_execution.filesystem import open_directory, open_file, read_bytes


def inspect_recovery(service, actor, workspace_id, command_id, finalize=None, *, request=None):
    """Resolve all evidence internally and retain full pins through a callback.

    A later approval service may persist this read observation under these pins.
    A model-provided recovery hash/position/history is never accepted as proof.
    Even a completed historical receipt requires live ownership for this fresh
    native read; the journal's historical read remains separately available.
    """
    from app.self_build.commit_execution import EXECUTION_POLICY_DIGEST

    journal = NativeCommitJournal(service)
    history = journal.history(actor, workspace_id, command_id)
    record = history.current
    plan = record.plan
    proposal = plan.preparation.plan.proposal
    if plan.execution_policy_digest != EXECUTION_POLICY_DIGEST:
        journal.fail("GIT_COMMIT_EXECUTION_PLAN_CHANGED")
    creation = service.preparation.creation.read(actor, workspace_id)
    source_id = plan.preparation.plan.source_edit_record_id

    def live():
        with service.repository.sessions() as session:
            row, attempt, policy, current_creation, nonce = service.preparation.live(
                actor, workspace_id, session, request=request
            )
            service.permission(actor, row, session)
            if attempt != proposal.lineage.attempt_id or current_creation != creation:
                journal.fail("GIT_COMMIT_EXECUTION_OWNER_CHANGED")
            return policy, current_creation, nonce

    baseline = live()
    policy, _, nonce = baseline

    def authority():
        if live() != baseline or journal.history(actor, workspace_id, command_id) != history:
            journal.fail("GIT_COMMIT_RECOVERY_RECORD_CHANGED")
        if (
            service.preparation.read(actor, workspace_id, plan.preparation.command_id)
            != plan.preparation
            or service.applied.source(actor, workspace_id, source_id) != plan.applied_source
            or service.artifacts.read(actor, source_id).manifest_hash
            != plan.source_artifact_manifest_hash
        ):
            journal.fail("GIT_COMMIT_EXECUTION_EVIDENCE_CHANGED")

    authority()
    checked = [float("-inf")]

    def scan_authority():
        if monotonic() - checked[0] >= 0.2:
            authority()
            checked[0] = monotonic()

    with ExitStack() as stack:
        target, source_guard = stack.enter_context(
            source_namespace(policy, creation, nonce, history.source_snapshot, scan_authority)
        )
        directory = open_directory(stack, target, internal=True)
        selected = {}
        for item in proposal.files:
            if item.after is not None:
                with open_file(directory, item.path, os.O_RDONLY, internal=True) as fd:
                    selected[item.path] = read_bytes(fd, item.after.byte_count)
        program = compile_objects(proposal, history.source_snapshot, selected)

        def process_guard(current):
            if current != record:
                journal.fail("GIT_COMMIT_RECOVERY_RECORD_CHANGED")
            source_guard(check_authority=False)
            authority()

        process = NativeCommitProcess(service.preparation.git.tool(), process_guard)
        process._validated(policy, record, program)
        root = Path(policy.primary_root) / ".git"
        common = open_directory(stack, Path(root.anchor), internal=True)
        for part in root.parts[1:]:
            common = open_directory(stack, common.path / part, common, part, internal=True)
        metadata_guard = pin_read_tree(stack, root, scan_authority)

        def guard(*, check_authority=True):
            source_guard(check_authority=False)
            metadata_guard()
            if check_authority:
                authority()

        paths = {
            "logs/refs/heads/" + proposal.branch,
            "worktrees/" + workspace_id + "/logs/HEAD",
        }

        def logs(path):
            if path not in paths:
                journal.fail("GIT_COMMIT_REFLOG_SCOPE_CHANGED")
            with open_file(common, path, os.O_RDONLY, internal=True) as fd:
                return read_bytes(fd, 67108864)

        def scan_guard():
            guard(check_authority=False)
            scan_authority()

        measured = measure_metadata(policy, scan_guard)
        validate_metadata(
            history,
            program,
            measured,
            read_reflog=logs,
            object_available=lambda index: process.object_available(policy, record, program, index),
        )
        position = measure_position(policy, record, program, process)
        step = recovery_step(record.state, position)
        if step == "blocked":
            journal.fail("GIT_COMMIT_RECOVERY_POSITION_BLOCKED")
        payload = dict(
            operation_id=record.operation_id,
            workspace_id=workspace_id,
            command_id=command_id,
            original_plan_hash=plan.plan_hash,
            record_digest=digest(record.model_dump(mode="json")),
            metadata_snapshot_digest=measured.snapshot_digest,
            phase=record.state,
            position=position.model_dump(mode="json"),
            next_step=step,
        )
        preview = NativeCommitRecovery(**payload, recovery_hash=digest(payload))
        guard()
        result = preview if finalize is None else finalize(preview, history, program, guard)
        guard()
        return result
