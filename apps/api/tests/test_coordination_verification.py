"""Independent node critique through the real coordinator and native checkpoints."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest

from app.model_providers.contracts import ModelExecutionResponse
from tests.test_coordination import claim_fixture, install_coordinator_router, takeover

pytest_plugins = ["tests.test_coordination"]


def critic_router(app, coordinator, *, outcome="passed", invalid=0, invented=False):
    base = install_coordinator_router(app, coordinator)
    original = base.execute
    base.critic_requests = []

    async def execute(*, request, requirements, budget, pricing=None):
        if request.output_schema.name != "independent_node_verdict":
            return await original(request=request, requirements=requirements, budget=budget)
        base.critic_requests.append(request)
        payload = json.loads(request.messages[-1].content.split("\nPrevious critic output")[0])
        evidence = "invented-proof" if invented else next(iter(payload["evidence"]))
        checks = [
            {
                "criterionId": f"criterion-{index}",
                "outcome": outcome,
                "evidenceIds": [evidence],
                "reason": "Checks the exact recorded result against frozen requirements",
            }
            for index, _ in enumerate(payload["frozenInputs"]["planned"]["completionCriteria"])
        ]
        content = (
            "invalid" if len(base.critic_requests) <= invalid else json.dumps({"checks": checks})
        )
        return ModelExecutionResponse(
            content=content, provider="fixture", model="scripted", latency_ms=0
        )

    base.execute = execute
    return base


@pytest.mark.asyncio
@pytest.mark.parametrize("prepared", [{"verify": True}], indirect=True)
async def test_independent_node_verdict_precedes_success_and_replays_after_lost_ack(
    app, prepared, monkeypatch
):
    coordinator, actor, _, record, fence = claim_fixture(app, prepared)
    router = critic_router(app, coordinator)
    original = coordinator.repository.record_success

    def lose_ack(*args, **kwargs):
        raise KeyboardInterrupt("after durable node verdict")

    monkeypatch.setattr(coordinator.repository, "record_success", lose_ack)
    with pytest.raises(KeyboardInterrupt):
        await coordinator.run_once(record.id, fence, actor)
    assert len(router.critic_requests) == len(router.coordinator_calls) == 1
    monkeypatch.setattr(coordinator.repository, "record_success", original)
    fence = takeover(app, fence)
    recovered = await coordinator.run_once(record.id, fence, actor)
    assert recovered.nodes[0].status == "succeeded", recovered.model_dump(mode="json")
    assert len(router.critic_requests) == len(router.coordinator_calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("prepared", [{"verify": True}], indirect=True)
@pytest.mark.parametrize("outcome", ["failed", "needs_correction", "unverifiable"])
async def test_nonpassing_node_critic_cannot_succeed(app, prepared, outcome):
    coordinator, actor, _, record, fence = claim_fixture(app, prepared)
    router = critic_router(app, coordinator, outcome=outcome)
    rejected = await coordinator.run_once(record.id, fence, actor)
    assert rejected.nodes[0].status == ("failed" if outcome == "unverifiable" else "retrying")
    assert len(router.critic_requests) == len(router.coordinator_calls) == 1
    assert rejected.modelDispatchCount == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("prepared", [{"verify": True}], indirect=True)
async def test_node_critic_repairs_schema_once_but_rejects_invented_evidence(app, prepared):
    coordinator, actor, _, record, fence = claim_fixture(app, prepared)
    router = critic_router(app, coordinator, invented=True)
    rejected = await coordinator.run_once(record.id, fence, actor)
    assert rejected.nodes[0].status == "failed"
    assert len(router.critic_requests) == 2
    assert rejected.modelDispatchCount == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("prepared", [{"verify": True}], indirect=True)
async def test_verified_nodes_complete_real_native_coordination(app, prepared):
    coordinator, actor, _, record, fence = claim_fixture(app, prepared)
    coordinator.task_leases = app.state.task_leases
    router = critic_router(app, coordinator)
    for _ in range(5):
        record = await coordinator.run_once(record.id, fence, actor)
    assert record.status == "completed"
    assert all(node.status == "succeeded" for node in record.nodes)
    assert app.state.repository.get_task_durable(record.taskId).status == "completed"
    assert len(router.critic_requests) == 3
    assert len(router.coordinator_calls) == 4
    assert record.modelDispatchCount == 7


@pytest.mark.asyncio
@pytest.mark.parametrize("prepared", [{"verify": True}], indirect=True)
async def test_node_critic_schema_repair_can_pass(app, prepared):
    coordinator, actor, _, record, fence = claim_fixture(app, prepared)
    router = critic_router(app, coordinator, invalid=1)
    record = await coordinator.run_once(record.id, fence, actor)
    assert record.nodes[0].status == "succeeded"
    assert len(router.critic_requests) == 2
    assert record.modelDispatchCount == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("prepared", [{"verify": True}], indirect=True)
async def test_native_success_requires_verdict_even_when_service_is_bypassed(
    app, prepared, monkeypatch
):
    coordinator, actor, _, record, fence = claim_fixture(app, prepared)
    router = critic_router(app, coordinator)

    async def bypass(*args):
        return True

    monkeypatch.setattr(coordinator, "_verify_node", bypass)
    rejected = await coordinator.run_once(record.id, fence, actor)
    assert rejected.status == "blocked"
    assert rejected.blockedReason == "COORDINATION_VERIFICATION_REQUIRED"
    assert all(node.status != "succeeded" for node in rejected.nodes)
    assert len(router.critic_requests) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("prepared", [{"verify": True}], indirect=True)
async def test_elapsed_policy_deadline_prevents_physical_dispatch(app, prepared, monkeypatch):
    from app.coordination import service as service_module

    coordinator, actor, _, record, fence = claim_fixture(app, prepared)
    router = critic_router(app, coordinator)
    deadline = app.state.agent_runtime_service.repository.load_run(
        record.runtimeRunId
    ).specification.deadline

    class ExpiredClock:
        @staticmethod
        def now(tz):
            return deadline + timedelta(seconds=1)

    monkeypatch.setattr(service_module, "datetime", ExpiredClock)
    rejected = await coordinator.run_once(record.id, fence, actor)
    assert rejected.status == "blocked"
    assert rejected.blockedReason == "COORDINATION_DEADLINE_EXCEEDED"
    assert len(router.coordinator_calls) == len(router.critic_requests) == 0
    assert rejected.modelDispatchCount == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("prepared", [{"verify": True}], indirect=True)
async def test_coordinator_source_timeout_clamps_to_frozen_deadline(app, prepared, monkeypatch):
    from app.coordination import service as service_module

    coordinator, actor, _, record, fence = claim_fixture(app, prepared)
    router = critic_router(app, coordinator)
    deadline = app.state.agent_runtime_service.repository.load_run(
        record.runtimeRunId
    ).specification.deadline
    requests = []
    original = router.execute

    async def capture(**kwargs):
        requests.append(kwargs["request"])
        return await original(**kwargs)

    class NearDeadlineClock:
        @staticmethod
        def now(tz):
            return deadline - timedelta(seconds=3)

    router.execute = capture
    monkeypatch.setattr(service_module, "datetime", NearDeadlineClock)
    record = await coordinator.run_once(record.id, fence, actor)
    assert record.nodes[0].status == "succeeded"
    assert len(requests) == 2
    assert all(0 < request.timeout_seconds <= 3 for request in requests)
    assert datetime.now(UTC) < deadline


@pytest.mark.asyncio
@pytest.mark.parametrize("prepared", [{"verify": True}], indirect=True)
@pytest.mark.parametrize("alteration", ["foreign_task", "foreign_criterion", "false_pass"])
async def test_native_success_revalidates_even_resealed_verdict(
    app, prepared, monkeypatch, alteration
):
    from app.db.models import AgentRuntimeCheckpointRow
    from app.models.agent_runtime import canonical_json, stable_hash

    coordinator, actor, _, record, fence = claim_fixture(app, prepared)
    critic_router(app, coordinator)
    original = coordinator.repository.record_success

    def alter_proof(*args, **kwargs):
        node = coordinator._required(record.id).nodes[0]
        checkpoint_id = "critic-" + stable_hash([node.runtimeAttemptId, "verdict"])[:48]
        with app.state.repository.session_factory() as session, session.begin():
            row = session.get(AgentRuntimeCheckpointRow, (checkpoint_id, node.runtimeRunId))
            envelope = json.loads(row.contract_json)
            metadata = envelope["metadata"]
            data = json.loads("".join(metadata["dataChunks"]))
            if alteration == "foreign_task":
                data["taskId"] = "unrelated-task"
            elif alteration == "foreign_criterion":
                data["checks"][0]["criterionId"] = "invented-criterion"
            else:
                data["checks"][0]["outcome"] = "failed"
            data.pop("digest")
            data["digest"] = stable_hash(data)
            material = canonical_json(data)
            metadata["dataChunks"] = [material[i : i + 2000] for i in range(0, len(material), 2000)]
            envelope["integrity_digest"] = "sha256:" + stable_hash(
                {"binding": metadata["binding"], "data": data}
            )
            row.contract_json = canonical_json(envelope)
        return original(*args, **kwargs)

    monkeypatch.setattr(coordinator.repository, "record_success", alter_proof)
    rejected = await coordinator.run_once(record.id, fence, actor)
    assert rejected.status == "blocked"
    assert rejected.blockedReason == "COORDINATION_VERIFICATION_REQUIRED"
    assert rejected.nodes[0].status != "succeeded"


@pytest.mark.asyncio
@pytest.mark.parametrize("prepared", [{"verify": True}], indirect=True)
async def test_same_lease_concurrency_does_not_duplicate_critic(app, prepared):
    coordinator, actor, _, record, fence = claim_fixture(app, prepared)
    router = critic_router(app, coordinator)
    entered, release = asyncio.Event(), asyncio.Event()
    original = router.execute

    async def hold(**kwargs):
        if kwargs["request"].output_schema.name == "independent_node_verdict":
            entered.set()
            await release.wait()
        return await original(**kwargs)

    router.execute = hold
    first = asyncio.create_task(coordinator.run_once(record.id, fence, actor))
    await asyncio.wait_for(entered.wait(), timeout=5)
    try:
        second = await coordinator.run_once(record.id, fence, actor)
        assert second.nodes[0].status == "running"
        assert second.modelDispatchCount == 2
    finally:
        release.set()
        completed = await first
    assert completed.nodes[0].status == "succeeded"
    assert len(router.critic_requests) == len(router.coordinator_calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("prepared", [{"verify": True}], indirect=True)
async def test_unknown_critic_outcome_requires_exception_without_repeat(app, prepared):
    coordinator, actor, _, record, fence = claim_fixture(app, prepared)
    router = critic_router(app, coordinator)
    original = router.execute

    async def interrupt(**kwargs):
        result = await original(**kwargs)
        if kwargs["request"].output_schema.name == "independent_node_verdict":
            raise KeyboardInterrupt("critic response lost before checkpoint")
        return result

    router.execute = interrupt
    with pytest.raises(KeyboardInterrupt):
        await coordinator.run_once(record.id, fence, actor)
    router.execute = original
    fence = takeover(app, fence)
    recovered = await coordinator.run_once(record.id, fence, actor)
    assert recovered.nodes[0].status == "failed"
    assert "unverifiable" in recovered.nodes[0].failureDetail
    assert len(router.critic_requests) == len(router.coordinator_calls) == 1
    assert recovered.modelDispatchCount == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("prepared", [{"verify": True}], indirect=True)
@pytest.mark.parametrize("change", ["stop", "revoke", "suspend"])
async def test_critic_response_cannot_override_late_native_authority(app, prepared, change):
    from sqlalchemy import select

    from app.db.models import (
        AgentPermissionAssignmentRow,
        IdentityAgentRow,
        SystemStateRow,
    )

    coordinator, actor, graph, record, fence = claim_fixture(app, prepared)
    router = critic_router(app, coordinator)
    original = router.execute

    async def invalidate(**kwargs):
        answer = await original(**kwargs)
        if kwargs["request"].output_schema.name == "independent_node_verdict":
            with app.state.repository.session_factory() as session, session.begin():
                if change == "stop":
                    session.get(SystemStateRow, 1).emergency_stop = True
                elif change == "suspend":
                    session.get(
                        IdentityAgentRow, graph.subtasks[0].assignedAgentId
                    ).lifecycle_state = "suspended"
                else:
                    for row in session.scalars(
                        select(AgentPermissionAssignmentRow).where(
                            AgentPermissionAssignmentRow.agent_id == actor.actor_id
                        )
                    ):
                        row.revoked_at = datetime.now(UTC) - timedelta(seconds=1)
        return answer

    router.execute = invalidate
    rejected = await coordinator.run_once(record.id, fence, actor)
    assert rejected.status == "blocked"
    assert rejected.nodes[0].status != "succeeded"
    assert app.state.repository.get_task_durable(record.taskId).status != "completed"
    assert len(router.critic_requests) == len(router.coordinator_calls) == 1
