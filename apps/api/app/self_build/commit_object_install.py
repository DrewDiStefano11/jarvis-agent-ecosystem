"""Native deterministic loose-object install; no Git temp names or cleanup."""

import os
import zlib
from contextlib import ExitStack
from pathlib import Path

from app.models.git_execution import NativeCommitRecord
from app.self_build.commit_namespace_watch import effect_namespace_watch
from app.self_build.commit_object_directory import object_directory
from app.self_build.commit_process import refuse
from app.source_mutation.native import pinned_file, read_pin, rename_pinned, staging_file
from app.tool_execution.filesystem import open_directory, open_file, read_bytes, windows_native_path


def object_stage_name(record, item):
    return ".jarvis-" + record.operation_id + "-" + item.sha[2:] + ".after"


def compressed_object(item):
    if (
        item.kind not in {"blob", "tree", "commit"}
        or not isinstance(item.payload, bytes)
        or len(item.payload) > 8388608
    ):
        raise ValueError("only a bounded compiled Git object can be installed")
    return zlib.compress(
        item.kind.encode("ascii")
        + b" "
        + str(len(item.payload)).encode("ascii")
        + b"\0"
        + item.payload,
        6,
    )


def exact_loose_object(data, item):
    expected = (
        item.kind.encode("ascii")
        + b" "
        + str(len(item.payload)).encode("ascii")
        + b"\0"
        + item.payload
    )
    decoder = zlib.decompressobj()
    try:
        decoded = decoder.decompress(data, len(expected) + 1)
    except zlib.error:
        refuse("GIT_COMMIT_OBJECT_CONTENT_INVALID")
    if decoded != expected or not decoder.eof or decoder.unconsumed_tail or decoder.unused_data:
        refuse("GIT_COMMIT_OBJECT_CONTENT_INVALID")


def install_object(
    policy,
    record,
    program,
    process,
    claim_directory,
    claim_stage,
    authority_check,
    *,
    expected_directory=None,
):
    """Caller fences complete primary/shared metadata and resolves live authority.

    Directory and stage claims commit actual native inode receipts before payload
    writing. expected_directory is derived from approved metadata or prior real
    journal receipts, never a caller/model path. No existing unclaimed directory
    or stage is adopted; uncertain artifacts remain. Content-addressed objects
    may be reused after exact native Git verification, without cleanup ownership.
    """
    record, policy, _ = process._validated(policy, record, program)
    index = record.pending_object_index
    if record.state != "prepared" or index is None or record.staging_inode is None:
        refuse("GIT_COMMIT_OBJECT_INTENT_REQUIRED")
    item = program.objects[index]
    encoded = compressed_object(item)
    prefix, leaf = item.sha[:2], item.sha[2:]
    stage_name = object_stage_name(record, item)
    path = Path(policy.primary_root) / ".git/objects"
    authority_check(record)
    try:
        with ExitStack() as stack:
            private_path = Path(policy.worktree_root) / (".jarvis-owner-" + record.workspace_id)
            private = open_directory(stack, Path(private_path.anchor), internal=True)
            for part in private_path.parts[1:]:
                private = open_directory(stack, private.path / part, private, part, internal=True)
            raw_name = record.operation_id + "-" + item.sha + ".payload"
            stack.enter_context(read_pin(private.path / raw_name))
            with open_file(private, raw_name, os.O_RDONLY, internal=True) as fd:
                info = os.fstat(fd)
                if (info.st_dev, info.st_ino) != (
                    record.staging_device,
                    record.staging_inode,
                ) or read_bytes(fd, len(item.payload)) != item.payload:
                    refuse("GIT_COMMIT_PAYLOAD_OWNERSHIP_INVALID")
            parent = open_directory(stack, Path(path.anchor), internal=True)
            for part in path.parts[1:]:
                parent = open_directory(stack, parent.path / part, parent, part, internal=True)
            names = frozenset({prefix, prefix + "/" + stage_name, prefix + "/" + leaf})
            namespace_check = stack.enter_context(effect_namespace_watch(path, names))
            count = 0

            def walk_error(error):
                raise error

            for root, directories, files in os.walk(path, followlinks=False, onerror=walk_error):
                authority_check(record)
                namespace_check()
                count += len(directories) + len(files)
                if count > 16384:
                    refuse("GIT_COMMIT_OBJECT_STORAGE_LIMIT")
                for name in directories:
                    open_directory(stack, Path(root) / name, internal=True)
                for name in files:
                    entry = Path(root) / name
                    if entry.relative_to(path).as_posix() != prefix + "/" + stage_name:
                        stack.enter_context(read_pin(entry))

            def guard(current):
                namespace_check()
                authority_check(current)
                namespace_check()

            guard(record)
            available = process.object_available(policy, record, program, index)
            if available and record.object_store_stage_inode is None:
                if os.path.lexists(windows_native_path(path / prefix / stage_name)):
                    refuse("GIT_COMMIT_OBJECT_STAGE_OWNERSHIP_INVALID")
                if record.object_directory_inode is not None:
                    _, actual = object_directory(stack, parent, prefix, create=False)
                    if actual != (record.object_directory_device, record.object_directory_inode):
                        refuse("GIT_COMMIT_OBJECT_DIRECTORY_INVALID")
                guard(record)
                return record
            if available and not os.path.lexists(windows_native_path(path / prefix / stage_name)):
                # The rename may already have completed before the journal ack.
                _, actual = object_directory(stack, parent, prefix, create=False)
                if actual != (record.object_directory_device, record.object_directory_inode):
                    refuse("GIT_COMMIT_OBJECT_DIRECTORY_INVALID")
                # The installed object already has an immutable read pin above.
                # Recovery observes it; requesting DELETE would conflict with
                # that fence and would grant unnecessary rename capability.
                with pinned_file(path / prefix / leaf, rename=False) as fd:
                    info = os.fstat(fd)
                    if (info.st_dev, info.st_ino) != (
                        record.object_store_stage_device,
                        record.object_store_stage_inode,
                    ):
                        refuse("GIT_COMMIT_OBJECT_STAGE_OWNERSHIP_INVALID")
                    exact_loose_object(read_bytes(fd, len(encoded)), item)
                guard(record)
                return record
            prior = (record.object_directory_device, record.object_directory_inode)
            if prior[0] is not None:
                directory, actual = object_directory(stack, parent, prefix, create=False)
                if actual != prior:
                    refuse("GIT_COMMIT_OBJECT_DIRECTORY_INVALID")
            else:
                directory, actual = object_directory(
                    stack, parent, prefix, create=expected_directory is None
                )
                if expected_directory is not None and actual != expected_directory:
                    refuse("GIT_COMMIT_OBJECT_DIRECTORY_INVALID")
                claimed = claim_directory(record, *actual)
                claimed = NativeCommitRecord.model_validate(claimed.model_dump(mode="json"))
                if claimed != record.model_copy(
                    update=dict(object_directory_device=actual[0], object_directory_inode=actual[1])
                ):
                    refuse("GIT_COMMIT_OBJECT_DIRECTORY_CLAIM_INVALID")
                record = claimed
            guard(record)
            stage_path = directory / stage_name
            identity = (record.object_store_stage_device, record.object_store_stage_inode)
            with staging_file(stage_path, None if identity[0] is None else identity) as (
                fd,
                actual,
            ):
                if identity[0] is None:
                    claimed = claim_stage(record, *actual)
                    claimed = NativeCommitRecord.model_validate(claimed.model_dump(mode="json"))
                    if claimed != record.model_copy(
                        update=dict(
                            object_store_stage_device=actual[0], object_store_stage_inode=actual[1]
                        )
                    ):
                        refuse("GIT_COMMIT_OBJECT_STAGE_CLAIM_INVALID")
                    record = claimed
                guard(record)
                current = read_bytes(fd, len(encoded))
                if encoded[: len(current)] != current:
                    refuse("GIT_COMMIT_OBJECT_STAGE_PREFIX_INVALID")
                position = len(current)
                os.lseek(fd, position, os.SEEK_SET)
                while position < len(encoded):
                    guard(record)
                    written = os.write(fd, encoded[position : position + 4096])
                    if written <= 0:
                        refuse("GIT_COMMIT_OBJECT_STAGE_WRITE_UNCERTAIN")
                    position += written
                os.fsync(fd)
            with pinned_file(stage_path) as fd:
                info = os.fstat(fd)
                if (info.st_dev, info.st_ino) != (
                    record.object_store_stage_device,
                    record.object_store_stage_inode,
                ):
                    refuse("GIT_COMMIT_OBJECT_STAGE_OWNERSHIP_INVALID")
                exact_loose_object(read_bytes(fd, len(encoded)), item)
                guard(record)
                # Existing object paths remain pinned. No overwrite, removal,
                # cleanup or hash-only ownership claim is ever attempted.
                if os.path.lexists(windows_native_path(directory / leaf)):
                    if not process.object_available(policy, record, program, index):
                        refuse("GIT_COMMIT_OBJECT_DESTINATION_UNCERTAIN")
                else:
                    rename_pinned(fd, leaf)
                guard(record)
            if not process.object_available(policy, record, program, index):
                refuse("GIT_COMMIT_OBJECT_ACKNOWLEDGEMENT_INVALID")
            guard(record)
            return record
    except OSError:
        refuse("GIT_COMMIT_OBJECT_OWNERSHIP_UNCERTAIN")
