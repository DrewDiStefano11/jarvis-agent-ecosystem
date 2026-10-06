"""Selection is measured opportunity ordering, not approval or a second evaluator."""

import pytest

from app.self_improvement.backlog_selection import ordered_candidates, scope_key
from app.self_improvement.engine import analyze
from tests.test_self_improvement import baseline, source


@pytest.fixture
def backlog_app(tmp_path):
    from app.main import create_app
    from app.models.identity import AssignPermissionRequest, CreatePermissionRequest
    from app.models.improvement_backlog import SelectImprovementRequest
    from app.self_improvement.backlog_service import ImprovementBacklogService
    from app.self_improvement.repository import ImprovementRepository
    from tests.test_agent_runtime_authorization import create_actor
    from tests.test_persistence import database_url

    app = create_app(database_url=database_url(tmp_path / "backlog.db"))
    # Service tests own one event loop. HTTP acceptance starts the real lifespan
    # separately; sharing its asyncio dispatcher across loops is invalid.
    try:
        identity = app.state.identity_service
        actor_id = create_actor(app, "backlog-operator")
        permission = identity.create_definition(
            "permission",
            CreatePermissionRequest(
                stable_key="self_improvement.select",
                display_name="Select improvement work",
                resource_type="administrative_function",
                action="select_improvement",
            ),
        )
        scope = dict(
            permission_id=permission.id,
            resource_type="administrative_function",
            resource_id="improvement_backlog",
        )
        identity.assign_permission(actor_id, AssignPermissionRequest(effect="allow", **scope))
        actor = app.state.agent_runtime_service.authenticate_actor(actor_id)
        service = ImprovementBacklogService(app.state.repository, app.state.broker, identity)
        analysis = ImprovementRepository(app.state.repository.session_factory).save_analysis(
            analyze(baseline())
        )
        request = SelectImprovementRequest(baseline_ids=(analysis.baseline.id,))
        yield app, actor, service, analysis, request, scope
    finally:
        app.state.engine.dispose()


@pytest.mark.asyncio
async def test_native_admission_and_uncertain_ack_replay_are_one_durable_task(
    backlog_app, monkeypatch
):
    from sqlalchemy import select

    from app.db.models import AgentRuntimeRunRow, AuditEventRow, OutboxEventRow
    from app.self_improvement.backlog_service import ImprovementBacklogService

    app, actor, service, analysis, request, _ = backlog_app
    publish = app.state.broker._publish

    async def lose_ack(*args, **kwargs):
        raise RuntimeError("committed admission acknowledgement lost")

    monkeypatch.setattr(app.state.broker, "_publish", lose_ack)
    with pytest.raises(RuntimeError, match="acknowledgement lost"):
        await service.select(actor, request, "admission-1")
    monkeypatch.setattr(app.state.broker, "_publish", publish)
    restarted = ImprovementBacklogService(
        app.state.repository, app.state.broker, app.state.identity_service
    )
    replay = await restarted.select(actor, request, "admission-1")
    assert replay.outcome == "replayed"
    assert replay.entry.evidence_ids == analysis.proposals[0].evidence_ids
    task = app.state.repository.get_task_durable(replay.entry.task_id)
    assert task.status == "queued" and task.createdBy == actor.actor_id
    assert replay.entry.proposal_id in task.description
    assert len(service.backlog.entries()) == 1
    with app.state.repository.session_factory() as session:
        audit = session.scalars(select(AuditEventRow).where(AuditEventRow.task_id == task.id)).all()
        assert len(audit) == 1 and audit[0].actor == actor.actor_id
        outbox = session.scalars(
            select(OutboxEventRow).where(OutboxEventRow.envelope["taskId"].as_string() == task.id)
        ).all()
        assert len(outbox) == 1 and outbox[0].status == "pending"
        assert not session.scalars(
            select(AgentRuntimeRunRow).where(AgentRuntimeRunRow.task_id == task.id)
        ).all()
    blocked = await service.select(actor, request, "admission-2")
    assert blocked.outcome == "blocked" and blocked.blocked_proposals == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["deny", "suspend", "stop"])
async def test_admission_rechecks_authority_and_stop_at_commit(backlog_app, monkeypatch, boundary):
    from app.core.errors import DomainError
    from app.models.identity import AssignPermissionRequest

    app, actor, service, _, request, scope = backlog_app
    before = set(app.state.repository.tasks)
    emit = app.state.broker.emit

    async def change_before_commit(*args, **kwargs):
        if boundary == "deny":
            app.state.identity_service.assign_permission(
                actor.actor_id, AssignPermissionRequest(effect="deny", **scope)
            )
        elif boundary == "suspend":
            app.state.identity_service.transition(actor.actor_id, "suspended")
        else:
            app.state.repository.emergency_stop = True
            app.state.repository.persist()
        return await emit(*args, **kwargs)

    monkeypatch.setattr(app.state.broker, "emit", change_before_commit)
    with pytest.raises(DomainError) as error:
        await service.select(actor, request, "revoked-selection")
    assert error.value.code == (
        "EMERGENCY_STOP_ACTIVE" if boundary == "stop" else "IMPROVEMENT_BACKLOG_PERMISSION_DENIED"
    )
    assert service.backlog.entries() == []
    app.state.repository.reload()
    assert set(app.state.repository.tasks) == before


def test_critical_evidence_precedes_lower_priority_without_invented_score():
    medium = analyze(baseline())
    critical = analyze(baseline(hard=True, metric="role_gate"))
    candidates = ordered_candidates([medium, critical])
    assert candidates[0].proposal.priority == "critical"
    assert candidates[0].work_kind == "prepare_experiment"
    assert candidates[0].proposal.experiment == critical.proposals[0].experiment


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["id", "task_id", "work_kind", "experiment_digest"])
async def test_fenced_admission_rejects_altered_lineage(backlog_app, monkeypatch, field):
    from app.core.errors import DomainError

    app, actor, service, _, request, _ = backlog_app
    before = set(app.state.repository.tasks)
    admit = service.backlog.admit_in_session

    def alter(session, entry):
        value = {
            "id": "0" * 64,
            "task_id": "task-forged",
            "work_kind": "gather_evidence",
            "experiment_digest": "0" * 64,
        }[field]
        admit(session, entry.model_copy(update={field: value}))

    monkeypatch.setattr(service.backlog, "admit_in_session", alter)
    with pytest.raises(DomainError) as error:
        await service.select(actor, request, "invalid-" + field)
    assert error.value.code == "IMPROVEMENT_BACKLOG_LINEAGE_INVALID"
    app.state.repository.reload()
    assert set(app.state.repository.tasks) == before
    assert service.backlog.entries() == []


def test_cli_admits_fresh_work_into_native_database(backlog_app, capsys):
    import json

    from app.self_improvement.backlog_cli import main

    app, actor, service, analysis, _, _ = backlog_app
    assert (
        main(
            [
                "--database-url",
                app.state.settings.database_url,
                "--actor-id",
                actor.actor_id,
                "select",
                "--baseline",
                analysis.baseline.id,
                "--idempotency-key",
                "cli-fresh",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["outcome"] == "selected"
    task = app.state.repository.get_task_durable(result["entry"]["task_id"])
    assert task.status == "queued" and task.createdBy == actor.actor_id
    assert len(service.backlog.entries()) == 1


def test_incomplete_evidence_selects_evidence_work_without_experiment_approval():
    analysis = analyze(baseline(provenance=source(complete=False)))
    selected = ordered_candidates([analysis])[0]
    assert selected.work_kind == "gather_evidence"
    assert selected.proposal.status == "needs_evidence"
    assert selected.proposal.experiment is None


def test_repeated_baseline_is_not_a_second_candidate():
    analysis = analyze(baseline())
    assert len(ordered_candidates([analysis, analysis])) == 1


def test_fresh_observations_keep_same_active_work_scope():
    first = analyze(baseline(values=(0, 1)))
    later = analyze(baseline(values=(0, 0)))
    assert first.baseline.id != later.baseline.id
    assert scope_key(first, first.proposals[0]) == scope_key(later, later.proposals[0])
    different_provider = analyze(baseline(provider="other-local-provider"))
    assert scope_key(first, first.proposals[0]) != scope_key(
        different_provider, different_provider.proposals[0]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("capture", ["rename", "extend"])
@pytest.mark.parametrize("bypass_projection", [False, True])
async def test_capture_aliases_cannot_duplicate_protected_work(
    backlog_app, monkeypatch, capture, bypass_projection
):
    from app.models.improvement_backlog import SelectImprovementRequest
    from app.self_improvement.engine import create_baseline
    from app.self_improvement.repository import ImprovementRepository

    app, actor, service, original, request, _ = backlog_app
    admitted = await service.select(actor, request, "original-capture")
    app.state.repository.tasks[admitted.entry.task_id].status = "under_review"
    app.state.repository.persist()
    later = baseline(values=(0, 0), provenance=source(source_id="new-capture-alias"))
    if capture == "extend":
        later = create_baseline(
            repo_sha=later.repo_sha,
            configuration_fingerprint=later.configuration_fingerprint,
            safety_fingerprint=later.safety_fingerprint,
            sources=original.baseline.sources + later.sources,
            observations=original.baseline.observations + later.observations,
        )
    saved = ImprovementRepository(app.state.repository.session_factory).save_analysis(
        analyze(later)
    )
    if bypass_projection:
        monkeypatch.setattr(service.backlog, "admission_state", lambda ids: (set(), set()))
    result = await service.select(
        actor, SelectImprovementRequest(baseline_ids=(saved.baseline.id,)), "new-capture"
    )
    assert result.outcome == "blocked"
    assert len(service.backlog.entries()) == 1
    assert app.state.repository.get_task_durable(admitted.entry.task_id).status == "under_review"


@pytest.mark.asyncio
@pytest.mark.parametrize("bypass_projection", [False, True])
async def test_legacy_scope_entries_reserve_work_without_rewriting_history(
    backlog_app, monkeypatch, bypass_projection
):
    from app.models.improvement_backlog import SelectImprovementRequest
    from app.self_improvement import backlog_repository, backlog_selection
    from app.self_improvement.engine import digest
    from app.self_improvement.repository import ImprovementRepository

    def legacy_scope(analysis, proposal):
        observations = {item.id: item for item in analysis.baseline.observations}
        identities = {
            (
                item.source_type,
                item.source_id,
                item.stage,
                item.role,
                item.model,
                item.provider,
                item.metric,
                item.inference_mode,
            )
            for reference in proposal.evidence_ids
            for item in [observations[reference]]
        }
        return digest(["improvement-work-scope-v1", proposal.category, sorted(identities)])

    app, actor, service, _, request, _ = backlog_app
    with monkeypatch.context() as historical:
        historical.setattr(backlog_selection, "scope_key", legacy_scope)
        historical.setattr(backlog_repository, "scope_key", legacy_scope)
        selected = await service.select(actor, request, "legacy-admission")
    original = service.backlog.entries()
    assert original[0].scope_key == selected.entry.scope_key
    later = ImprovementRepository(app.state.repository.session_factory).save_analysis(
        analyze(baseline(values=(0, 0), provenance=source(source_id="new-alias")))
    )
    assert scope_key(later, later.proposals[0]) != original[0].scope_key
    if bypass_projection:
        monkeypatch.setattr(service.backlog, "admission_state", lambda ids: (set(), set()))
    result = await service.select(
        actor, SelectImprovementRequest(baseline_ids=(later.baseline.id,)), "after-legacy"
    )
    assert result.outcome == "blocked"
    assert service.backlog.entries() == original


def test_selection_is_bounded_and_invalid_lineage_fails_closed():
    analysis = analyze(baseline())
    with pytest.raises(ValueError, match="eight"):
        ordered_candidates([analysis] * 9)
    corrupt = analysis.model_copy(
        update={"proposals": (analysis.proposals[0].model_copy(update={"weakness_id": "f" * 64}),)}
    )
    with pytest.raises(ValueError, match="proposal does not match"):
        ordered_candidates([corrupt])


@pytest.mark.asyncio
async def test_empty_selection_rechecks_permission_before_persisting_receipt(
    backlog_app, monkeypatch
):
    from app.core.errors import DomainError
    from app.models.identity import AssignPermissionRequest
    from app.models.improvement_backlog import SelectImprovementRequest
    from app.self_improvement.repository import ImprovementRepository

    app, actor, service, _, _, scope = backlog_app
    empty = ImprovementRepository(app.state.repository.session_factory).save_analysis(
        analyze(baseline(values=(1, 1)))
    )
    request = SelectImprovementRequest(baseline_ids=(empty.baseline.id,))
    inspect = service.backlog.admission_state

    def deny_after_inspection(*args, **kwargs):
        state = inspect(*args, **kwargs)
        app.state.identity_service.assign_permission(
            actor.actor_id, AssignPermissionRequest(effect="deny", **scope)
        )
        return state

    monkeypatch.setattr(service.backlog, "admission_state", deny_after_inspection)
    with pytest.raises(DomainError) as error:
        await service.select(actor, request, "empty-denied")
    assert error.value.code == "IMPROVEMENT_BACKLOG_PERMISSION_DENIED"
    assert service.backlog.entries() == []


@pytest.mark.asyncio
async def test_blocked_critical_work_does_not_starve_independent_candidate(backlog_app):
    from app.models.improvement_backlog import SelectImprovementRequest
    from app.self_improvement.repository import ImprovementRepository

    app, actor, service, medium, _, _ = backlog_app
    critical = ImprovementRepository(app.state.repository.session_factory).save_analysis(
        analyze(baseline(hard=True, metric="role_gate"))
    )
    request = SelectImprovementRequest(baseline_ids=(medium.baseline.id, critical.baseline.id))
    first = await service.select(actor, request, "priority-first")
    assert first.entry.priority == "critical"
    following = await service.select(actor, request, "priority-next")
    assert following.outcome == "selected" and following.entry.priority == "medium"
    assert following.blocked_proposals == 1
    assert following.entry.scope_key != first.entry.scope_key


@pytest.mark.asyncio
async def test_new_baseline_preserves_active_protected_work_until_terminal(backlog_app):
    from app.models.improvement_backlog import SelectImprovementRequest
    from app.self_improvement.repository import ImprovementRepository

    app, actor, service, _, request, _ = backlog_app
    first = await service.select(actor, request, "first-baseline")
    task_id = first.entry.task_id
    app.state.repository.tasks[task_id].status = "under_review"
    app.state.repository.persist()
    later = ImprovementRepository(app.state.repository.session_factory).save_analysis(
        analyze(baseline(values=(0, 0)))
    )
    later_request = SelectImprovementRequest(baseline_ids=(later.baseline.id,))
    blocked = await service.select(actor, later_request, "new-baseline-active")
    assert blocked.outcome == "blocked"
    assert app.state.repository.get_task_durable(task_id).status == "under_review"
    app.state.task_leases.cancel_task(task_id)
    admitted = await service.select(actor, later_request, "new-baseline-terminal")
    assert admitted.outcome == "selected" and admitted.entry.task_id != task_id
    assert app.state.repository.get_task_durable(task_id).status == "cancelled"
    same_old_evidence = await service.select(actor, request, "old-baseline-terminal")
    assert same_old_evidence.outcome == "blocked"


@pytest.mark.asyncio
@pytest.mark.parametrize("bypass_projection", [False, True])
async def test_failed_task_with_retry_remaining_reserves_scope(
    backlog_app, monkeypatch, bypass_projection
):
    from app.models.improvement_backlog import SelectImprovementRequest
    from app.self_improvement.repository import ImprovementRepository

    app, actor, service, _, request, _ = backlog_app
    selected = await service.select(actor, request, "before-worker-failure")
    leases = app.state.task_leases
    worker = leases.register_worker("backlog-test", "backlog-test-instance")
    _, lease = leases.acquire_task(worker.id, task_id=selected.entry.task_id)
    failed = leases.fail_task(
        selected.entry.task_id,
        worker.id,
        lease.leaseToken,
        {"code": "TEST_FAILURE"},
        retryable=False,
    )
    assert failed.status == "failed" and failed.retryCount < failed.maxRetries
    later = ImprovementRepository(app.state.repository.session_factory).save_analysis(
        analyze(baseline(values=(0, 0)))
    )
    later_request = SelectImprovementRequest(baseline_ids=(later.baseline.id,))
    if bypass_projection:
        monkeypatch.setattr(service.backlog, "admission_state", lambda ids: (set(), set()))
        result = await service.select(actor, later_request, "after-worker-failure")
        assert result.outcome == "blocked"
    else:
        assert (
            await service.select(actor, later_request, "after-worker-failure")
        ).outcome == "blocked"
    assert len(service.backlog.entries()) == 1


def test_backlog_selection_openapi_advertises_both_success_envelopes(backlog_app):
    app, *_ = backlog_app
    responses = app.openapi()["paths"]["/api/self-improvement/backlog/select"]["post"]["responses"]
    assert (
        responses["201"]["content"]["application/json"]["schema"]
        == responses["200"]["content"]["application/json"]["schema"]
    )


def test_concurrent_operators_do_not_duplicate_active_work(backlog_app):
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from app.repositories.sqlalchemy import SqlAlchemyRepository
    from app.self_improvement.backlog_service import ImprovementBacklogService
    from app.services.events import EventBroker

    app, actor, _, _, request, _ = backlog_app
    barrier = Barrier(2)
    before = set(app.state.repository.tasks)

    def select(index):
        repository = SqlAlchemyRepository(app.state.repository.session_factory)
        broker = EventBroker(repository)
        emit = broker.emit

        async def contend(*args, **kwargs):
            barrier.wait(timeout=10)
            return await emit(*args, **kwargs)

        broker.emit = contend
        selector = ImprovementBacklogService(repository, broker, app.state.identity_service)
        return asyncio.run(selector.select(actor, request, f"concurrent-{index}"))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(select, [0, 1]))
    assert sorted(result.outcome for result in results) == ["blocked", "selected"]
    app.state.repository.reload()
    assert len(set(app.state.repository.tasks) - before) == 1


@pytest.mark.asyncio
async def test_active_capacity_is_rechecked_inside_admission_transaction(backlog_app, monkeypatch):
    from app.core.errors import DomainError
    from app.models.improvement_backlog import SelectImprovementRequest
    from app.self_improvement import backlog_repository
    from app.self_improvement.repository import ImprovementRepository

    app, actor, service, _, request, _ = backlog_app
    monkeypatch.setattr(backlog_repository, "MAX_ACTIVE_BACKLOG", 2)
    first = await service.select(actor, request, "capacity-first")
    records = ImprovementRepository(app.state.repository.session_factory)
    current = records.save_analysis(analyze(baseline(provider="current-provider")))
    competing = records.save_analysis(analyze(baseline(provider="competing-provider")))
    current_request = SelectImprovementRequest(baseline_ids=(current.baseline.id,))
    competing_request = SelectImprovementRequest(baseline_ids=(competing.baseline.id,))
    emit = app.state.broker.emit
    inserted = False

    async def consume_capacity_before_commit(*args, **kwargs):
        nonlocal inserted
        if not inserted:
            inserted = True
            second = await service.select(actor, competing_request, "capacity-competing")
            assert second.outcome == "selected"
        return await emit(*args, **kwargs)

    monkeypatch.setattr(app.state.broker, "emit", consume_capacity_before_commit)
    with pytest.raises(DomainError) as error:
        await service.select(actor, current_request, "capacity-current")
    assert error.value.code == "IMPROVEMENT_BACKLOG_SCAN_LIMIT"
    entries = service.backlog.entries()
    assert len(entries) == 2 and first.entry.id in {entry.id for entry in entries}
    assert current.baseline.id not in {entry.baseline_id for entry in entries}
    assert all(
        app.state.repository.get_task_durable(entry.task_id).status == "queued" for entry in entries
    )


def test_local_http_and_cli_use_native_admission_and_separate_read_permission(backlog_app, capsys):
    import json

    from fastapi.testclient import TestClient

    from app.models.identity import AssignPermissionRequest, CreatePermissionRequest
    from app.self_improvement.backlog_cli import main

    app, actor, service, analysis, request, _ = backlog_app
    headers = {"X-Jarvis-Actor-Id": actor.actor_id, "Idempotency-Key": "http-selection"}
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/self-improvement/backlog/select",
                json=request.model_dump(mode="json"),
                headers={"Idempotency-Key": "missing-actor"},
            ).status_code
            == 401
        )
        selected = client.post(
            "/api/self-improvement/backlog/select",
            json=request.model_dump(mode="json"),
            headers=headers,
        )
        assert selected.status_code == 201, selected.text
        task_id = selected.json()["data"]["entry"]["task_id"]
        replay = client.post(
            "/api/self-improvement/backlog/select",
            json=request.model_dump(mode="json"),
            headers=headers,
        )
        assert replay.status_code == 200 and replay.json()["data"]["outcome"] == "replayed"
        assert client.get("/api/self-improvement/backlog", headers=headers).status_code == 403
        read = app.state.identity_service.create_definition(
            "permission",
            CreatePermissionRequest(
                stable_key="self_improvement.read",
                display_name="Read improvement backlog",
                resource_type="administrative_function",
                action="read_improvement",
            ),
        )
        app.state.identity_service.assign_permission(
            actor.actor_id,
            AssignPermissionRequest(
                permission_id=read.id,
                effect="allow",
                resource_type="administrative_function",
                resource_id="improvement_backlog",
            ),
        )
        listed = client.get("/api/self-improvement/backlog?limit=1", headers=headers)
        assert listed.status_code == 200 and listed.json()["data"][0]["task_status"] == "queued"
    assert (
        main(
            [
                "--database-url",
                app.state.settings.database_url,
                "--actor-id",
                actor.actor_id,
                "select",
                "--baseline",
                analysis.baseline.id,
                "--idempotency-key",
                "http-selection",
            ]
        )
        == 0
    )
    cli_result = json.loads(capsys.readouterr().out)
    assert cli_result["outcome"] == "replayed" and cli_result["entry"]["task_id"] == task_id
    assert len(service.backlog.entries()) == 1
