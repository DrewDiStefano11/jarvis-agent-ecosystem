"""Refuse capacity exhaustion before preview approval or native mutation."""

from types import SimpleNamespace

import pytest

from app.core.errors import DomainError
from app.self_build.commit_metadata import reserve_history, reserve_metadata
from app.self_build.commit_objects import GitObject, ObjectProgram


def program():
    return ObjectProgram(
        objects=(GitObject("blob", b"source"),),
        index_payload=b"candidate-index",
        preparation_plan_hash="a" * 64,
        observation_fingerprint="b" * 64,
        tree_sha="c" * 40,
        commit_sha="d" * 40,
    )


def snapshot(count, byte_count=0):
    # Capacity arithmetic is pure; production passes its independently measured
    # and validated snapshot. No synthetic record grants execution authority.
    return SimpleNamespace(
        nodes=tuple(
            SimpleNamespace(path=f"original-{index}", byte_count=byte_count if index == 0 else 0)
            for index in range(count)
        )
    )


def test_metadata_growth_includes_transient_names_before_side_effects():
    reserve_metadata(snapshot(16375), program())
    with pytest.raises(DomainError) as caught:
        reserve_metadata(snapshot(16376), program())
    assert caught.value.code == "GIT_COMMIT_METADATA_CAPACITY_REQUIRED"


def test_byte_budget_keeps_index_staging_and_retry_reflogs_reserved():
    reserve_metadata(snapshot(1, 268435456 - 100000), program())
    with pytest.raises(DomainError) as caught:
        reserve_metadata(snapshot(1, 268435456 - 65536), program())
    assert caught.value.code == "GIT_COMMIT_METADATA_CAPACITY_REQUIRED"


def test_existing_loose_objects_reduce_reserved_growth_without_inode_adoption():
    candidate = program()
    item = candidate.objects[0]
    original = snapshot(16375)
    original.nodes += (
        SimpleNamespace(path="objects/" + item.sha[:2], byte_count=None),
        SimpleNamespace(path="objects/" + item.sha[:2] + "/" + item.sha[2:], byte_count=100),
    )
    reserve_metadata(original, candidate)


def test_complete_history_is_reserved_before_approval_not_exhausted_after_effects():
    class Evidence:
        def __init__(self, size, count=0):
            self.size, self.object_count = size, count

        def model_dump_json(self):
            return "x" * self.size

    reserve_history(Evidence(200000, 4096), Evidence(1000000), Evidence(1000000))
    with pytest.raises(DomainError) as caught:
        reserve_history(Evidence(200000, 8192), Evidence(1000000), Evidence(1000000))
    assert caught.value.code == "GIT_COMMIT_HISTORY_CAPACITY_REQUIRED"
