"""Creation envelopes cannot choose paths, grant source writes or fake ready shape."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.models.self_build import WorkspacePlan
from app.models.workspace_creation import WorkspaceCreationPlan, WorkspaceCreationRecord


def plan():
    key = "jarvis-" + "a" * 32
    workspace = WorkspacePlan(
        repository_id="repo",
        repository_identity="github.com/example/jarvis",
        policy_digest="b" * 64,
        task_id="task",
        runtime_run_id="run",
        base_branch="main",
        base_sha="c" * 40,
        branch="codex/" + key,
        worktree_key=key,
        plan_hash="d" * 64,
    )
    return WorkspaceCreationPlan(
        workspace_id=key,
        workspace_version=1,
        workspace=workspace,
        inspection_id="inspection",
        base_tree_sha="e" * 40,
        inventory_digest="f" * 64,
        file_count=2,
        tool_identity_digest="1" * 64,
        tool_sha256="2" * 64,
        creation_policy_digest="3" * 64,
        plan_hash="4" * 64,
    )


@pytest.mark.parametrize(
    "mutation",
    [
        {"workspace_id": "other"},
        {"target_path": "C:/Users"},
        {"maximum_file_bytes": 8388609},
        {"maximum_total_bytes": 67108865},
        {"allowed_tools": ["workspace.list", "workspace.write"]},
        {"write_prefixes": ["."]},
        {"file_count": 4097},
    ],
)
def test_creation_plan_cannot_expand_the_derived_boundary(mutation):
    with pytest.raises(ValidationError):
        WorkspaceCreationPlan.model_validate({**plan().model_dump(), **mutation})


def test_ready_shape_requires_complete_inventory_and_checkpoint():
    approved = plan()
    now = datetime.now(UTC)
    values = dict(
        operation_id="operation",
        workspace_id=approved.workspace_id,
        plan=approved,
        approval_id="approval",
        worker_id="worker",
        attempt_id="attempt",
        state="ready",
        checkpoint_id="checkpoint",
        ownership_digest="5" * 64,
        completed_file_count=2,
        created_at=now,
        updated_at=now,
    )
    assert WorkspaceCreationRecord(**values).state == "ready"
    for changed in (
        {"checkpoint_id": None},
        {"completed_file_count": 1},
        {"completed_file_count": 3},
    ):
        with pytest.raises(ValidationError):
            WorkspaceCreationRecord(**{**values, **changed})
