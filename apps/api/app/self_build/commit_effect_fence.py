"""Complete shared metadata fence for one journaled object or index effect."""

from contextlib import ExitStack, contextmanager
from pathlib import Path
from time import monotonic

from app.self_build.commit_index_install import index_names
from app.self_build.commit_metadata import measure_metadata
from app.self_build.commit_metadata_rules import validate_metadata
from app.self_build.commit_namespace_watch import effect_namespace_watch
from app.self_build.commit_object_install import object_stage_name
from app.self_build.commit_process import refuse
from app.source_mutation.native import read_pin
from app.tool_execution.filesystem import open_directory


def effect_names(record, program):
    if record.state == "prepared" and record.pending_object_index is not None:
        item = program.objects[record.pending_object_index]
        prefix = "objects/" + item.sha[:2]
        return frozenset(
            {prefix, prefix + "/" + item.sha[2:], prefix + "/" + object_stage_name(record, item)}
        )
    if record.state == "index_prepared":
        prefix = "worktrees/" + record.workspace_id + "/"
        before, after = index_names(record)
        return frozenset({prefix + "index", prefix + before, prefix + after})
    refuse("GIT_COMMIT_EFFECT_INTENT_REQUIRED")


@contextmanager
def metadata_effect(policy, program, process, history_reader, authority):
    """Caller retains exact source pins; history_reader resolves protected rows.

    The permitted names come solely from durable intent and the compiled program.
    The effect itself must claim every new inode before writing it. This fence
    neither claims artifacts nor grants model, approval or runtime authority.
    """
    history = history_reader()
    record = history.current
    process._validated(policy, record, program)
    names = effect_names(record, program)
    root = Path(policy.primary_root) / ".git"
    with ExitStack() as stack:
        directory = open_directory(stack, Path(root.anchor), internal=True)
        for part in root.parts[1:]:
            directory = open_directory(stack, directory.path / part, directory, part, internal=True)
        namespace = stack.enter_context(effect_namespace_watch(root, names))
        checked = [float("-inf")]

        def guard(*, check_authority=True):
            namespace()
            if check_authority:
                authority()
            namespace()

        def scan_guard():
            guard(check_authority=False)
            if monotonic() - checked[0] >= 0.2:
                guard()
                checked[0] = monotonic()

        def verify():
            current = history_reader()
            if (
                current.original != history.original
                or current.source_snapshot != history.source_snapshot
            ):
                refuse("GIT_COMMIT_EFFECT_HISTORY_CHANGED")
            validate_metadata(
                current,
                program,
                measure_metadata(policy, scan_guard),
                object_available=lambda index: process.object_available(
                    policy, current.current, program, index
                ),
            )

        guard()
        verify()
        before = measure_metadata(policy, scan_guard)
        for node in before.nodes:
            path = root / node.path
            if node.kind == "directory":
                open_directory(stack, path, internal=True)
            elif node.path not in names:
                stack.enter_context(read_pin(path))
        # Close the acquisition gap before permitting the effect.
        verify()
        guard()
        yield guard
        guard()
        verify()
        guard()
