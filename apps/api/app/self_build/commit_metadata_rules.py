"""Interpret measured Git metadata against complete protected commit history.

No mutations or execution grant. The caller resolves the history from its journal,
retains native source/metadata pins and separately checks current live authority.
"""

from hashlib import sha256

from app.models.git_commit_history import NativeCommitHistory
from app.models.git_commit_metadata import CommitMetadataSnapshot
from app.self_build.commit_index_install import index_names
from app.self_build.commit_object_install import compressed_object, object_stage_name
from app.self_build.commit_process import refuse
from app.self_build.commit_progress import apply_progress


def claims(history):
    """Recover earlier claims even after the live cursor clears staging fields."""
    projected = history.original.model_copy(
        update={"intent_checkpoint_id": history.current.intent_checkpoint_id}
    )
    objects = {}
    for entry in history.progress:
        index = projected.pending_object_index
        projected = apply_progress(projected, entry)
        if index is not None and entry.action in {
            "object_stage",
            "object_directory",
            "object_store_stage",
        }:
            objects.setdefault(index, {})[entry.action] = (entry.device, entry.inode)
    return objects


def validate_metadata(history, program, measured, *, read_reflog=None, object_available=None):
    history = NativeCommitHistory.model_validate(history.model_dump(mode="json"))
    measured = CommitMetadataSnapshot.model_validate(measured.model_dump(mode="json"))
    record = history.current
    proposal = record.plan.preparation.plan.proposal
    if (
        program.fingerprint != record.plan.object_program_fingerprint
        or len(program.objects) != record.plan.object_count
        or program.tree_sha != proposal.prospective_tree_sha
        or program.commit_sha != proposal.prospective_commit_sha
        or sha256(program.index_payload).hexdigest() != record.plan.index_payload_sha256
        or (measured.root_device, measured.root_inode)
        != (history.metadata_snapshot.root_device, history.metadata_snapshot.root_inode)
    ):
        refuse("GIT_COMMIT_METADATA_SCOPE_CHANGED")
    original = {node.path: node for node in history.metadata_snapshot.nodes}
    actual = {node.path: node for node in measured.nodes}
    expected = set(original)
    mutable = set()
    receipts = claims(history)

    def identity(node):
        return (node.device, node.inode)

    def exact_payload(node, content, receipt, *, partial=False):
        return (
            node is not None
            and node.kind == "file"
            and receipt is not None
            and identity(node) == receipt
            and (node.byte_count <= len(content) if partial else node.byte_count == len(content))
            and node.sha256 == sha256(content[: node.byte_count]).hexdigest()
        )

    for index, item in enumerate(program.objects):
        own = receipts.get(index, {})
        directory = "objects/" + item.sha[:2]
        target = directory + "/" + item.sha[2:]
        stage = directory + "/" + object_stage_name(record, item)
        directory_claim = own.get("object_directory")
        if directory_claim is not None:
            node = actual.get(directory)
            if node is None or node.kind != "directory" or identity(node) != directory_claim:
                refuse("GIT_COMMIT_METADATA_DIRECTORY_CHANGED")
            if directory in original and node != original[directory]:
                refuse("GIT_COMMIT_METADATA_DIRECTORY_CHANGED")
            expected.add(directory)
        encoded = compressed_object(item)
        store_claim = own.get("object_store_stage")
        if target in original:
            if actual.get(target) != original[target]:
                refuse("GIT_COMMIT_METADATA_OBJECT_CHANGED")
        elif target in actual:
            if not exact_payload(actual[target], encoded, store_claim):
                refuse("GIT_COMMIT_METADATA_OBJECT_CHANGED")
            expected.add(target)
            # An installed new object must retain its real claimed directory.
            if directory_claim is None:
                refuse("GIT_COMMIT_METADATA_DIRECTORY_CHANGED")
        elif index < record.completed_object_count and (
            object_available is None or object_available(index) is not True
        ):
            refuse("GIT_COMMIT_METADATA_OBJECT_MISSING")
        if stage in actual:
            if not exact_payload(actual[stage], encoded, store_claim, partial=True):
                refuse("GIT_COMMIT_METADATA_STAGE_CHANGED")
            expected.add(stage)
            if directory_claim is None:
                refuse("GIT_COMMIT_METADATA_DIRECTORY_CHANGED")
        elif store_claim is not None and target not in actual:
            refuse("GIT_COMMIT_METADATA_STAGE_MISSING")
        if index < record.completed_object_count and stage in actual:
            # A retained collision stage is permitted only when the original
            # content-addressed object existed before execution; never silently
            # adopt a second unknown destination after a claimed rename.
            if target not in original:
                refuse("GIT_COMMIT_METADATA_STAGE_UNCERTAIN")

    index_path = "worktrees/" + record.workspace_id + "/index"
    original_index = original.get(index_path)
    if (
        original_index is None
        or original_index.kind != "file"
        or identity(original_index)
        != (record.plan.original_index_device, record.plan.original_index_inode)
        or original_index.sha256 != record.plan.original_index_sha256
    ):
        refuse("GIT_COMMIT_METADATA_INDEX_ORIGIN_CHANGED")
    before, after = index_names(record)
    backup_path = "worktrees/" + record.workspace_id + "/" + before
    stage_path = "worktrees/" + record.workspace_id + "/" + after
    if record.state in {
        "index_prepared",
        "indexed",
        "reference_prepared",
        "committed",
        "checkpointed",
    }:
        mutable.add(index_path)
        current_index, backup, stage = (
            actual.get(index_path),
            actual.get(backup_path),
            actual.get(stage_path),
        )
        owned = (
            None
            if record.index_stage_inode is None
            else (record.index_stage_device, record.index_stage_inode)
        )
        original_present = current_index == original_index
        backup_present = backup == original_index.model_copy(update={"path": backup_path})
        candidate_present = exact_payload(current_index, program.index_payload, owned)
        stage_present = exact_payload(stage, program.index_payload, owned, partial=True)
        valid_original = (
            original_present
            and backup is None
            and ((stage is None and owned is None) or stage_present)
        )
        valid_gap = (
            current_index is None
            and backup_present
            and exact_payload(stage, program.index_payload, owned)
        )
        valid_installed = candidate_present and backup_present and stage is None
        if not (valid_original or valid_gap or valid_installed) or (
            record.state != "index_prepared" and not valid_installed
        ):
            refuse("GIT_COMMIT_METADATA_INDEX_CHANGED")
        if current_index is None:
            expected.discard(index_path)
        if backup is not None:
            expected.add(backup_path)
        if stage is not None:
            expected.add(stage_path)

    reference = "refs/heads/" + proposal.branch
    if (
        reference not in original
        or original[reference].sha256 != sha256((proposal.parent_sha + "\n").encode()).hexdigest()
    ):
        refuse("GIT_COMMIT_METADATA_REFERENCE_ORIGIN_CHANGED")
    if record.state in {"reference_prepared", "committed", "checkpointed"}:
        mutable.add(reference)
        node = actual.get(reference)
        changed = (
            node is not None
            and node.kind == "file"
            and node.byte_count == 41
            and node.sha256 == sha256((program.commit_sha + "\n").encode()).hexdigest()
        )
        if not (node == original[reference] or changed) or (
            record.state != "reference_prepared" and not changed
        ):
            refuse("GIT_COMMIT_METADATA_REFERENCE_CHANGED")
        if changed and identity(node) == identity(original[reference]):
            refuse("GIT_COMMIT_METADATA_REFERENCE_INODE_UNCERTAIN")
        person = proposal.metadata.committer
        line = f"{proposal.parent_sha} {program.commit_sha} {person.name} <{person.email}> {person.timestamp} {person.timezone}\t{record.operation_id}\n".encode()
        for path in ("logs/" + reference, "worktrees/" + record.workspace_id + "/logs/HEAD"):
            before_log, current_log = original.get(path), actual.get(path)
            if current_log == before_log:
                continue
            if current_log is None or current_log.kind != "file" or read_reflog is None:
                refuse("GIT_COMMIT_METADATA_REFLOG_UNCONFIRMED")
            raw = read_reflog(path)
            if (
                not isinstance(raw, bytes)
                or len(raw) != current_log.byte_count
                or sha256(raw).hexdigest() != current_log.sha256
            ):
                refuse("GIT_COMMIT_METADATA_REFLOG_CHANGED")
            prefix_size = before_log.byte_count if before_log is not None else 0
            if before_log is not None and (
                identity(current_log) != identity(before_log)
                or sha256(raw[:prefix_size]).hexdigest() != before_log.sha256
            ):
                refuse("GIT_COMMIT_METADATA_REFLOG_CHANGED")
            suffix = raw[prefix_size:]
            if (
                not suffix
                or len(suffix) % len(line)
                or not 1 <= len(suffix) // len(line) <= 32
                or suffix != line * (len(suffix) // len(line))
            ):
                refuse("GIT_COMMIT_METADATA_REFLOG_CHANGED")
            mutable.add(path)
            expected.add(path)

    if set(actual) != expected:
        refuse("GIT_COMMIT_METADATA_NAMESPACE_CHANGED")
    for path, node in original.items():
        if path not in mutable and actual.get(path) != node:
            refuse("GIT_COMMIT_METADATA_IMMUTABLE_CHANGED")
    return tuple(sorted(mutable))
