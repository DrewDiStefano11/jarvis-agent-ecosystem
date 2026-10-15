"""Bounded SHA-1 Git push encoding; no transport, credentials or effect authority.

Only the future protected publication service may supply verified native objects.
This module does not import model requests, run Git or open a network connection.
"""

import re
import struct
import zlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from hashlib import sha1
from itertools import islice
from typing import Protocol

from app.core.errors import DomainError
from app.github_publication.transport import RepositoryTarget, safe_branch

MAX_OBJECTS = 8192
MAX_SOURCE_BYTES = 8388608
MAX_PACK_BYTES = 9437184
MAX_RESPONSE_BYTES = 1048576
ZERO = "0" * 40
OID = re.compile(r"[a-f0-9]{40}")


class ObjectPayload(Protocol):
    kind: str
    payload: bytes


@dataclass(frozen=True)
class Advertisement:
    refs: tuple[tuple[str, str], ...]
    capabilities: frozenset[str]


@dataclass(frozen=True)
class PushRequest:
    reference: str
    old_sha: str
    new_sha: str
    body: bytes = field(repr=False)


def refuse(code="GITHUB_GIT_PROTOCOL_INVALID"):
    raise DomainError(code, "Git push failed a bounded protocol or scope check.", 409)


def packet(payload):
    if type(payload) is not bytes or not 1 <= len(payload) <= 65516:
        refuse()
    return f"{len(payload) + 4:04x}".encode("ascii") + payload


def packets(raw):
    """Parse the supported v0 packet stream; delimiters/truncation refuse."""
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_RESPONSE_BYTES:
        refuse()
    result, offset = [], 0
    while offset < len(raw):
        prefix = raw[offset : offset + 4]
        if not re.fullmatch(rb"[0-9a-fA-F]{4}", prefix):
            refuse()
        size = int(prefix, 16)
        if size == 0:
            result.append(None)
            offset += 4
        elif not 5 <= size <= 65520 or offset + size > len(raw):
            refuse()
        else:
            result.append(raw[offset + 4 : offset + size])
            offset += size
        if len(result) > 16384:
            refuse()
    return tuple(result)


def advertised_ref(name):
    """Remote names use Git's byte-level rules, never generated-branch scope."""
    if name == b"HEAD":
        return True
    return (
        name.startswith(b"refs/")
        and not name.endswith((b"/", b"."))
        and not any(token in name for token in (b"..", b"//", b"@{", b"\\"))
        and not any(byte <= 32 or byte == 127 or byte in b"~^:?*[" for byte in name)
        and all(
            part and not part.startswith(b".") and not part.endswith(b".lock")
            for part in name.split(b"/")
        )
    )


def advertisement(raw):
    stream = packets(raw)
    if (
        len(stream) < 4
        or stream[:2] != (b"# service=git-receive-pack\n", None)
        or stream[-1] is not None
        or any(value is None for value in stream[2:-1])
    ):
        refuse()
    first = stream[2].removesuffix(b"\n")
    if first.count(b"\0") != 1:
        refuse()
    first_ref, raw_capabilities = first.split(b"\0")
    try:
        tokens = raw_capabilities.decode("ascii").split(" ")
    except UnicodeError:
        refuse()
    if (
        not 1 <= len(tokens) <= 128
        or len(tokens) != len(set(tokens))
        or any(not re.fullmatch(r"[!-~]{1,256}", item) for item in tokens)
    ):
        refuse()
    capabilities = frozenset(tokens)
    formats = [item for item in tokens if item.startswith("object-format=")]
    if formats and formats != ["object-format=sha1"]:
        refuse("GITHUB_GIT_OBJECT_FORMAT_UNSUPPORTED")
    if "report-status" not in capabilities:
        refuse("GITHUB_GIT_CAPABILITY_UNAVAILABLE")
    lines = (first_ref,) + tuple(value.removesuffix(b"\n") for value in stream[3:-1])
    if lines == ((ZERO + " capabilities^{}").encode(),):
        return Advertisement((), capabilities)
    refs = []
    for line in lines:
        try:
            raw_value, raw_name = line.split(b" ", 1)
            value = raw_value.decode("ascii")
        except (ValueError, UnicodeError):
            refuse()
        if not OID.fullmatch(value) or value == ZERO or not advertised_ref(raw_name):
            refuse()
        name = raw_name.decode("utf-8", errors="surrogateescape")
        refs.append((name, value))
    if len(refs) != len({name for name, _ in refs}):
        refuse()
    return Advertisement(tuple(refs), capabilities)


def encode_objects(objects: Iterable[ObjectPayload]):
    """Encode an undeltified pack and derive identities from actual payloads."""
    objects = tuple(islice(objects, MAX_OBJECTS + 1))
    if not 1 <= len(objects) <= MAX_OBJECTS:
        refuse("GITHUB_GIT_PACK_LIMIT")
    parts, identifiers, commits, size = [], set(), {}, 0
    for item in objects:
        if item.kind not in {"blob", "tree", "commit"} or type(item.payload) is not bytes:
            refuse()
        size += len(item.payload)
        if size > MAX_SOURCE_BYTES:
            refuse("GITHUB_GIT_PACK_LIMIT")
        identifier = sha1(
            f"{item.kind} {len(item.payload)}\0".encode() + item.payload, usedforsecurity=False
        ).hexdigest()
        if identifier in identifiers:
            refuse("GITHUB_GIT_OBJECT_DUPLICATE")
        identifiers.add(identifier)
        if item.kind == "commit":
            commits[identifier] = item.payload
        kind = {"commit": 1, "tree": 2, "blob": 3}[item.kind]
        remaining = len(item.payload) >> 4
        header = bytearray([(kind << 4) | (len(item.payload) & 15) | (128 if remaining else 0)])
        while remaining:
            value, remaining = remaining & 127, remaining >> 7
            header.append(value | (128 if remaining else 0))
        parts.append(bytes(header) + zlib.compress(item.payload))
    body = b"PACK" + struct.pack("!II", 2, len(objects)) + b"".join(parts)
    body += sha1(body, usedforsecurity=False).digest()
    if len(body) > MAX_PACK_BYTES:
        refuse("GITHUB_GIT_PACK_LIMIT")
    return body, commits


def receive_request(target, branch, parent_sha, new_sha, remote_advertisement, objects):
    """One approved namespace ref, with an explicit expected remote old value.

    The caller must first resolve protected task scope, native commit checkpoint
    and Campaign B validation. These structural checks grant no such authority.
    Remote absence permits creation; an existing ref must equal the native parent.
    Exact already-published state is reconciled by the durable service separately.
    """
    if not isinstance(target, RepositoryTarget):
        refuse()
    reference = "refs/heads/" + target.branch(branch)
    if not isinstance(parent_sha, str) or not isinstance(new_sha, str):
        refuse()
    if not OID.fullmatch(parent_sha) or not OID.fullmatch(new_sha) or ZERO in {parent_sha, new_sha}:
        refuse()
    if parent_sha == new_sha:
        refuse("GITHUB_GIT_COMMIT_SCOPE_INVALID")
    remote = advertisement(remote_advertisement)
    existing = dict(remote.refs).get(reference, ZERO)
    if existing not in {ZERO, parent_sha}:
        refuse("GITHUB_REMOTE_REFERENCE_CHANGED")
    pack, commits = encode_objects(objects)
    if set(commits) != {new_sha}:
        refuse("GITHUB_GIT_COMMIT_SCOPE_INVALID")
    # The reviewed native operation creates one direct child, never a merge or
    # replacement history. The private compiler owns all remaining commit bytes.
    header = commits[new_sha].split(b"\n\n", 1)[0].split(b"\n")
    if len(header) < 4 or [line for line in header if line.startswith(b"parent ")] != [
        ("parent " + parent_sha).encode()
    ]:
        refuse("GITHUB_GIT_COMMIT_SCOPE_INVALID")
    command = f"{existing} {new_sha} {reference}".encode() + b"\0report-status\n"
    return PushRequest(reference, existing, new_sha, packet(command) + b"0000" + pack)


def receive_result(raw, reference):
    """No remote hook/error text enters ordinary diagnostics."""
    if (
        not isinstance(reference, str)
        or not reference.startswith("refs/heads/")
        or not safe_branch(reference[11:])
    ):
        refuse()
    stream = packets(raw)
    if len(stream) != 3 or stream[-1] is not None or any(item is None for item in stream[:-1]):
        refuse()
    if stream[0] != b"unpack ok\n":
        refuse("GITHUB_PUSH_REJECTED")
    if stream[1] == ("ok " + reference + "\n").encode():
        return
    if stream[1].startswith(("ng " + reference + " ").encode()):
        refuse("GITHUB_PUSH_REJECTED")
    refuse()
