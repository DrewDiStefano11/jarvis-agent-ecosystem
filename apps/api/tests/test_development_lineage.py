"""Actual two-workspace native source effects; fixture provider is not coding acceptance."""

import json
from hashlib import sha256
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.core.errors import DomainError
from app.development_agent.baseline_repository import DevelopmentBaselineRow
from app.development_agent.lineage_repository import DevelopmentLineageRow
from app.development_agent.lineages import DevelopmentLineageService
from app.development_agent.repository import DevelopmentAdmissionRow, DevelopmentDispatchRow
from app.models.agent_runtime import (
    BeginAttemptCommand,
    ClaimAgentRunCommand,
    CreateAgentRunCommand,
    QueueAgentRunCommand,
    StartAttemptCommand,
)
from app.models.development_agent import ContextSelection
from app.models.development_lineage import ApproveCorrectionWorkspace, PreviewCorrectionWorkspace
from app.models.git_inspection import ApproveRepositoryInspection, InspectRepositoryRequest
from app.models.self_build import ApproveWorkspaceRequest, ReserveWorkspaceRequest, WorkspaceIntent
from app.models.source_mutation import ApplySourceMutation, ApproveSourceMutation
from app.models.workspace_creation import ApproveWorkspaceCreation, CreateWorkspaceRequest
from tests.agent_runtime_testkit import make_spec, ts
from tests.test_development_baselines import completed
from tests.test_development_execution import executor, output

pytest_plugins = ["tests.test_development_agent", "tests.test_development_provider"]
pytestmark = pytest.mark.usefixtures("mock_transport_execution")


def fresh_workspace(development):
    app, actor, operator, authority, _, _, _, context = development
    runtime = app.state.agent_runtime_service
    spec = make_spec(agent_id=actor.actor_id, task_id=context.task_id).model_copy(
        update={"run_id": "run-2", "idempotency_key": "create-run-2"}
    )
    runtime.handle_authorized(
        CreateAgentRunCommand(specification=spec, command_id="create-two", timestamp=ts(0)), actor
    )
    runtime.handle_authorized(
        QueueAgentRunCommand(
            run_id="run-2", command_id="queue-two", expected_run_version=1, timestamp=ts(1)
        ),
        actor,
    )
    runtime.handle_authorized(
        ClaimAgentRunCommand(
            run_id="run-2",
            command_id="claim-two",
            expected_run_version=2,
            timestamp=ts(2),
            executor_reference=authority.worker_id,
        ),
        actor,
    )
    workspaces = app.state.self_build_workspace_service
    intent = WorkspaceIntent(
        repository_id="jarvis", runtime_run_id="run-2", base_sha=context.code_state.base_sha
    )
    plan = workspaces.preview(actor, intent)
    approval = workspaces.approve(
        operator, ApproveWorkspaceRequest(**intent.model_dump(), expected_plan_hash=plan.plan_hash)
    )
    workspace = workspaces.reserve(
        actor,
        ReserveWorkspaceRequest(
            **intent.model_dump(),
            expected_plan_hash=plan.plan_hash,
            approval_id=approval.approval_id,
            worker_id=authority.worker_id,
            lease_token=authority.lease_token,
        ),
    )
    git = app.state.self_build_git_service
    plan = git.preview(actor, workspace.workspace_id)
    approval = git.approve(
        operator,
        ApproveRepositoryInspection(
            workspace_id=workspace.workspace_id, expected_plan_hash=plan.plan_hash
        ),
    )
    observed = git.inspect(
        actor,
        workspace.workspace_id,
        InspectRepositoryRequest(
            expected_plan_hash=plan.plan_hash,
            approval_id=approval.approval_id,
            worker_id=authority.worker_id,
            lease_token=authority.lease_token,
        ),
    )
    before = runtime.read_run_authorized("run-2", actor)
    begun = runtime.handle_authorized(
        BeginAttemptCommand(
            run_id="run-2",
            command_id="begin-two",
            expected_run_version=before.version,
            timestamp=ts(3),
            executor_reference=authority.worker_id,
        ),
        actor,
    )
    runtime.handle_authorized(
        StartAttemptCommand(
            run_id="run-2",
            command_id="start-two",
            expected_run_version=begun.snapshot.version,
            timestamp=ts(4),
            attempt_id=begun.snapshot.active_attempt_id,
        ),
        actor,
    )
    creation = app.state.self_build_creation_service
    plan = creation.preview(actor, workspace.workspace_id, observed.inspection_id)
    approval = creation.approve(
        operator,
        ApproveWorkspaceCreation(
            workspace_id=workspace.workspace_id,
            inspection_id=observed.inspection_id,
            expected_plan_hash=plan.plan_hash,
        ),
    )
    request = CreateWorkspaceRequest(
        inspection_id=observed.inspection_id,
        expected_plan_hash=plan.plan_hash,
        approval_id=approval.approval_id,
        worker_id=authority.worker_id,
        lease_token=authority.lease_token,
    )
    creation.create(actor, workspace.workspace_id, request)
    return workspace.workspace_id, request


@pytest.mark.asyncio
@pytest.mark.timeout(600)
async def test_independent_lineage_preserves_first_work_and_budget_across_real_second_effect(
    development,
    monkeypatch,
):
    app, actor, operator, old_authority, _, old_target, _, context = development
    baselines, first, mutation = await completed(development)
    baseline = baselines.capture(
        actor, context.workspace_id, old_authority, first.dispatch_id, mutation
    )
    with app.state.repository.session_factory() as session:
        origin_payload = session.get(DevelopmentAdmissionRow, context.task_id).payload
    workspace_id, authority = fresh_workspace(development)
    service = DevelopmentLineageService(app)
    preview_request = PreviewCorrectionWorkspace(
        authority=authority,
        baseline_id=baseline.snapshot.baseline_id,
        repair_selection=ContextSelection(paths=("source.py",)),
    )
    plan = service.preview(actor, workspace_id, preview_request)
    approve = ApproveCorrectionWorkspace(
        **preview_request.model_dump(),
        execution_actor_id=actor.actor_id,
        expected_plan_hash=plan.plan_hash,
    )
    with pytest.raises(DomainError):
        service.approve(actor, actor, workspace_id, approve)
    with pytest.raises(DomainError):
        service.approve(
            operator,
            actor,
            workspace_id,
            approve.model_copy(update={"expected_plan_hash": "0" * 64}),
        )
    acknowledge = service.dispatches.acknowledge

    def crash(*args, **kwargs):
        raise RuntimeError("simulated lineage root acknowledgement gap")

    monkeypatch.setattr(service.dispatches, "acknowledge", crash)
    with pytest.raises(RuntimeError):
        service.approve(operator, actor, workspace_id, approve)
    monkeypatch.setattr(service.dispatches, "acknowledge", acknowledge)
    lineage = service.approve(operator, actor, workspace_id, approve)
    assert lineage.admission.admitted_at.isoformat() == origin_payload["admitted_at"].replace(
        "Z", "+00:00"
    )
    assert service.approve(operator, actor, workspace_id, approve) == lineage
    with pytest.raises(DomainError):
        service.approve(
            operator, actor, workspace_id, approve.model_copy(update={"baseline_id": "0" * 64})
        )
    from app.db.models import SystemStateRow

    with app.state.repository.session_factory() as session:
        session.get(SystemStateRow, 1).emergency_stop = True
        session.commit()
    with pytest.raises(DomainError):
        service.recover(actor, workspace_id, authority)
    with app.state.repository.session_factory() as session:
        session.get(SystemStateRow, 1).emergency_stop = False
        session.commit()

    policy = app.state.self_build_workspace_service.policies.get("jarvis")
    target = Path(policy.worktree_root) / workspace_id
    assert (target / "source.py").read_bytes() == b"value = 1\n" and not (
        target / "new.py"
    ).exists()
    calls = []

    def transport(request):
        calls.append(request)
        response = output(request)
        proposal = json.loads(response["output"][0]["content"][0]["text"])
        proposal["files"] = [
            dict(
                operation="modify",
                path="source.py",
                before_digest=sha256(b"value = 2\n").hexdigest(),
                text="value = 3\n",
            )
        ]
        response["output"][0]["content"][0]["text"] = json.dumps(proposal)
        return httpx.Response(200, json=response)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), trust_env=False
    ) as client:
        engine = executor(app, client)
        second = await engine.execute(actor, workspace_id, authority, context.selection, "correct")
        assert second.ordinal == 2 and len(calls) == 1
        plan = engine.mutations.preview(actor, second.editing_record_id)
        grant = engine.mutations.approve(
            operator,
            ApproveSourceMutation(
                editing_record_id=second.editing_record_id, expected_plan_hash=plan.plan_hash
            ),
        )
        request = ApplySourceMutation(
            editing_record_id=second.editing_record_id,
            expected_plan_hash=plan.plan_hash,
            approval_id=grant.approval_id,
            worker_id=authority.worker_id,
            lease_token=authority.lease_token,
        )
        engine.apply_approved(actor, workspace_id, authority, second.dispatch_id, request)
        assert (target / "source.py").read_bytes() == b"value = 3\n"
        assert (target / "new.py").read_bytes() == (old_target / "new.py").read_bytes()
        assert (old_target / "source.py").read_bytes() == b"value = 2\n"
        assert (
            await engine.execute(actor, workspace_id, authority, context.selection, "correct")
        ).dispatch_id == second.dispatch_id
        assert len(calls) == 1
    with app.state.repository.session_factory() as session:
        assert session.get(DevelopmentAdmissionRow, context.task_id).payload == origin_payload
        assert len(session.scalars(select(DevelopmentLineageRow)).all()) == 1
        assert len(session.scalars(select(DevelopmentDispatchRow)).all()) == 2
        assert len(session.scalars(select(DevelopmentBaselineRow)).all()) == 1
