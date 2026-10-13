from hashlib import sha256

import pytest

from app.self_build.commit_position import classify_index

ORIGINAL = ((1, 10), b"original index")
CANDIDATE = ((1, 20), b"compiled candidate")


def measured(index, backup=None, stage=None, stage_identity=(1, 20)):
    return classify_index(
        index,
        backup,
        stage,
        original_identity=ORIGINAL[0],
        original_hash=sha256(ORIGINAL[1]).hexdigest(),
        stage_identity=stage_identity,
        candidate=CANDIDATE[1],
    )


def test_interrupted_index_rename_is_recoverable_only_with_both_owned_inodes():
    assert measured(ORIGINAL, stage=CANDIDATE) == "original"
    assert measured(None, ORIGINAL, CANDIDATE) == "installing"
    assert measured(CANDIDATE, ORIGINAL) == "exact_tree"


@pytest.mark.parametrize(
    "index,backup,stage",
    [
        (((1, 30), CANDIDATE[1]), ORIGINAL, None),
        (CANDIDATE, ((1, 30), ORIGINAL[1]), None),
        (CANDIDATE, None, None),
        (CANDIDATE, ORIGINAL, CANDIDATE),
        (None, ORIGINAL, ((1, 30), CANDIDATE[1])),
        (None, None, CANDIDATE),
        (ORIGINAL, ORIGINAL, CANDIDATE),
        ((ORIGINAL[0], b"foreign bytes"), None, None),
        (ORIGINAL, None, (CANDIDATE[0], b"foreign bytes")),
    ],
)
def test_identical_bytes_do_not_grant_inode_ownership_or_cleanup_permission(index, backup, stage):
    assert measured(index, backup, stage) == "foreign"


def test_partial_owned_stage_can_resume_but_cannot_count_as_installed_index():
    partial = (CANDIDATE[0], CANDIDATE[1][:5])
    assert measured(ORIGINAL, stage=partial) == "original"
    assert measured(None, ORIGINAL, partial) == "foreign"


def test_unknown_or_lost_stage_claim_cannot_adopt_matching_bytes():
    assert measured(CANDIDATE, ORIGINAL, stage_identity=(None, None)) == "foreign"
    assert measured(ORIGINAL, stage=CANDIDATE, stage_identity=(None, None)) == "foreign"
    assert measured(ORIGINAL, stage=None) == "foreign"
    assert measured(ORIGINAL, stage=None, stage_identity=(None, None)) == "original"
