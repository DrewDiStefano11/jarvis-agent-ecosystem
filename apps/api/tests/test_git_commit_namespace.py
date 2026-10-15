"""Native effect names are exact; transient unknown names cannot disappear safely."""

import os
import struct

import pytest

from app.core.errors import DomainError
from app.self_build.commit_namespace_watch import (
    effect_namespace_watch,
    exact_names,
    notification_names,
)


def notification(name, *, action=1, following=0):
    value = name.encode("utf-16-le")
    return struct.pack("<III", following, action, len(value)) + value


def test_notification_chain_preserves_exact_case_and_path():
    first = notification("refs\\heads\\jarvis\\branch")
    offset = (len(first) + 3) // 4 * 4
    first = struct.pack("<I", offset) + first[4:] + b"\0" * (offset - len(first))
    assert notification_names(first + notification("index", action=5)) == (
        "refs/heads/jarvis/branch",
        "index",
    )


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"\0" * 12,
        notification("index", action=6),
        notification("../index"),
        notification("index") + b"hidden",
        notification("index", following=4),
        struct.pack("<III", 0, 1, 1) + b"x",
        notification("index")[:-1],
    ],
)
def test_malformed_or_ambiguous_notifications_refuse(payload):
    with pytest.raises(DomainError):
        notification_names(payload)


@pytest.mark.parametrize(
    "names",
    [
        {"index"},
        frozenset(),
        frozenset({"Index", "index"}),
        frozenset({"index.lock/../index"}),
        frozenset({"/index"}),
        frozenset({"index:stream"}),
        frozenset({"refs//heads"}),
    ],
)
def test_effect_name_scope_cannot_be_broadened(names):
    with pytest.raises(ValueError):
        exact_names(names)


@pytest.mark.skipif(os.name != "nt", reason="native Windows namespace notifications")
def test_native_watch_rearms_for_permitted_exact_names(tmp_path):
    with effect_namespace_watch(
        tmp_path, frozenset({"index", "index.before", "index.after"})
    ) as check:
        (tmp_path / "index.after").write_bytes(b"approved")
        check()
        (tmp_path / "index.after").rename(tmp_path / "index")
        check()
        (tmp_path / "index").rename(tmp_path / "index.before")
        check()
    assert (tmp_path / "index.before").read_bytes() == b"approved"


@pytest.mark.skipif(os.name != "nt", reason="native Windows namespace notifications")
def test_native_watch_retains_transient_foreign_name(tmp_path):
    with pytest.raises(DomainError) as fault:
        with effect_namespace_watch(tmp_path, frozenset({"index"})) as check:
            (tmp_path / "foreign.lock").write_bytes(b"foreign")
            (tmp_path / "foreign.lock").unlink()
            check()
    assert fault.value.code == "GIT_COMMIT_NAMESPACE_CHANGED"
