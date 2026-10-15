"""Deterministic full SHA-1 Git index v2; no sparse, merge or extension state.

Format: https://git-scm.com/docs/index-format . Stat hints are zero; Git must
measure actual working files rather than treating cached filesystem hints as proof.
"""

from hashlib import sha1, sha256
from pathlib import Path
from struct import pack

from app.models.git_changes import InventoryFile
from app.self_build.change_verifier import path_nodes

MAX_INDEX_BYTES = 8388608
INDEX_POLICY_DIGEST = sha256(Path(__file__).read_text(encoding="utf-8").encode()).hexdigest()


def compile_index(entries):
    entries = tuple(InventoryFile.model_validate(item.model_dump(mode="json")) for item in entries)
    if len(entries) > 4096 or len({item.path for item in entries}) != len(entries):
        raise ValueError("index requires unique bounded files")
    path_nodes([(item.path, True) for item in entries])
    output = bytearray(b"DIRC" + pack("!II", 2, len(entries)))
    for item in sorted(entries, key=lambda value: value.path.encode("utf-8")):
        name = item.path.encode("utf-8")
        identity = item.identity
        record = (
            pack("!10I", 0, 0, 0, 0, 0, 0, int(identity.mode, 8), 0, 0, identity.byte_count)
            + bytes.fromhex(identity.blob_sha)
            + pack("!H", min(len(name), 0xFFF))
            + name
        )
        output.extend(record + b"\0" * (8 - len(record) % 8))
        if len(output) + 20 > MAX_INDEX_BYTES:
            raise ValueError("index exceeds its byte bound")
    return bytes(output) + sha1(output, usedforsecurity=False).digest()
