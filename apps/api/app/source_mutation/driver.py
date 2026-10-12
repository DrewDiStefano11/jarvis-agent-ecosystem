"""Bounded source edits in an already created native owned workspace.

The service owns durable intent/checkpoints and live authority. This driver only
performs measured file effects. Originals remain in operation-specific staging;
uncertain or foreign artifacts are preserved for explicit recovery.
"""

import json
import os
import stat
from contextlib import ExitStack
from hashlib import sha256
from pathlib import Path

from app.core.errors import DomainError
from app.models.development_validation import CodeState, FileChange
from app.models.source_change import (
    MAX_FILE_BYTES,
    FileIdentity,
    SourceContent,
    SourceEntry,
    SourceObservation,
    Topology,
)
from app.self_build.checkout_driver import pin_read_tree, supports_mutation
from app.self_build.git_namespace_watch import namespace_watch
from app.self_build.git_observer import pinned_metadata_file
from app.self_build.policy import digest
from app.self_build.workspace_namespace import owned_namespace
from app.source_change.planner import identity, verify_source_plan
from app.source_mutation.native import pinned_file, read_pin, rename_pinned, staging_file
from app.tool_execution.filesystem import check_stat, open_directory, open_file, read_bytes

RESERVED = {".git", ".jarvis-workspace.json", ".jarvis-workspace.lock"}


def refuse(code):
    raise DomainError(code, "Source mutation failed a required safety check.", 409) from None


def allowed_changes(plan):
    for change in plan.changes:
        if any(
            part.casefold() in RESERVED
            or part.casefold().startswith(".jarvis-")
            or len(part.encode("utf-16-le")) // 2 > 255
            for part in change.path.split("/")
        ):
            refuse("SOURCE_MUTATION_PATH_DENIED")
        # Windows does not provide POSIX executable-bit mutation. Keep inherited
        # Git modes, refuse mode changes, and introduce only ordinary new files.
        if change.after and (
            (change.before and change.before.mode != change.after.mode)
            or (change.before is None and change.after.mode != "100644")
        ):
            refuse("SOURCE_MUTATION_MODE_UNAVAILABLE")


def content(data, mode):
    try:
        return SourceContent(
            digest=sha256(data).hexdigest(),
            byte_count=len(data),
            mode=mode,
            text=data.decode("utf-8"),
        )
    except (ValueError, UnicodeError):
        refuse("SOURCE_MUTATION_CONTENT_INVALID")


def measured(path, mode):
    try:
        with pinned_file(path) as fd:
            data = read_bytes(fd, MAX_FILE_BYTES)
        return FileIdentity(digest=sha256(data).hexdigest(), byte_count=len(data), mode=mode)
    except FileNotFoundError:
        return None


def staging_names(operation_id, index):
    key = sha256(f"{operation_id}:{index}".encode()).hexdigest()
    return ".jarvis-edit-" + key + ".before", ".jarvis-edit-" + key + ".after"


def require_stage_absent(path):
    """Only native file-not-found establishes a missing publication artifact."""
    try:
        with read_pin(path):
            refuse("SOURCE_MUTATION_FOREIGN_ARTIFACT")
    except FileNotFoundError:
        return


def observe(stack, checkout, source_plan, modes, ignored=()):
    """Complete immediate sibling inventory at every selected directory level.

    Never create parents. Retain checked ancestor handles throughout the caller's
    effects. Modes come from the verified native Git inventory, never stat guesses.
    """
    selected = {change.path for change in source_plan.changes}
    directories = {"": checkout}
    for change in source_plan.changes:
        parts = change.path.split("/")
        for depth in range(1, len(parts)):
            prefix = "/".join(parts[:depth])
            if prefix not in directories:
                parent = directories["/".join(parts[: depth - 1])]
                directories[prefix] = open_directory(
                    stack, parent.path / parts[depth - 1], parent, parts[depth - 1], internal=True
                )
    entries = {}
    for prefix, directory in directories.items():
        with os.scandir(directory.path) as listing:
            for item in listing:
                path = prefix + "/" + item.name if prefix else item.name
                if path in ignored:
                    continue
                info = os.stat(directory.name(item.name), follow_symlinks=False)
                kind = "directory" if stat.S_ISDIR(info.st_mode) else "file"
                check_stat(info, directory=kind == "directory", internal=True)
                observed_content = None
                if path in selected and kind == "file":
                    with open_file(directory, item.name, os.O_RDONLY, internal=True) as fd:
                        observed_content = content(read_bytes(fd, MAX_FILE_BYTES), modes[path])
                entries[path] = SourceEntry(
                    path=path, kind=kind, link_count=info.st_nlink, content=observed_content
                )
                if len(entries) > 4096:
                    refuse("SOURCE_MUTATION_INVENTORY_LIMIT")
    for path in selected - entries.keys():
        entries[path] = SourceEntry(path=path, kind="absent")
    try:
        return SourceObservation(
            repository_identity=source_plan.source.repository_identity,
            head_sha=source_plan.source.head_sha,
            base_sha=source_plan.source.base_sha,
            root=Topology(kind="directory"),
            entries=tuple(entries.values()),
        ), directories
    except ValueError:
        refuse("SOURCE_MUTATION_OBSERVATION_INVALID")


def expected_observation(original, changes, completed, *, pending_absent=False):
    entries = {entry.path: entry for entry in original.entries}
    for index, change in enumerate(changes):
        if index < completed:
            entries[change.path] = (
                SourceEntry(path=change.path, kind="file", content=change.after)
                if change.after
                else SourceEntry(path=change.path, kind="absent")
            )
        elif index == completed and pending_absent:
            entries[change.path] = SourceEntry(path=change.path, kind="absent")
    return original.model_copy(
        update={"entries": tuple(sorted(entries.values(), key=lambda e: e.path))}
    )


def measure_code_state(stack, checkout, source, base, plan, ignored, authority_check):
    """Measure the whole bounded checkout without filters, shell or index writes."""
    actual, sizes, total, nodes = {}, {}, 0, 0

    def walk_error(error):
        raise error

    for directory, directories, files in os.walk(
        checkout.path, followlinks=False, onerror=walk_error
    ):
        authority_check()
        parent = open_directory(stack, Path(directory), internal=True)
        nodes += len(directories) + len(files)
        if nodes > 8192:
            refuse("SOURCE_MUTATION_INVENTORY_LIMIT")
        for name in directories:
            open_directory(stack, parent.path / name, parent, name, internal=True)
        for name in files:
            path = (parent.path / name).relative_to(checkout.path).as_posix()
            if path in ignored or path in {".git", ".jarvis-workspace.json"}:
                continue
            if any(part.casefold().startswith(".jarvis-") for part in path.split("/")):
                refuse("SOURCE_MUTATION_FOREIGN_ARTIFACT")
            with open_file(parent, name, os.O_RDONLY, internal=True) as fd:
                data = read_bytes(fd, plan.maximum_file_bytes)
            total += len(data)
            if total > plan.maximum_total_bytes or len(actual) >= 4096:
                refuse("SOURCE_MUTATION_INVENTORY_LIMIT")
            actual[path] = (sha256(data).hexdigest(), base.get(path, (None, "100644"))[1])
            sizes[path] = len(data)
    changes = tuple(
        FileChange(
            path=path,
            before_hash=base[path][0] if path in base else None,
            before_mode=base[path][1] if path in base else None,
            after_hash=actual[path][0] if path in actual else None,
            after_mode=actual[path][1] if path in actual else None,
        )
        for path in sorted(base.keys() | actual.keys())
        if base.get(path) != actual.get(path)
    )
    return CodeState(
        repository_identity=source.repository_identity,
        head_sha=source.head_sha,
        base_sha=source.base_sha,
        changes=changes,
    ), sizes


def check_target_budget(sizes, changes, maximum_bytes):
    projected = dict(sizes)
    for change in changes:
        if change.after is None:
            projected.pop(change.path, None)
        else:
            projected[change.path] = change.after.byte_count
    if len(projected) > 4096 or sum(projected.values()) > maximum_bytes:
        refuse("SOURCE_MUTATION_INVENTORY_LIMIT")


def pin_source_tree(stack, target, authority_check):
    check = stack.enter_context(namespace_watch(target))
    nodes = 0

    def walk_error(error):
        raise error

    for parent, directories, files in os.walk(target, followlinks=False, onerror=walk_error):
        authority_check()
        nodes += len(directories) + len(files)
        if nodes > 8192:
            refuse("SOURCE_MUTATION_INVENTORY_LIMIT")
        for name in directories:
            open_directory(stack, Path(parent) / name, internal=True)
        for name in files:
            stack.enter_context(read_pin(Path(parent) / name))
    check()
    return check


def verify_base_index(driver, policy, creation_plan, target):
    raw = driver.invoke(policy, "index_inventory", creation_plan, target)
    entries = []
    try:
        for record in raw.split(b"\0"):
            if not record:
                continue
            header, path = record.split(b"\t", 1)
            mode, blob, stage = header.decode("ascii").split()
            if stage != "0":
                raise ValueError("unmerged index")
            entries.append([mode, blob, path.decode("utf-8")])
    except (ValueError, UnicodeError):
        refuse("SOURCE_MUTATION_INDEX_CONFLICT")
    if digest(entries) != creation_plan.inventory_digest:
        refuse("SOURCE_MUTATION_INDEX_CONFLICT")


def verify_creation_marker(checkout, creation_plan):
    expected = json.dumps(
        dict(
            schemaVersion="1.0",
            workspaceId=creation_plan.workspace_id,
            allowedTools=list(creation_plan.allowed_tools),
            readPrefixes=list(creation_plan.read_prefixes),
            writePrefixes=list(creation_plan.write_prefixes),
            planHash=creation_plan.plan_hash,
        ),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    with open_file(checkout, ".jarvis-workspace.json", os.O_RDONLY, internal=True) as fd:
        if read_bytes(fd, 8192) != expected:
            refuse("SOURCE_MUTATION_MARKER_CONFLICT")


class SourceMutationDriver:
    def __init__(self, checkout_driver):
        self.checkout_driver = checkout_driver
        self.authority_check = checkout_driver.authority_check

    def apply(
        self,
        policy,
        creation_plan,
        owner,
        source_plan,
        observation,
        operation_id,
        *,
        original_code_state,
        expected_code_state,
        completed,
        pending=None,
        staging_identity=None,
        claim_stage,
        begin,
        acknowledge,
        finalize,
    ):
        if not supports_mutation():
            refuse("SOURCE_MUTATION_PLATFORM_UNAVAILABLE")
        verify_source_plan(source_plan, observation)
        allowed_changes(source_plan)
        tool = self.checkout_driver.observer
        if (
            tool.executable_hash != creation_plan.tool_sha256
            or digest([str(tool.executable), tool.executable_hash])
            != creation_plan.tool_identity_digest
        ):
            refuse("SOURCE_MUTATION_TOOL_CHANGED")
        if (
            source_plan.source.repository_identity != policy.repository_identity
            or source_plan.source.head_sha != creation_plan.workspace.base_sha
            or source_plan.source.base_sha != creation_plan.workspace.base_sha
        ):
            refuse("SOURCE_MUTATION_SOURCE_CONFLICT")
        self.authority_check()
        try:
            return self._apply(
                policy,
                creation_plan,
                owner,
                source_plan,
                observation,
                operation_id,
                original_code_state=original_code_state,
                expected_code_state=expected_code_state,
                completed=completed,
                pending=pending,
                staging_identity=staging_identity,
                claim_stage=claim_stage,
                begin=begin,
                acknowledge=acknowledge,
                finalize=finalize,
            )
        except OSError:
            refuse("SOURCE_MUTATION_NATIVE_CONFLICT")
        except (ValueError, KeyError):
            refuse("SOURCE_MUTATION_OBSERVATION_INVALID")

    def _apply(
        self,
        policy,
        creation_plan,
        owner,
        source_plan,
        observation,
        operation_id,
        *,
        original_code_state,
        expected_code_state,
        completed,
        pending,
        staging_identity,
        claim_stage,
        begin,
        acknowledge,
        finalize,
    ):
        driver = self.checkout_driver
        root_path = Path(policy.worktree_root)
        target = root_path / creation_plan.workspace_id
        private = root_path / (".jarvis-owner-" + creation_plan.workspace_id)
        # owned_namespace is also a creation primitive. Require existing pinned
        # owner/target/claim before entering it, so editing can never claim a root.
        with ExitStack() as initial:
            directory = open_directory(initial, Path(root_path.anchor), internal=True)
            for leaf in root_path.parts[1:]:
                directory = open_directory(
                    initial, directory.path / leaf, directory, leaf, internal=True
                )
            open_directory(initial, target, directory, target.name, internal=True)
            open_directory(initial, private, directory, private.name, internal=True)
            initial.enter_context(pinned_metadata_file(private / "owner.json"))
            initial.enter_context(pinned_metadata_file(private / "target.json"))
            with owned_namespace(
                policy,
                creation_plan.workspace_id,
                owner,
                authority_check=self.authority_check,
                validate_existing_target=lambda stack, root, owned: driver.validate_existing_target(
                    stack, root, owned, policy, creation_plan
                ),
            ) as (stack, root, owned, _):
                checkout = open_directory(stack, owned, root, owned.name, internal=True)
                common = Path(policy.primary_root) / ".git"
                check_metadata = pin_read_tree(
                    stack,
                    common,
                    self.authority_check,
                    maximum=creation_plan.maximum_initial_metadata_nodes
                    + creation_plan.metadata_growth_reserve,
                )
                stack.enter_context(pinned_metadata_file(owned / ".git"))
                stack.enter_context(pinned_metadata_file(owned / ".jarvis-workspace.json"))
                verify_creation_marker(checkout, creation_plan)
                verify_base_index(driver, policy, creation_plan, owned)
                modes = {
                    change.path: change.before.mode if change.before else change.after.mode
                    for change in source_plan.changes
                }
                from app.self_build.materialization import inventory

                base_inventory = inventory(driver, policy, creation_plan, owned)
                base_modes = {path: mode for mode, _, path, _ in base_inventory}
                base = {}
                from app.self_build.materialization import blob_digest

                for mode, blob, path, size in base_inventory:
                    data = driver.invoke(policy, "blob", creation_plan, owned, blob=blob)
                    if len(data) != size or blob_digest(data) != blob:
                        refuse("SOURCE_MUTATION_BASE_CONFLICT")
                    base[path] = (sha256(data).hexdigest(), mode)
                if any(
                    change.before and change.before.mode != base_modes.get(change.path, "100644")
                    for change in source_plan.changes
                ):
                    refuse("SOURCE_MUTATION_MODE_CONFLICT")
                ignored = set()
                for index, change in enumerate(source_plan.changes):
                    parent = change.path.rpartition("/")[0]
                    for name in staging_names(operation_id, index):
                        ignored.add(parent + "/" + name if parent else name)
                original_deltas = {entry.path: entry for entry in original_code_state.changes}
                for delta in original_code_state.changes:
                    if (delta.before_hash, delta.before_mode) != base.get(delta.path, (None, None)):
                        refuse("SOURCE_MUTATION_BASE_CONFLICT")
                for change in source_plan.changes:
                    original = original_deltas.get(change.path)
                    expected_before = (
                        (original.after_hash, original.after_mode)
                        if original
                        else base.get(change.path, (None, None))
                    )
                    if expected_before != (
                        (change.before.digest, change.before.mode)
                        if change.before
                        else (None, None)
                    ):
                        refuse("SOURCE_MUTATION_BASE_CONFLICT")
                measured_state, measured_sizes = measure_code_state(
                    stack,
                    checkout,
                    source_plan.source,
                    base,
                    creation_plan,
                    ignored,
                    self.authority_check,
                )
                check_target_budget(
                    measured_sizes, source_plan.changes, creation_plan.maximum_total_bytes
                )
                unrelated = tuple(
                    delta for delta in measured_state.changes if delta.path not in modes
                )
                expected_unrelated = tuple(
                    delta for delta in original_code_state.changes if delta.path not in modes
                )
                if unrelated != expected_unrelated:
                    refuse("SOURCE_MUTATION_BEFORE_CONFLICT")
                # Pin every observed unrelated file against foreign content edits.
                for entry in observation.entries:
                    if (
                        entry.kind == "file"
                        and entry.path not in modes
                        and entry.path != ".git"
                        and entry.path != ".jarvis-workspace.json"
                    ):
                        stack.enter_context(read_pin(owned / entry.path))
                for index, change in enumerate(source_plan.changes):
                    self.authority_check()
                    backup, stage = staging_names(operation_id, index)
                    parent_path = owned.joinpath(*change.path.split("/")[:-1])
                    if index < completed or change.after is None:
                        require_stage_absent(parent_path / stage)
                    backup_identity = measured(parent_path / backup, modes[change.path])
                    if backup_identity is not None and backup_identity != change.before:
                        refuse("SOURCE_MUTATION_BACKUP_CONFLICT")
                    actual = measured(owned / change.path, modes[change.path])
                    after = identity(change.after)
                    if index < completed:
                        if actual != after or (
                            change.before is not None and backup_identity != change.before
                        ):
                            refuse("SOURCE_MUTATION_RECOVERY_CONFLICT")
                        continue
                    if index != pending and (
                        backup_identity is not None or actual != change.before
                    ):
                        refuse("SOURCE_MUTATION_BEFORE_CONFLICT")
                    observed, directories = observe(stack, checkout, source_plan, modes, ignored)
                    expected = expected_observation(observation, source_plan.changes, index)
                    if actual == after and (
                        change.before is None or backup_identity == change.before
                    ):
                        expected = expected_observation(observation, source_plan.changes, index + 1)
                    elif (
                        actual is None
                        and backup_identity == change.before
                        and change.before is not None
                    ):
                        expected = expected_observation(
                            observation, source_plan.changes, index, pending_absent=True
                        )
                    if observed != expected:
                        refuse("SOURCE_MUTATION_BEFORE_CONFLICT")
                    parent = directories[change.path.rpartition("/")[0]]
                    # The callback commits a per-file intent and native checkpoint
                    # BEFORE any staging, original rename or destination effect.
                    begin(index)
                    self._file(
                        parent,
                        change,
                        backup,
                        stage,
                        staging_identity if index == pending else None,
                        lambda device_inode, index=index: claim_stage(index, device_inode),
                    )
                    acknowledge(
                        index + 1,
                        digest(
                            [
                                item.model_dump(mode="json")
                                for item in source_plan.preview[: index + 1]
                            ]
                        ),
                    )
                check_source = pin_source_tree(stack, owned, self.authority_check)
                # Recheck after the final namespace watch is armed. A foreign
                # stage may have appeared after a completed-prefix probe; never
                # silently exclude it from the completion measurement.
                for index, change in enumerate(source_plan.changes):
                    _, stage = staging_names(operation_id, index)
                    require_stage_absent(owned.joinpath(*change.path.split("/")[:-1], stage))
                final, _ = observe(stack, checkout, source_plan, modes, ignored)
                final_state, _ = measure_code_state(
                    stack,
                    checkout,
                    source_plan.source,
                    base,
                    creation_plan,
                    ignored,
                    self.authority_check,
                )
                if final_state != expected_code_state:
                    refuse("SOURCE_MUTATION_AFTER_CONFLICT")
                if final != expected_observation(
                    observation, source_plan.changes, len(source_plan.changes)
                ):
                    refuse("SOURCE_MUTATION_AFTER_CONFLICT")

                def publication_guard():
                    check_metadata()
                    check_source()

                publication_guard()
                self.authority_check()
                return finalize(digest(final.model_dump(mode="json")), publication_guard)

    def _file(self, parent, change, backup, stage, staging_identity, claim_stage):
        before, after = change.before, identity(change.after)
        leaf = change.path.split("/")[-1]
        current = measured(parent.path / leaf, (before or after).mode)
        saved = measured(parent.path / backup, (before or after).mode)
        if saved is not None and saved != before:
            refuse("SOURCE_MUTATION_BACKUP_CONFLICT")
        if current == after and (before is None or saved == before):
            require_stage_absent(parent.path / stage)
            if after is not None:
                # A pending publication is owned only by the stage identity in
                # the validated checkpoint. Matching bytes alone grant nothing.
                if staging_identity is None:
                    refuse("SOURCE_MUTATION_STAGE_CONFLICT")
                with pinned_file(parent.path / leaf) as fd:
                    info = os.fstat(fd)
                    if (info.st_dev, info.st_ino) != staging_identity:
                        refuse("SOURCE_MUTATION_STAGE_CONFLICT")
                    if content(read_bytes(fd, MAX_FILE_BYTES), after.mode) != change.after:
                        refuse("SOURCE_MUTATION_AFTER_CONFLICT")
            return
        if current != before and not (current is None and saved == before and before is not None):
            refuse("SOURCE_MUTATION_BEFORE_CONFLICT")
        if change.after:
            data = change.after.text.encode("utf-8")
            self.authority_check()
            with staging_file(parent.path / stage, staging_identity) as (fd, device_inode):
                if staging_identity is None:
                    claim_stage(device_inode)
                partial = read_bytes(fd, MAX_FILE_BYTES)
                if partial != data[: len(partial)]:
                    refuse("SOURCE_MUTATION_STAGE_CONFLICT")
                position = len(partial)
                while position < len(data):
                    self.authority_check()
                    written = os.write(fd, data[position : position + 8192])
                    if written <= 0:
                        raise OSError("Source staging write made no progress")
                    position += written
                os.fsync(fd)
            if measured(parent.path / stage, after.mode) != after:
                refuse("SOURCE_MUTATION_STAGE_CONFLICT")
        if current is not None:
            if saved is not None:
                refuse("SOURCE_MUTATION_RECOVERY_CONFLICT")
            with pinned_file(parent.path / leaf) as fd:
                data = read_bytes(fd, MAX_FILE_BYTES)
                actual = FileIdentity(
                    digest=sha256(data).hexdigest(), byte_count=len(data), mode=before.mode
                )
                if actual != before:
                    refuse("SOURCE_MUTATION_BEFORE_CONFLICT")
                self.authority_check()
                rename_pinned(fd, backup)
        if change.after:
            with pinned_file(parent.path / stage) as fd:
                staged_info = os.fstat(fd)
                if (staged_info.st_dev, staged_info.st_ino) != device_inode:
                    refuse("SOURCE_MUTATION_STAGE_CONFLICT")
                data = read_bytes(fd, MAX_FILE_BYTES)
                if content(data, after.mode) != change.after:
                    refuse("SOURCE_MUTATION_STAGE_CONFLICT")
                self.authority_check()
                rename_pinned(fd, leaf)
        if measured(parent.path / leaf, (before or after).mode) != after:
            refuse("SOURCE_MUTATION_AFTER_CONFLICT")
