"""Checkpoint identity and derived-path boundaries without filesystem authority."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.core.errors import DomainError
from app.models.git_changes import NativeArtifact
from app.models.source_change import source_path
from app.models.source_mutation import OriginalIdentity
from app.self_build import native_artifacts
from app.self_build.policy import digest


def payload():
    return dict(
        path=".jarvis-edit-" + "a" * 64 + ".before",
        kind="source_backup",
        sha256="a" * 64,
        byte_count=0,
        provenance_digest="b" * 64,
    )


def test_legacy_artifact_shape_and_hash_are_preserved():
    original = payload()
    artifact = NativeArtifact(**original)
    assert artifact.model_dump(mode="json") == original
    assert digest(artifact.model_dump(mode="json")) == digest(original)
    assert NativeArtifact.model_validate_json(artifact.model_dump_json()) == artifact
    claimed = NativeArtifact(**original, device=0, inode=1)
    assert digest(claimed.model_dump(mode="json")) != digest(original)


@pytest.mark.parametrize(
    "identity",
    [
        {"device": 0},
        {"inode": 1},
        {"device": -1, "inode": 1},
        {"device": 0, "inode": 0},
        {"device": True, "inode": 1},
        {"device": 0, "inode": "1"},
    ],
)
def test_backup_identity_requires_a_strict_pair(identity):
    with pytest.raises(ValidationError):
        NativeArtifact(**payload(), **identity)


@pytest.mark.parametrize(
    "parent",
    [
        "a" * 119 + "/" + "b" * 119,
        "../ordinary",
        "ordinary/",
        "ordinary\\alias",
        ".git/ordinary",
        "credentials/ordinary",
        "ordinary/CON",
        "ordinary/alias~1",
        "ordinary/\u200bhidden",
        "/ordinary",
    ],
)
def test_derived_paths_do_not_expand_source_parent_authority(parent):
    with pytest.raises((ValueError, DomainError)):
        NativeArtifact(**(payload() | {"path": parent + "/" + payload()["path"]}))


def history_fixture(monkeypatch, originals):
    parent = "a" * 118 + "/" + "b" * 119
    source_path(parent + "/x")
    change = SimpleNamespace(
        path=parent + "/x", before=SimpleNamespace(digest="a" * 64, byte_count=0)
    )
    source = SimpleNamespace(
        record_id="source",
        plan=SimpleNamespace(
            plan_hash="b" * 64, observation=None, source_plan=SimpleNamespace(changes=(change,))
        ),
    )
    record = SimpleNamespace(
        state="complete",
        checkpoint_id="checkpoint",
        plan=SimpleNamespace(editing_plan_hash=source.plan.plan_hash),
        completed_count=1,
        pending_index=None,
        staging_inode=None,
        final_observation_digest=digest({}),
        original_identities=originals,
        operation_id="mutation",
    )
    history = SimpleNamespace(
        record=record, native_checkpoint_present=True, journal_checkpoint_acknowledged=True
    )
    monkeypatch.setattr(
        native_artifacts,
        "MutationHistoryReader",
        lambda app: SimpleNamespace(read=lambda *args: history),
    )
    monkeypatch.setattr(
        native_artifacts,
        "expected_observation",
        lambda *args: SimpleNamespace(model_dump=lambda **kwargs: {}),
    )
    monkeypatch.setattr(native_artifacts, "intent_digest", lambda record: "c" * 64)
    return source


@pytest.mark.parametrize("originals", [(), (OriginalIdentity(change_index=1, device=0, inode=1),)])
def test_manifest_refuses_missing_or_unrelated_checkpoint_identity(monkeypatch, originals):
    source = history_fixture(monkeypatch, originals)
    with pytest.raises(DomainError) as failure:
        native_artifacts.mutation_artifacts(None, None, source)
    assert failure.value.code == "GIT_COMMIT_APPLIED_SOURCE_REQUIRED"


def test_manifest_carries_exact_checkpoint_identity_for_long_derived_path(monkeypatch):
    claim = OriginalIdentity(change_index=0, device=17, inode=123)
    source = history_fixture(monkeypatch, (claim,))
    manifest = native_artifacts.mutation_artifacts(None, None, source)
    (artifact,) = manifest.artifacts
    assert len(artifact.path) > 240
    assert (artifact.device, artifact.inode) == (claim.device, claim.inode)
