"""Exact immutable source payloads; no guest launch, host mount or credentials.

The trusted caller supplies a freshly verified native inventory and live runtime
fence. Wire input carries no independent filenames or result claims. A receiver
must already possess the expected capsule through its protected control channel.
"""

import json
import os
import stat
import sys
from contextlib import ExitStack
from hashlib import sha256
from pathlib import Path
from struct import pack, unpack

from app.models.validation_isolation import SourceCapsule
from app.tool_execution.filesystem import open_directory, open_file, read_bytes
from app.validation_runner.isolation import verify_capsule, verify_transfer

MAGIC = b"JARVIS-SOURCE\x00\x01"
MAX_MANIFEST = 2097152
MAX_PAYLOAD = 134217728 + MAX_MANIFEST + len(MAGIC) + 4
# Reserve ample descriptor capacity for process I/O, SQLite and native guards.
MAX_DIRECTORY_PINS = 128


def preflight_directory_pins(root, files):
    prefixes = {()}
    for item in files:
        parts = item.path.split("/")
        prefixes.update(tuple(parts[:depth]) for depth in range(1, len(parts)))
        if len(root.parents) + len(prefixes) > MAX_DIRECTORY_PINS:
            raise ValueError("CAPSULE_DIRECTORY_PIN_LIMIT")
    if len(root.parents) + len(prefixes) > MAX_DIRECTORY_PINS:
        raise ValueError("CAPSULE_DIRECTORY_PIN_LIMIT")


def manifest_bytes(capsule):
    capsule = SourceCapsule.model_validate(capsule.model_dump(mode="json"))
    data = json.dumps(
        capsule.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    if len(data) > MAX_MANIFEST:
        raise ValueError("CAPSULE_MANIFEST_LIMIT")
    return capsule, data


def freeze_payload(capsule, stream, *, authority_check):
    """Verify all bytes before returning anything that a transport can expose.

    Materialize at most the approved inventory plus one entry. Immutable bytes
    prevent a caller from changing buffers between verification and transmission.
    """
    from itertools import islice

    capsule, manifest = manifest_bytes(capsule)
    authority_check()
    records = []
    expected = {item.path: item for item in capsule.files}
    seen = set()
    for raw in islice(stream, len(capsule.files) + 1):
        authority_check()
        if not isinstance(raw, tuple) or len(raw) != 3:
            raise ValueError("CAPSULE_TRANSFER_INVALID")
        path, mode, content = raw
        if type(path) is not str or path not in expected or path in seen:
            raise ValueError("CAPSULE_TRANSFER_PATH")
        item = expected[path]
        # Accept bytes only: never retain a mutable memoryview/bytearray.
        if type(content) is not bytes or len(content) != item.byte_count or mode != item.mode:
            raise ValueError("CAPSULE_TRANSFER_CONTENT")
        if sha256(content).hexdigest() != item.content_digest:
            raise ValueError("CAPSULE_TRANSFER_DIGEST")
        seen.add(path)
        records.append((path, mode, content))
    verify_transfer(capsule, records)
    by_path = {path: content for path, _, content in records}
    payload = MAGIC + pack(">I", len(manifest)) + manifest
    payload += b"".join(by_path[item.path] for item in capsule.files)
    authority_check()
    return payload


def receive_payload(capsule, payload, *, authority_check):
    """Reject truncation, corruption, substitution and trailing bytes before I/O.

    The expected manifest is canonical and compared byte-for-byte; untrusted JSON
    is never parsed into authority. Fixed inventory lengths delimit file contents.
    This is transfer verification, not an execution receipt or guest attestation.
    """
    capsule, expected = manifest_bytes(capsule)
    authority_check()
    if type(payload) is not bytes or not len(MAGIC) + 4 <= len(payload) <= MAX_PAYLOAD:
        raise ValueError("CAPSULE_PAYLOAD_INVALID")
    if not payload.startswith(MAGIC):
        raise ValueError("CAPSULE_PAYLOAD_INVALID")
    size = unpack(">I", payload[len(MAGIC) : len(MAGIC) + 4])[0]
    start = len(MAGIC) + 4
    if size != len(expected) or payload[start : start + size] != expected:
        raise ValueError("CAPSULE_PAYLOAD_BINDING_MISMATCH")
    position = start + size
    records = []
    for item in capsule.files:
        authority_check()
        content = payload[position : position + item.byte_count]
        records.append((item.path, item.mode, content))
        position += item.byte_count
    if position != len(payload):
        raise ValueError("CAPSULE_PAYLOAD_LENGTH_MISMATCH")
    verify_transfer(capsule, records)
    authority_check()
    return tuple(records)


def export_snapshot(
    root, capsule, plan, *, owner, journal_digest, native_inventory_digest, authority_check
):
    """Read approved bytes through pinned ancestry into a complete frozen payload.

    authority_check(root, capsule) MUST verify registered root/workspace identity,
    current native source inventory, plan approval, lease and emergency stop. It
    runs before reads, throughout capture, and after sealing. A digest alone does
    not grant permission to read a caller-selected repository. No root marker is
    fabricated here. Expected Git modes come from the independent native reader;
    on POSIX, executable bits are additionally checked on the opened file.
    """
    capsule = verify_capsule(
        capsule,
        plan,
        owner=owner,
        journal_digest=journal_digest,
        native_inventory_digest=native_inventory_digest,
    )
    root = Path(root)
    if not root.is_absolute() or os.name not in {"nt", "posix"}:
        raise ValueError("CAPSULE_ROOT_INVALID")

    def guard():
        authority_check(root, capsule)

    guard()
    preflight_directory_pins(root, capsule.files)
    with ExitStack() as stack:
        # Windows locks every ancestor against replacement. POSIX traverses each
        # component with directory descriptors and O_NOFOLLOW, including root.
        parent = None
        for path in (*reversed(root.parents), root):
            parent = open_directory(
                stack, path, parent=parent, leaf=path.name if parent else None, internal=True
            )
        directories = {(): parent}
        records = []
        for item in capsule.files:
            guard()
            parts = tuple(item.path.split("/"))
            for index in range(1, len(parts)):
                prefix = parts[:index]
                if prefix not in directories:
                    directories[prefix] = open_directory(
                        stack,
                        root.joinpath(*prefix),
                        parent=directories[prefix[:-1]],
                        leaf=prefix[-1],
                        internal=True,
                    )
            with open_file(directories[parts[:-1]], parts[-1], os.O_RDONLY, internal=True) as fd:
                info = os.fstat(fd)
                if os.name == "posix" and bool(info.st_mode & 0o111) != (item.mode == "100755"):
                    raise ValueError("CAPSULE_SOURCE_MODE_MISMATCH")
                content = read_bytes(fd, item.byte_count)
                if (
                    len(content) != item.byte_count
                    or sha256(content).hexdigest() != item.content_digest
                ):
                    raise ValueError("CAPSULE_SOURCE_CHANGED")
                records.append((item.path, item.mode, content))
            guard()
        payload = freeze_payload(capsule, records, authority_check=guard)
        # Independent state recheck remains inside the pinned directory lifetime.
        guard()
        return payload


def import_linux_guest(root, capsule, payload, *, authority_check):
    """Materialize into an empty root-owned private Linux guest directory.

    Run only as the enrolled guest service, before candidate processes exist.
    Candidate code must later run under a distinct unprivileged identity. This
    function does not enroll that service, assert its integrity or start code.
    Partial failure leaves artifacts quarantined; it never recursively deletes.
    Windows guest materialization is deliberately unavailable until equivalent
    handle-relative creation and reparse/ADS enumeration are implemented.
    """
    if sys.platform != "linux" or os.geteuid() != 0:
        raise ValueError("CAPSULE_GUEST_IMPORT_UNAVAILABLE")
    records = receive_payload(capsule, payload, authority_check=authority_check)
    root = Path(root)
    if not root.is_absolute():
        raise ValueError("CAPSULE_ROOT_INVALID")
    preflight_directory_pins(root, capsule.files)
    with ExitStack() as stack:
        parent = None
        for path in (*reversed(root.parents), root):
            parent = open_directory(
                stack, path, parent=parent, leaf=path.name if parent else None, internal=True
            )
            info = os.fstat(parent.fd)
            if info.st_uid != 0 or info.st_mode & 0o022:
                raise ValueError("CAPSULE_GUEST_ROOT_UNPROTECTED")
        if stat.S_IMODE(os.fstat(parent.fd).st_mode) != 0o700 or os.listdir(parent.fd):
            raise ValueError("CAPSULE_GUEST_ROOT_NOT_EMPTY_PRIVATE")
        # Inspect the actual destination before creating even the first path.
        name_max = os.fpathconf(parent.fd, "PC_NAME_MAX")
        if name_max <= 0:
            raise ValueError("CAPSULE_GUEST_NAME_LIMIT_UNAVAILABLE")
        if any(
            len(component.encode("utf-8")) > name_max
            for path, _, _ in records
            for component in path.split("/")
        ):
            raise ValueError("CAPSULE_GUEST_NAME_LIMIT")
        root_device = os.fstat(parent.fd).st_dev
        directories = {(): parent}
        children = {(): set()}
        for path, mode, content in records:
            authority_check()
            parts = tuple(path.split("/"))
            for index in range(1, len(parts)):
                prefix = parts[:index]
                if prefix not in directories:
                    ancestor = directories[prefix[:-1]]
                    # Exclusive creation refuses pre-existing links/directories.
                    os.mkdir(prefix[-1], mode=0o700, dir_fd=ancestor.fd)
                    directories[prefix] = open_directory(
                        stack,
                        root.joinpath(*prefix),
                        parent=ancestor,
                        leaf=prefix[-1],
                        internal=True,
                    )
                    if os.fstat(directories[prefix].fd).st_dev != root_device:
                        raise ValueError("CAPSULE_GUEST_MOUNT_DENIED")
                    children[prefix] = set()
                    children[prefix[:-1]].add(prefix[-1])
            directory = directories[parts[:-1]]
            with open_file(
                directory, parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL, internal=True
            ) as fd:
                position = 0
                while position < len(content):
                    authority_check()
                    written = os.write(fd, content[position : position + 65536])
                    if written <= 0:
                        raise OSError("Guest source write failed")
                    position += written
                os.fchmod(fd, 0o555 if mode == "100755" else 0o444)
                os.fsync(fd)
            children[parts[:-1]].add(parts[-1])
        # Reopen every file and measure the materialized inventory, modes and bytes.
        for path, mode, content in records:
            authority_check()
            parts = tuple(path.split("/"))
            with open_file(directories[parts[:-1]], parts[-1], os.O_RDONLY, internal=True) as fd:
                info = os.fstat(fd)
                if (
                    info.st_uid != 0
                    or info.st_dev != root_device
                    or stat.S_IMODE(info.st_mode) != (0o555 if mode == "100755" else 0o444)
                    or read_bytes(fd, len(content)) != content
                ):
                    raise ValueError("CAPSULE_GUEST_IMPORT_MISMATCH")
        for prefix, directory in reversed(tuple(directories.items())):
            authority_check()
            if set(os.listdir(directory.fd)) != children[prefix]:
                raise ValueError("CAPSULE_GUEST_UNEXPECTED_ENTRY")
            os.fchmod(directory.fd, 0o555)
            os.fsync(directory.fd)
        authority_check()
    return capsule.capsule_digest
