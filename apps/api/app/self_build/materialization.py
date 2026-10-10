"""Private bounded raw-blob materialization. No checkout filters or model tool."""

import ctypes
import json
import os
from contextlib import ExitStack
from ctypes import wintypes
from hashlib import sha1, sha256
from pathlib import Path

from app.self_build.checkout_driver import fail, supports_mutation
from app.self_build.git_observer import COMMIT
from app.self_build.policy import digest
from app.self_build.workspace_namespace import owned_namespace
from app.tool_execution.filesystem import (
    open_directory,
    open_file,
    read_bytes,
    repository_parts,
    windows_native_path,
)

RESERVED = {".jarvis-workspace.json", ".jarvis-workspace.lock"}


def inventory(driver, policy, plan, target):
    raw = driver.invoke(policy, "inventory", plan, target)
    entries, names, total = [], set(), 0
    try:
        for record in raw.split(b"\0"):
            if not record:
                continue
            header, name = record.split(b"\t", 1)
            mode, kind, blob, size = header.decode("ascii").split()
            path = name.decode("utf-8")
            segments = repository_parts(path)
            length = int(size)
            if (
                kind != "blob"
                or mode not in {"100644", "100755"}
                or not COMMIT.fullmatch(blob)
                or length < 0
                or length > plan.maximum_file_bytes
                or len(segments) > 16
                or any(part.casefold() in RESERVED for part in segments)
                or path.casefold() in names
            ):
                fail(
                    "SELF_BUILD_MATERIALIZATION_UNSAFE",
                    "The base inventory exceeds its protected materialization scope.",
                )
            names.add(path.casefold())
            total += length
            entries.append((mode, blob, path, length))
            if len(entries) > plan.file_count or total > plan.maximum_total_bytes:
                fail(
                    "SELF_BUILD_MATERIALIZATION_LIMIT",
                    "The base tree exceeds its approved count or byte budget.",
                )
    except (ValueError, UnicodeError):
        fail("SELF_BUILD_MATERIALIZATION_UNSAFE", "The base inventory is malformed.")
    if (
        len(entries) != plan.file_count
        or digest([list(item[:3]) for item in entries]) != plan.inventory_digest
    ):
        fail(
            "SELF_BUILD_REINSPECTION_REQUIRED",
            "Raw materialization requires the approved exact inventory.",
        )
    return entries


def blob_digest(content):
    return sha1(
        b"blob " + str(len(content)).encode() + b"\0" + content, usedforsecurity=False
    ).hexdigest()


def move_without_replace(source, target):
    # Only Windows mutation is supported. No replacement, cross-volume copy,
    # reboot scheduling, deletion or arbitrary user-selected destination is used.
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    move = kernel.MoveFileExW
    move.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    move.restype = wintypes.BOOL
    if not move(windows_native_path(source), windows_native_path(target), 8):
        raise OSError(ctypes.get_last_error(), "Owned file acknowledgement failed")


def write_file(driver, root, private, path, content):
    segments = repository_parts(path)
    with ExitStack() as stack:
        parent = root
        for leaf in segments[:-1]:
            driver.authority_check()
            parent = open_directory(
                stack, parent.path / leaf, parent, leaf, create=True, internal=True
            )
        leaf = segments[-1]
        try:
            with open_file(parent, leaf, os.O_RDONLY, internal=True) as fd:
                existing = read_bytes(fd, len(content))
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if existing != content:
                fail(
                    "SELF_BUILD_MATERIALIZATION_CONFLICT",
                    "An existing workspace file differs from approved content; preserve it for recovery.",
                )
            return
        stage = "stage-" + sha256(content).hexdigest()
        driver.authority_check()
        with open_file(private, stage, os.O_RDWR | os.O_CREAT, internal=True) as fd:
            partial = read_bytes(fd, len(content))
            if partial != content[: len(partial)]:
                fail(
                    "SELF_BUILD_MATERIALIZATION_CONFLICT",
                    "Owned staging content is inconsistent; preserve it for recovery.",
                )
            os.lseek(fd, len(partial), os.SEEK_SET)
            position = len(partial)
            while position < len(content):
                driver.authority_check()
                written = os.write(fd, content[position : position + 65536])
                if written <= 0:
                    raise OSError("Owned staging write made no progress")
                position += written
            os.fsync(fd)
            os.lseek(fd, 0, os.SEEK_SET)
            if read_bytes(fd, len(content)) != content:
                fail(
                    "SELF_BUILD_MATERIALIZATION_CONFLICT",
                    "Owned staging write was not acknowledged.",
                )
        driver.authority_check()
        move_without_replace(private.path / stage, parent.path / leaf)
        with open_file(parent, leaf, os.O_RDONLY, internal=True) as fd:
            if read_bytes(fd, len(content)) != content:
                fail(
                    "SELF_BUILD_MATERIALIZATION_CONFLICT",
                    "The native file acknowledgement differs from approved content.",
                )


def materialize(driver, policy, plan, owner, *, acknowledge):
    """Persisted native phase authority/checkpoints belong to the calling service.

    Each source effect is an exclusive atomic move of verified raw Git bytes.
    Retries verify exact existing bytes rather than overwrite conflicts. Partial
    private staging resumes only a verified prefix. Acknowledgement callbacks must
    journal and checkpoint the measured result before advancing recovery position.
    """
    if not supports_mutation():
        fail(
            "SELF_BUILD_CHECKOUT_PLATFORM_UNAVAILABLE",
            "Raw materialization requires Windows native containment.",
        )
    with owned_namespace(
        policy, plan.workspace_id, owner, authority_check=driver.authority_check
    ) as (stack, root, target, _):
        primary_path = Path(policy.primary_root)
        primary = open_directory(stack, Path(primary_path.anchor), internal=True)
        for leaf in primary_path.parts[1:]:
            primary = open_directory(stack, primary.path / leaf, primary, leaf, internal=True)
        common = open_directory(stack, primary.path / ".git", primary, ".git", internal=True)
        objects = open_directory(stack, common.path / "objects", common, "objects", internal=True)
        open_directory(stack, objects.path / "info", objects, "info", internal=True)
        registrations = open_directory(
            stack, common.path / "worktrees", common, "worktrees", internal=True
        )
        open_directory(
            stack,
            registrations.path / plan.workspace_id,
            registrations,
            plan.workspace_id,
            internal=True,
        )
        driver.verify(stack, root, target, policy, plan, common)
        entries = inventory(driver, policy, plan, target)
        checkout = open_directory(stack, target, root, target.name, internal=True)
        private_key = ".jarvis-owner-" + plan.workspace_id
        private = open_directory(stack, root.path / private_key, root, private_key, internal=True)
        measured = []
        for index, (mode, blob, path, size) in enumerate(entries):
            driver.authority_check()
            content = driver.invoke(policy, "blob", plan, target, blob=blob)
            if len(content) != size or blob_digest(content) != blob:
                fail(
                    "SELF_BUILD_MATERIALIZATION_UNSAFE",
                    "Raw blob bytes do not match the approved object.",
                )
            write_file(driver, checkout, private, path, content)
            observation = dict(path=path, mode=mode, sha256=sha256(content).hexdigest(), bytes=size)
            measured.append(observation)
            acknowledge(index + 1, digest(measured))
        marker = json.dumps(
            dict(
                schemaVersion="1.0",
                workspaceId=plan.workspace_id,
                allowedTools=list(plan.allowed_tools),
                readPrefixes=list(plan.read_prefixes),
                writePrefixes=list(plan.write_prefixes),
                planHash=plan.plan_hash,
            ),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        write_file(driver, checkout, private, ".jarvis-workspace.json", marker)
        driver.authority_check()
        driver.invoke(policy, "index", plan, target)
        raw_index = driver.invoke(policy, "index_inventory", plan, target)
        indexed = []
        try:
            for record in raw_index.split(b"\0"):
                if not record:
                    continue
                header, path = record.split(b"\t", 1)
                mode, blob, stage = header.decode("ascii").split()
                if stage != "0":
                    raise ValueError("unmerged index")
                indexed.append([mode, blob, path.decode("utf-8")])
        except (ValueError, UnicodeError):
            fail("SELF_BUILD_MATERIALIZATION_UNSAFE", "The dedicated index is malformed.")
        if digest(indexed) != plan.inventory_digest:
            fail(
                "SELF_BUILD_MATERIALIZATION_UNSAFE",
                "The dedicated index differs from the approved inventory.",
            )
        expected_names = {entry[2] for entry in entries} | {".git", ".jarvis-workspace.json"}
        observed_names = set()
        directory_count = 0
        for directory, directories, files in os.walk(target, followlinks=False):
            driver.authority_check()
            directory_count += len(directories)
            if len(observed_names) + directory_count > plan.file_count * 16 + 2:
                fail(
                    "SELF_BUILD_MATERIALIZATION_LIMIT",
                    "The final workspace exceeds its bounded inventory.",
                )
            parent = Path(directory)
            for leaf in directories:
                open_directory(stack, parent / leaf, internal=True)
            for leaf in files:
                relative = (parent / leaf).relative_to(target).as_posix()
                if relative not in expected_names:
                    fail(
                        "SELF_BUILD_MATERIALIZATION_CONFLICT",
                        "The workspace contains an unexpected file; preserve it for recovery.",
                    )
                observed_names.add(relative)
        if observed_names != expected_names:
            fail(
                "SELF_BUILD_MATERIALIZATION_CONFLICT",
                "The final workspace inventory is incomplete.",
            )
        final_measurements = []
        for mode, blob, path, size in entries:
            driver.authority_check()
            with ExitStack() as file_stack:
                directory = checkout
                segments = repository_parts(path)
                for leaf in segments[:-1]:
                    directory = open_directory(
                        file_stack, directory.path / leaf, directory, leaf, internal=True
                    )
                with open_file(directory, segments[-1], os.O_RDONLY, internal=True) as fd:
                    content = read_bytes(fd, plan.maximum_file_bytes)
                if len(content) != size or blob_digest(content) != blob:
                    fail(
                        "SELF_BUILD_MATERIALIZATION_CONFLICT",
                        "Final source readback differs from the approved blob.",
                    )
                final_measurements.append(
                    dict(path=path, mode=mode, sha256=sha256(content).hexdigest(), bytes=size)
                )
        if final_measurements != measured:
            fail(
                "SELF_BUILD_MATERIALIZATION_CONFLICT",
                "Final source evidence differs from file acknowledgements.",
            )
        with open_file(checkout, ".jarvis-workspace.json", os.O_RDONLY, internal=True) as fd:
            if read_bytes(fd, 8192) != marker:
                fail(
                    "SELF_BUILD_MATERIALIZATION_CONFLICT",
                    "Final read-only marker acknowledgement differs.",
                )
        driver.verify(stack, root, target, policy, plan, common)
        driver.authority_check()
        return dict(
            completed_file_count=len(measured),
            source_digest=digest(measured),
            inventory_digest=plan.inventory_digest,
        )
