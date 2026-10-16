"""No uncertain effect is replayed solely from a journal phase."""

import pytest
from pydantic import ValidationError

from app.models.git_execution import CommitRecoveryPosition, NativeCommitRecovery
from app.self_build.commit_recovery import recovery_step
from app.self_build.policy import digest


def position(branch="parent", index="original", objects="complete", step="index"):
    return CommitRecoveryPosition(branch=branch, index=index, objects=objects, next_step=step)


@pytest.mark.parametrize(
    "phase,expected",
    [
        ("prepared", "acknowledge_objects"),
        ("objects_verified", "prepare_index"),
        ("index_prepared", "index"),
        ("indexed", "blocked"),
        ("reference_prepared", "blocked"),
        ("committed", "blocked"),
        ("checkpointed", "blocked"),
    ],
)
def test_original_index_cannot_regress_acknowledged_effects(phase, expected):
    assert recovery_step(phase, position()) == expected


@pytest.mark.parametrize(
    "phase,expected",
    [
        ("prepared", "blocked"),
        ("objects_verified", "blocked"),
        ("index_prepared", "acknowledge_index"),
        ("indexed", "prepare_reference"),
        ("reference_prepared", "reference"),
        ("committed", "blocked"),
        ("checkpointed", "blocked"),
    ],
)
def test_exact_new_index_requires_prior_durable_index_intent(phase, expected):
    assert recovery_step(phase, position(index="exact_tree", step="reference")) == expected


@pytest.mark.parametrize(
    "phase,expected",
    [
        ("prepared", "blocked"),
        ("objects_verified", "blocked"),
        ("index_prepared", "blocked"),
        ("indexed", "blocked"),
        ("reference_prepared", "checkpoint"),
        ("committed", "checkpoint"),
        ("checkpointed", "checkpoint"),
    ],
)
def test_exact_created_commit_is_acknowledged_without_reference_replay(phase, expected):
    assert (
        recovery_step(phase, position(branch="exact_commit", index="exact_tree", step="checkpoint"))
        == expected
    )


@pytest.mark.parametrize("field", ["branch", "index", "objects"])
def test_foreign_state_always_blocks(field):
    values = dict(branch="parent", index="original", objects="complete", next_step="blocked")
    values[field] = "foreign"
    observed = CommitRecoveryPosition(**values)
    for phase in ("prepared", "index_prepared", "reference_prepared", "committed"):
        assert recovery_step(phase, observed) == "blocked"


def test_incoherent_or_model_invented_recovery_position_is_rejected():
    with pytest.raises(ValidationError):
        position(objects="incomplete", index="exact_tree", step="reference")
    with pytest.raises(ValidationError):
        recovery_step("approved-by-model", position())
    with pytest.raises(ValidationError):
        recovery_step(
            "reference_prepared", position().model_copy(update={"next_step": "checkpoint"})
        )


@pytest.mark.parametrize(
    "phase,expected",
    [
        ("prepared", "blocked"),
        ("objects_verified", "blocked"),
        ("index_prepared", "index"),
        ("indexed", "blocked"),
        ("reference_prepared", "blocked"),
        ("committed", "blocked"),
        ("checkpointed", "blocked"),
    ],
)
def test_owned_original_backup_and_stage_allow_only_pending_index_resume(phase, expected):
    assert recovery_step(phase, position(index="installing")) == expected


def recovery_receipt(**changes):
    payload = dict(
        operation_id="commit-exec-" + "a" * 32,
        workspace_id="workspace-proof",
        command_id="command-proof",
        original_plan_hash="a" * 64,
        record_digest="b" * 64,
        metadata_snapshot_digest="c" * 64,
        phase="index_prepared",
        position=position(index="installing").model_dump(mode="json"),
        next_step="index",
    )
    payload.update(changes)
    return NativeCommitRecovery(**payload, recovery_hash=digest(payload))


def test_recovery_receipt_cannot_rehash_an_unacknowledged_commit_into_success():
    with pytest.raises(ValidationError):
        recovery_receipt(phase="committed", next_step="checkpoint")
    observed = recovery_receipt()
    with pytest.raises(ValidationError):
        NativeCommitRecovery.model_validate(
            observed.model_copy(update={"record_digest": "0" * 64}).model_dump(mode="json")
        )
