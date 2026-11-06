"""Discovery is bounded advice, with native admission/RBAC still authoritative."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select

from app.core.errors import DomainError
from app.db.models import AuditEventRow, OutboxEventRow, TaskRow
from app.models.identity import AssignPermissionRequest, CreatePermissionRequest
from app.models.improvement_backlog import ImprovementCandidate
from app.self_improvement.candidate_repository import CandidateRepository
from app.self_improvement.engine import analyze
from app.self_improvement.repository import ImprovementRecordRow, ImprovementRepository
from tests.test_self_improvement import NOW, baseline, source

pytest_plugins = ["tests.test_improvement_backlog"]


@pytest.fixture
def candidates(backlog_app):
    app, actor, service, analysis, request, _ = backlog_app
    permission = app.state.identity_service.create_definition(
        "permission",
        CreatePermissionRequest(
            stable_key="candidates.read",
            display_name="Read recorded candidates",
            resource_type="administrative_function",
            action="read_improvement",
        ),
    )
    scope = dict(
        permission_id=permission.id,
        resource_type="administrative_function",
        resource_id="improvement_backlog",
    )
    app.state.identity_service.assign_permission(
        actor.actor_id, AssignPermissionRequest(effect="allow", **scope)
    )
    return app, actor, service, analysis, request, scope


def counts(app):
    with app.state.repository.session_factory() as session:
        return tuple(
            session.scalar(select(func.count()).select_from(model))
            for model in (ImprovementRecordRow, TaskRow, AuditEventRow, OutboxEventRow)
        )


def save(app, **kwargs):
    return ImprovementRepository(app.state.repository.session_factory).save_analysis(
        analyze(baseline(**kwargs))
    )


def test_http_order_pagination_provenance_and_no_admission(candidates):
    app, actor, _, medium, _, _ = candidates
    critical = save(app, hard=True, metric="role_gate")
    before = counts(app)
    with TestClient(app) as client:
        headers = {"X-Jarvis-Actor-Id": actor.actor_id}
        response = client.get("/api/self-improvement/candidates", headers=headers)
        assert response.status_code == 200
        assert response.json()["meta"]["schemaVersion"] == "1.0"
        items = [ImprovementCandidate.model_validate(item) for item in response.json()["data"]]
        assert [item.baseline_id for item in items] == [critical.baseline.id, medium.baseline.id]
        assert items[0].priority == "critical" and items[0].work_kind == "prepare_experiment"
        assert items[0].evidence_ids == critical.proposals[0].evidence_ids
        assert items[0].experiment_digest is not None
        for item in response.json()["data"]:
            assert (
                not {"task_id", "task_status", "selected_by", "selected_at", "entry"} & item.keys()
            )
        for offset in range(3):
            page = client.get(
                f"/api/self-improvement/candidates?offset={offset}&limit=1", headers=headers
            )
            assert page.json()["data"] == response.json()["data"][offset : offset + 1]
        schema = client.get("/openapi.json").json()
        assert "ImprovementCandidate" in schema["components"]["schemas"]
    assert counts(app) == before


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1", "offset=100001"])
def test_http_bounds_are_enforced(candidates, query):
    app, actor, *_ = candidates
    with TestClient(app) as client:
        assert (
            client.get(
                f"/api/self-improvement/candidates?{query}",
                headers={"X-Jarvis-Actor-Id": actor.actor_id},
            ).status_code
            == 422
        )


def test_read_denial_precedes_candidate_scan(candidates, monkeypatch):
    app, actor, _, _, _, scope = candidates
    app.state.identity_service.assign_permission(
        actor.actor_id, AssignPermissionRequest(effect="deny", **scope)
    )

    def forbidden(*args, **kwargs):
        pytest.fail("denied actor reached discovery")

    monkeypatch.setattr(CandidateRepository, "get_candidates", forbidden)
    with TestClient(app) as client:
        response = client.get(
            "/api/self-improvement/candidates", headers={"X-Jarvis-Actor-Id": actor.actor_id}
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "IMPROVEMENT_BACKLOG_PERMISSION_DENIED"


def test_nonlocal_and_missing_actor_cannot_discover(candidates):
    app, actor, *_ = candidates
    with TestClient(app, client=("192.0.2.1", 50000)) as client:
        response = client.get(
            "/api/self-improvement/candidates", headers={"X-Jarvis-Actor-Id": actor.actor_id}
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "LOCAL_CONTROL_PLANE_ONLY"


def test_missing_actor_cannot_discover(candidates):
    app, *_ = candidates
    with TestClient(app) as client:
        assert client.get("/api/self-improvement/candidates").status_code == 401


def test_semantic_duplicates_suppressed_before_pagination_with_native_tie_breaks(candidates):
    app, actor, service, original, *_ = candidates
    # Distinct immutable captures, same measured scope, equal priority/confidence.
    newer = baseline(provenance=source(source_id="new-capture"))
    newer = newer.model_copy(
        update={"created_at": original.baseline.created_at + timedelta(days=1)}
    )
    latest = ImprovementRepository(app.state.repository.session_factory).save_analysis(
        analyze(newer)
    )
    assert latest.baseline.id != original.baseline.id
    items = service.list_candidates(actor)
    assert len(items) == 1 and items[0].baseline_id == latest.baseline.id
    assert service.list_candidates(actor, offset=1) == []


@pytest.mark.asyncio
async def test_admission_and_active_scope_suppress_new_captures(candidates):
    app, actor, service, original, request, _ = candidates
    admitted = await service.select(actor, request, "admit-discovered")
    assert admitted.outcome == "selected"
    assert service.list_candidates(actor) == []
    other = save(app, provenance=source(source_id="new-capture"))
    assert other.baseline.id != original.baseline.id
    assert service.list_candidates(actor) == []
    # Completing work releases semantic scope; the originally admitted proposal
    # must remain excluded by proposal ID, not by its different entry ID.
    with app.state.repository.session_factory.begin() as session:
        session.get(TaskRow, admitted.entry.task_id).status = "completed"
    items = service.list_candidates(actor)
    assert len(items) == 1 and items[0].baseline_id == other.baseline.id


@pytest.mark.asyncio
async def test_corrupt_active_admission_fails_closed(candidates):
    app, actor, service, _, request, _ = candidates
    admitted = await service.select(actor, request, "corrupt-discovery")
    with app.state.repository.session_factory.begin() as session:
        row = session.get(ImprovementRecordRow, admitted.entry.id)
        row.payload = {**row.payload, "task_id": "task-missing"}
    with pytest.raises(DomainError) as error:
        service.list_candidates(actor)
    assert error.value.code == "IMPROVEMENT_BACKLOG_TASK_MISSING"


def test_empty_healthy_and_bounded_latest_eight_analysis_window(candidates):
    app, actor, service, original, *_ = candidates
    with app.state.repository.session_factory.begin() as session:
        session.delete(session.get(ImprovementRecordRow, original.baseline.id))
    assert service.list_candidates(actor) == []
    # Nine distinct scopes; oldest persisted analysis is outside the advertised window.
    saved = [save(app, model=f"model-{index}") for index in range(9)]
    with app.state.repository.session_factory.begin() as session:
        for index, analysis in enumerate(saved):
            session.get(ImprovementRecordRow, analysis.baseline.id).created_at = NOW + timedelta(
                seconds=index
            )
    items = service.list_candidates(actor)
    assert len(items) == 8
    assert saved[0].baseline.id not in {item.baseline_id for item in items}
    assert {item.baseline_id for item in items} == {item.baseline.id for item in saved[1:]}
    # A valid healthy analysis contributes no fabricated candidate.
    healthy = save(app, values=(1, 1), model="healthy")
    assert healthy.baseline.id not in {item.baseline_id for item in service.list_candidates(actor)}


def test_read_snapshot_does_not_mix_a_concurrent_capture(candidates):
    app, actor, service, original, *_ = candidates
    inserted = []

    def after_read(connection, cursor, statement, parameters, context, executemany):
        if "FROM self_improvement_records" in statement and not inserted:
            inserted.append(True)
            save(app, hard=True, metric="role_gate")

    event.listen(app.state.engine, "after_cursor_execute", after_read)
    try:
        items = service.list_candidates(actor)
    finally:
        event.remove(app.state.engine, "after_cursor_execute", after_read)
    assert [item.baseline_id for item in items] == [original.baseline.id]
    assert len(service.list_candidates(actor)) == 2


def test_unsupported_database_refuses_inconsistent_snapshot(candidates, monkeypatch):
    app, *_ = candidates
    monkeypatch.setattr(app.state.engine.dialect, "name", "postgresql")
    with pytest.raises(DomainError) as error:
        CandidateRepository(app.state.repository.session_factory).get_candidates()
    assert error.value.code == "IMPROVEMENT_BACKLOG_DATABASE_UNSUPPORTED"
