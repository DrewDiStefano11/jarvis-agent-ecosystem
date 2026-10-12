"""Private bounded raw-blob materialization. No checkout filters or model tool."""

import ctypes
import json
import os
from contextlib import ExitStack
from ctypes import wintypes
from hashlib import sha1, sha256
from pathlib import Path

from app.self_build.checkout_driver import fail, pin_read_tree, supports_mutation
from app.self_build.git_observer import COMMIT, pinned_metadata_file
from app.self_build.policy import digest
from app.self_build.workspace_namespace import owned_namespace
from app.tool_execution.filesystem import (
    check_stat,
    open_directory,
    open_file,
    read_bytes,
    repository_parts,
    windows_final_path,
    windows_native_path,
)

RESERVED = {".jarvis-workspace.json", ".jarvis-workspace.lock"}


def inventory(driver, policy, plan, target):
    raw = driver.invoke(policy, "inventory", plan, target)
    entries, names, total = [], {}, 0
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
                or any(
                    part.casefold() in RESERVED or part.casefold().startswith(".jarvis-stage-")
                    for part in segments
                )
            ):
                fail(
                    "SELF_BUILD_MATERIALIZATION_UNSAFE",
                    "The base inventory exceeds its protected materialization scope.",
                )
            # Every Windows path prefix has one exact spelling and node kind.
            # Full-path checks alone miss aliased parents and file/directory collisions.
            for depth in range(1, len(segments) + 1):
                prefix = "/".join(segments[:depth])
                node = (prefix, depth == len(segments))
                previous = names.get(prefix.casefold())
                if previous is not None and (previous != node or node[1]):
                    fail(
                        "SELF_BUILD_MATERIALIZATION_UNSAFE",
                        "The base inventory contains colliding path components.",
                    )
                names[prefix.casefold()] = node
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
    # A same-parent native rename avoids reopening the pinned directory for write.
    # The target ownership pin remains deny-write/delete throughout acknowledgement.
    import msvcrt

    if source.parent != target.parent:
        raise ValueError("Owned acknowledgement must remain in its pinned parent")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    native = ctypes.WinDLL("ntdll")
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    handle = kernel.CreateFileW(
        windows_native_path(source), 0x80010000, 1, None, 3, 0x00200000, None
    )
    if handle == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), "Cannot pin owned staging file")
    fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    try:
        check_stat(os.fstat(fd), internal=True)
        if os.path.normcase(windows_final_path(fd)) != os.path.normcase(str(source)):
            raise OSError("Owned staging handle changed namespace")

        class Rename(ctypes.Structure):
            _fields_ = [
                ("ReplaceIfExists", wintypes.BOOLEAN),
                ("RootDirectory", wintypes.HANDLE),
                ("FileNameLength", wintypes.ULONG),
                ("FileName", wintypes.WCHAR * (len(target.name) + 1)),
            ]

        class IoStatus(ctypes.Structure):
            _fields_ = [("Status", ctypes.c_void_p), ("Information", ctypes.c_size_t)]

        rename = Rename(False, None, len(target.name.encode("utf-16-le")), target.name)
        io = IoStatus()
        native.NtSetInformationFile.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(IoStatus),
            ctypes.c_void_p,
            wintypes.ULONG,
            wintypes.ULONG,
        ]
        native.NtSetInformationFile.restype = ctypes.c_long
        status = native.NtSetInformationFile(
            handle, ctypes.byref(io), ctypes.byref(rename), ctypes.sizeof(rename), 10
        )
        if status < 0:
            native.RtlNtStatusToDosError.argtypes = [ctypes.c_long]
            native.RtlNtStatusToDosError.restype = wintypes.ULONG
            raise OSError(native.RtlNtStatusToDosError(status), "Owned file acknowledgement failed")
    finally:
        os.close(fd)


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
        stage = ".jarvis-stage-" + sha256(content).hexdigest()
        driver.authority_check()
        with open_file(parent, stage, os.O_RDWR | os.O_CREAT, internal=True) as fd:
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
        move_without_replace(parent.path / stage, parent.path / leaf)
        with open_file(parent, leaf, os.O_RDONLY, internal=True) as fd:
            if read_bytes(fd, len(content)) != content:
                fail(
                    "SELF_BUILD_MATERIALIZATION_CONFLICT",
                    "The native file acknowledgement differs from approved content.",
                )


def materialize(driver, policy, plan, owner, *, acknowledge, finalize=None):
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
        policy,
        plan.workspace_id,
        owner,
        authority_check=driver.authority_check,
        validate_existing_target=lambda stack, root, target: driver.validate_existing_target(
            stack, root, target, policy, plan
        ),
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
        # Pin every verified artifact and both absent-name namespaces before
        # final verification; keep them through the service's ready checkpoint.
        check_source = pin_read_tree(
            stack, target, driver.authority_check, maximum=plan.file_count * 16 + 2
        )
        check_metadata = pin_read_tree(
            stack,
            common.path,
            driver.authority_check,
            maximum=plan.maximum_initial_metadata_nodes + plan.metadata_growth_reserve,
        )
        stack.enter_context(pinned_metadata_file(private.path / "owner.json"))
        try:
            with open_file(private, "owner.json", os.O_RDONLY, internal=True) as fd:
                actual_owner = json.loads(read_bytes(fd, 4096))
        except (ValueError, UnicodeError):
            fail("SELF_BUILD_MATERIALIZATION_CONFLICT", "Final namespace ownership is invalid.")
        if actual_owner != owner:
            fail("SELF_BUILD_MATERIALIZATION_CONFLICT", "Final namespace ownership changed.")
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
        expected_directories = {
            "/".join(repository_parts(entry[2])[:length])
            for entry in entries
            for length in range(1, len(repository_parts(entry[2])))
        }
        observed_directories = set()
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
                relative_directory = (parent / leaf).relative_to(target).as_posix()
                if relative_directory not in expected_directories:
                    fail(
                        "SELF_BUILD_MATERIALIZATION_CONFLICT",
                        "The workspace contains an unapproved directory; preserve it for recovery.",
                    )
                observed_directories.add(relative_directory)
                open_directory(stack, parent / leaf, internal=True)
            for leaf in files:
                relative = (parent / leaf).relative_to(target).as_posix()
                if relative not in expected_names:
                    fail(
                        "SELF_BUILD_MATERIALIZATION_CONFLICT",
                        "The workspace contains an unexpected file; preserve it for recovery.",
                    )
                observed_names.add(relative)
        if observed_names != expected_names or observed_directories != expected_directories:
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
        result = dict(
            completed_file_count=len(measured),
            source_digest=digest(measured),
            inventory_digest=plan.inventory_digest,
        )
        check_source()
        check_metadata()

        # The callback performs final intent/checkpoint publication while native
        # deny-write/delete pins and both namespace notifications remain armed.
        def publication_guard():
            check_source()
            check_metadata()
            # The service revalidates authority in its existing commit transaction.

        return result if finalize is None else finalize(result, publication_guard)
