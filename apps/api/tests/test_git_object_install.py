"""Loose-object frames and atomic directory receipts are bounded and exact."""

import os
import zlib
from contextlib import ExitStack

import pytest

from app.core.errors import DomainError
from app.self_build.commit_object_directory import object_directory
from app.self_build.commit_object_install import compressed_object, exact_loose_object
from app.self_build.commit_objects import GitObject
from app.tool_execution.filesystem import open_directory


@pytest.mark.parametrize(
    "kind,payload",
    [("blob", b""), ("blob", b"\0binary\xff"), ("tree", b"entry\0"), ("commit", b"commit\n")],
)
def test_loose_frames_recover_exact_uncompressed_git_payload(kind, payload):
    item = GitObject(kind, payload)
    encoded = compressed_object(item)
    exact_loose_object(encoded, item)
    assert (
        zlib.decompress(encoded)
        == kind.encode() + b" " + str(len(payload)).encode() + b"\0" + payload
    )


@pytest.mark.parametrize(
    "fault", ["wrong_kind", "wrong_size", "wrong_payload", "trailing", "truncated", "bomb"]
)
def test_loose_object_refuses_wrong_or_ambiguous_bytes(fault):
    item = GitObject("blob", b"approved")
    data = compressed_object(item)
    if fault == "wrong_kind":
        data = zlib.compress(b"tree 8\0approved")
    elif fault == "wrong_size":
        data = zlib.compress(b"blob 9\0approved")
    elif fault == "wrong_payload":
        data = zlib.compress(b"blob 8\0replaced")
    elif fault == "trailing":
        data += b"foreign"
    elif fault == "truncated":
        data = data[:-1]
    else:
        data = zlib.compress(b"blob 1000000\0" + b"x" * 1000000)
    with pytest.raises(DomainError) as blocked:
        exact_loose_object(data, item)
    assert blocked.value.code == "GIT_COMMIT_OBJECT_CONTENT_INVALID"


@pytest.mark.skipif(os.name != "nt", reason="native atomic object prefix ownership")
def test_native_object_directory_create_retains_receipt_and_refuses_adoption(tmp_path):
    with ExitStack() as stack:
        root = open_directory(stack, tmp_path, internal=True)
        path, identity = object_directory(stack, root, "ab", create=True)
        assert identity == (path.stat().st_dev, path.stat().st_ino)
        with pytest.raises(FileExistsError):
            object_directory(stack, root, "ab", create=True)
        assert object_directory(stack, root, "ab", create=False)[1] == identity
        with pytest.raises(OSError):
            path.rename(tmp_path / "foreign")
    assert path.is_dir()


@pytest.mark.parametrize("prefix", ["../ab", "AB", "abc", "ab/../cd", "a~", ""])
def test_native_prefix_cannot_be_widened_to_a_path(tmp_path, prefix):
    with ExitStack() as stack:
        root = open_directory(stack, tmp_path, internal=True)
        with pytest.raises(ValueError):
            object_directory(stack, root, prefix, create=True)
