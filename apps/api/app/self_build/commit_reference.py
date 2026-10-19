"""One fixed owned-reference CAS under complete immutable metadata/name pins."""

from contextlib import ExitStack
from pathlib import Path
from time import monotonic

from app.models.git_commit_history import NativeCommitHistory
from app.self_build.commit_metadata import measure_metadata
from app.self_build.commit_metadata_rules import validate_metadata
from app.self_build.commit_namespace_watch import effect_namespace_watch
from app.self_build.commit_process import NativeCommitProcess, refuse
from app.source_mutation.native import read_pin
from app.tool_execution.filesystem import open_directory, open_file, read_bytes


def advance_reference(policy, history, program, process):
    """Caller supplies protected history and retains approved source/owner pins.

    The live guard must resolve current journal, approval, lease and emergency stop.
    This internal primitive never records a successful checkpoint on its own.
    Exact post-effect state is independently measured before returning, including
    all unrelated primary/shared metadata. Unknown artifacts remain untouched.
    """
    history = NativeCommitHistory.model_validate(history.model_dump(mode="json"))
    record, policy, proposal = process._validated(policy, history.current, program)
    if record.state != "reference_prepared":
        refuse("GIT_COMMIT_REFERENCE_INTENT_REQUIRED")
    root_path = Path(policy.primary_root) / ".git"
    reference = "refs/heads/" + proposal.branch
    names = frozenset(
        {
            reference,
            reference + ".lock",
            "worktrees/" + record.workspace_id + "/HEAD.lock",
            "logs/" + reference,
            "worktrees/" + record.workspace_id + "/logs/HEAD",
        }
    )
    process.guard(record)
    try:
        with ExitStack() as stack:
            directory = open_directory(stack, Path(root_path.anchor), internal=True)
            for part in root_path.parts[1:]:
                directory = open_directory(
                    stack, directory.path / part, directory, part, internal=True
                )
            namespace = stack.enter_context(effect_namespace_watch(root_path, names))

            def guard(current):
                if current != record:
                    refuse("GIT_COMMIT_REFERENCE_RECORD_CHANGED")
                namespace()
                process.guard(current)
                namespace()

            bounded = NativeCommitProcess(process.observer, guard)
            checked = [float("-inf")]

            def scan_authority():
                # Native file pins protect every byte throughout each scan. Keep
                # name checks on every call and poll expensive live journal/RBAC
                # authority at the same bounded interval as the owned process.
                # Critical command boundaries still call the full guard directly.
                namespace()
                if monotonic() - checked[0] >= 0.2:
                    guard(record)
                    checked[0] = monotonic()
                namespace()

            def log_bytes(path):
                # No provider/model path importer: only these generated names.
                if path not in {
                    "logs/" + reference,
                    "worktrees/" + record.workspace_id + "/logs/HEAD",
                }:
                    refuse("GIT_COMMIT_REFLOG_SCOPE_CHANGED")
                with open_file(directory, path, 0, internal=True) as fd:
                    return read_bytes(fd, 67108864)

            before = measure_metadata(policy, scan_authority)
            validate_metadata(
                history,
                program,
                before,
                read_reflog=log_bytes,
                object_available=lambda index: bounded.object_available(
                    policy, record, program, index
                ),
            )
            # Retain every ordinary directory and every immutable file throughout
            # the command. Mutable scope is exactly this feature ref and its logs.
            for node in before.nodes:
                path = root_path / node.path
                if node.kind == "directory":
                    open_directory(stack, path, internal=True)
                elif node.path not in names:
                    stack.enter_context(read_pin(path))
            guard(record)
            sealed = measure_metadata(policy, scan_authority)
            validate_metadata(
                history,
                program,
                sealed,
                read_reflog=log_bytes,
                object_available=lambda index: bounded.object_available(
                    policy, record, program, index
                ),
            )
            current_sha = bounded.reference_value(policy, record, program)
            if current_sha == proposal.parent_sha:
                bounded.update_reference(policy, record, program)
            elif current_sha != program.commit_sha:
                refuse("GIT_COMMIT_REFERENCE_CHANGED")
            # Lost acknowledgement reconciles the exact ref; it never replays CAS.
            if bounded.reference_value(policy, record, program) != program.commit_sha:
                refuse("GIT_COMMIT_REFERENCE_ACKNOWLEDGEMENT_INVALID")
            after = measure_metadata(policy, scan_authority)
            validate_metadata(
                history,
                program,
                after,
                read_reflog=log_bytes,
                object_available=lambda index: bounded.object_available(
                    policy, record, program, index
                ),
            )
            guard(record)
    except OSError:
        refuse("GIT_COMMIT_REFERENCE_METADATA_UNAVAILABLE")
