"""Owned namespace replay/collision tests use isolated temporary roots only."""

from pathlib import Path

import pytest

from app.core.errors import DomainError
from app.self_build.policy import RepositoryPolicy
from app.self_build.workspace_namespace import owned_namespace

KEY = "jarvis-" + "a" * 32


@pytest.fixture
def namespace(tmp_path):
    primary, worktrees = tmp_path / "primary", tmp_path / "worktrees"
    primary.mkdir()
    worktrees.mkdir()
    policy = RepositoryPolicy(
        repository_identity="github.com/example/jarvis",
        primary_root=str(primary),
        worktree_root=str(worktrees),
    )
    owner = dict(workspaceId=KEY, operationId="creation-1", planHash="b" * 64, nonce="c" * 64)
    return policy, owner


def test_claim_replay_preserves_private_owner_and_pinned_target_identity(namespace):
    policy, owner = namespace
    calls = []
    arguments = dict(authority_check=lambda: calls.append("fence"))
    with owned_namespace(policy, KEY, owner, **arguments) as (_, root, target, first):
        assert target.parent == root.path
        assert target.is_dir() and list(target.iterdir()) == []
        identity = target.stat().st_ino
    marker = Path(policy.worktree_root) / (".jarvis-owner-" + KEY) / "owner.json"
    original = marker.read_bytes()
    with owned_namespace(policy, KEY, owner, **arguments) as (_, _, target, second):
        assert first == second and target.stat().st_ino == identity
    assert marker.read_bytes() == original and len(calls) >= 6


def test_different_persisted_intent_cannot_adopt_or_overwrite(namespace):
    policy, owner = namespace
    with owned_namespace(policy, KEY, owner, authority_check=lambda: None):
        pass
    marker = Path(policy.worktree_root) / (".jarvis-owner-" + KEY) / "owner.json"
    before = marker.read_bytes()
    with pytest.raises(DomainError, match="different intent") as error:
        with owned_namespace(
            policy, KEY, {**owner, "nonce": "d" * 64}, authority_check=lambda: None
        ):
            pass
    assert error.value.code == "SELF_BUILD_OWNERSHIP_CONFLICT"
    assert marker.read_bytes() == before


def test_existing_unmarked_directory_is_uncertain_and_never_adopted(namespace):
    policy, owner = namespace
    private = Path(policy.worktree_root) / (".jarvis-owner-" + KEY)
    private.mkdir()
    with pytest.raises(DomainError) as error:
        with owned_namespace(policy, KEY, owner, authority_check=lambda: None):
            pass
    assert error.value.code == "SELF_BUILD_OWNERSHIP_UNCERTAIN"
    assert list(private.iterdir()) == []


def test_revocation_before_claim_creates_no_namespace(namespace):
    policy, owner = namespace

    def revoked():
        raise DomainError("EXECUTION_STOPPED", "Stopped", 409)

    with pytest.raises(DomainError) as error:
        with owned_namespace(policy, KEY, owner, authority_check=revoked):
            pass
    assert error.value.code == "EXECUTION_STOPPED"
    assert list(Path(policy.worktree_root).iterdir()) == []


@pytest.mark.parametrize("key", ["../outside", "jarvis-wrong", KEY + "/child"])
def test_non_generated_names_cannot_be_selected(namespace, key):
    policy, owner = namespace
    with pytest.raises(DomainError):
        with owned_namespace(policy, key, owner, authority_check=lambda: None):
            pass
    assert list(Path(policy.worktree_root).iterdir()) == []


def test_existing_target_cannot_receive_a_new_ownership_claim(namespace):
    policy, owner = namespace
    target = Path(policy.worktree_root) / KEY
    target.mkdir()
    original = target / "user.txt"
    original.write_text("unrelated workspace")
    with pytest.raises(DomainError) as error:
        with owned_namespace(policy, KEY, owner, authority_check=lambda: None):
            pass
    assert error.value.code == "SELF_BUILD_OWNERSHIP_CONFLICT"
    assert original.read_text() == "unrelated workspace"
    assert not (target.parent / (".jarvis-owner-" + KEY)).exists()


def test_same_namespace_is_serialized_while_other_missions_remain_independent(namespace):
    policy, owner = namespace
    other_key = "jarvis-" + "d" * 32
    other = {**owner, "workspaceId": other_key, "operationId": "creation-2", "nonce": "e" * 64}
    with owned_namespace(policy, KEY, owner, authority_check=lambda: None):
        with pytest.raises(DomainError) as error:
            with owned_namespace(policy, KEY, owner, authority_check=lambda: None):
                pass
        assert error.value.code == "TOOL_WORKSPACE_BUSY"
        with owned_namespace(policy, other_key, other, authority_check=lambda: None) as (
            _,
            _,
            target,
            _,
        ):
            assert target.name == other_key and target.is_dir() and list(target.iterdir()) == []


def test_preparation_replay_cannot_adopt_target_added_after_private_claim(namespace):
    policy, owner = namespace
    with owned_namespace(policy, KEY, owner, authority_check=lambda: None):
        pass
    target = Path(policy.worktree_root) / KEY
    target.rename(target.with_name("saved-original"))
    target.mkdir()
    foreign = target / "user.txt"
    foreign.write_bytes(b"foreign workspace")
    marker = target.parent / (".jarvis-owner-" + KEY) / "owner.json"
    original = marker.read_bytes()
    with pytest.raises(DomainError) as failure:
        with owned_namespace(policy, KEY, owner, authority_check=lambda: None):
            pytest.fail("foreign target yielded as owned")
    assert failure.value.code == "SELF_BUILD_OWNERSHIP_CONFLICT"
    assert foreign.read_bytes() == b"foreign workspace" and marker.read_bytes() == original


def test_target_created_at_atomic_claim_boundary_is_never_adopted(namespace, monkeypatch):
    from app.self_build import workspace_namespace

    policy, owner = namespace
    native = workspace_namespace.pinned_target_directory
    target = Path(policy.worktree_root) / KEY

    def foreign(stack, root, leaf, *, create):
        if create:
            target.mkdir()
            (target / "foreign.txt").write_bytes(b"foreign")
        return native(stack, root, leaf, create=create)

    monkeypatch.setattr(workspace_namespace, "pinned_target_directory", foreign)
    with pytest.raises(DomainError) as failure:
        with owned_namespace(policy, KEY, owner, authority_check=lambda: None):
            pytest.fail("foreign target yielded")
    assert failure.value.code == "SELF_BUILD_OWNERSHIP_CONFLICT"
    assert (target / "foreign.txt").read_bytes() == b"foreign"
    assert not (target.parent / (".jarvis-owner-" + KEY) / "target.json").exists()


def test_atomically_claimed_target_cannot_be_replaced_before_context_release(namespace):
    policy, owner = namespace
    with owned_namespace(policy, KEY, owner, authority_check=lambda: None) as (_, _, target, _):
        with pytest.raises(PermissionError):
            target.rename(target.with_name("replacement"))
        with pytest.raises(PermissionError):
            target.rmdir()
        # Child creation is allowed without granting directory replacement rights.
        (target / "owned.txt").write_bytes(b"owned")
    assert (target / "owned.txt").read_bytes() == b"owned"


def test_unavailable_atomic_directory_adapter_has_no_filesystem_effects(namespace, monkeypatch):
    from app.self_build import workspace_namespace

    policy, owner = namespace
    monkeypatch.setattr(workspace_namespace, "supports_atomic_directory", lambda: False)
    with pytest.raises(DomainError) as failure:
        with owned_namespace(policy, KEY, owner, authority_check=lambda: None):
            pytest.fail("unavailable adapter yielded")
    assert failure.value.code == "SELF_BUILD_NAMESPACE_PLATFORM_UNAVAILABLE"
    assert list(Path(policy.worktree_root).iterdir()) == []
