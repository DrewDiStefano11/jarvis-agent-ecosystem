"""Real native source snapshots; model fixtures are not independent execution evidence."""

import httpx
import pytest
from sqlalchemy import select

from app.core.errors import DomainError
from app.db.models import AuditEventRow, SystemStateRow
from app.development_agent.baseline_repository import DevelopmentBaselineRow
from app.development_agent.baselines import DevelopmentBaselineService
from app.models.source_mutation import ApplySourceMutation, ApproveSourceMutation
from app.self_build.policy import digest
from tests.test_development_dispatch import admit
from tests.test_development_execution import executor, grant_editing, output

pytest_plugins = ["tests.test_development_agent"]


async def completed(development):
    app, actor, operator, authority, _, _, _, context = development
    admit(development)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=output(request))),
        trust_env=False,
    ) as client:
        engine = executor(app, client)
        iteration = await engine.execute(
            actor, context.workspace_id, authority, context.selection, "feature"
        )
        grant_editing(app, actor, operator)
        plan = engine.mutations.preview(actor, iteration.editing_record_id)
        approval = engine.mutations.approve(
            operator,
            ApproveSourceMutation(
                editing_record_id=iteration.editing_record_id,
                expected_plan_hash=plan.plan_hash,
            ),
        )
        request = ApplySourceMutation(
            editing_record_id=iteration.editing_record_id,
            expected_plan_hash=plan.plan_hash,
            approval_id=approval.approval_id,
            worker_id=authority.worker_id,
            lease_token=authority.lease_token,
        )
        engine.apply_approved(
            actor, context.workspace_id, authority, iteration.dispatch_id, request
        )
    return DevelopmentBaselineService(app), iteration, request


@pytest.mark.asyncio
async def test_native_baseline_bytes_checkpoint_crash_and_historical_replay(
    development, monkeypatch
):
    app, actor, _, authority, _, target, _, context = development
    service, iteration, request = await completed(development)
    args = (actor, context.workspace_id, authority, iteration.dispatch_id, request)
    handle = service.dispatches.runtime.handle_authorized

    def crash(*args, **kwargs):
        raise RuntimeError("simulated baseline checkpoint crash")

    monkeypatch.setattr(service.dispatches.runtime, "handle_authorized", crash)
    with pytest.raises(RuntimeError):
        service.capture(*args)
    monkeypatch.setattr(service.dispatches.runtime, "handle_authorized", handle)

    def forbidden(*args, **kwargs):
        pytest.fail("Historical recovery must not remeasure or recall a provider.")

    monkeypatch.setattr(service.reader, "inspect", forbidden)
    monkeypatch.setattr(service.inspector, "verify_artifacts", forbidden)
    record = service.capture(*args)
    assert service.checkpoint(actor, record)[0] is not None
    assert service.capture(*args) == record
    files = {item.change.path: item for item in record.snapshot.files}
    assert files["source.py"].content.text == "value = 2\n"
    assert files["new.py"].change.before_hash is None
    assert files["new.py"].content.text.startswith("from source import value")
    assert not any(
        ".jarvis-edit-" in entry.path for entry in record.snapshot.context.observation.entries
    )
    summary = service.summary(record)
    assert not summary.current_source_measured and not summary.validation_passed
    assert authority.lease_token not in summary.model_dump_json()
    from fastapi.testclient import TestClient

    path = f"/api/development/workspaces/{context.workspace_id}/iterations/{iteration.dispatch_id}/baseline"
    with TestClient(app, base_url="https://testserver") as client:
        assert (
            path.replace(context.workspace_id, "{workspace_id}").replace(
                iteration.dispatch_id, "{dispatch_id}"
            )
            in app.openapi()["paths"]
        )
        body = dict(
            authority=authority.model_dump(mode="json"), mutation=request.model_dump(mode="json")
        )
        assert client.post(path, json=body).status_code == 401
        result = client.post(path, json=body, headers={"X-Jarvis-Actor-Id": actor.actor_id})
        assert result.status_code == 200, result.text
        assert result.json()["data"] == summary.model_dump(mode="json")
        assert authority.lease_token not in result.text and "value =" not in result.text

    # Pure compiler contract only: a fixture destination is not actual new-workspace evidence.
    from hashlib import sha256

    from app.development_agent.corrections import compile_correction, correction_context_hash
    from app.models.development_agent import ContextSelection, DevelopmentProposal
    from app.models.source_change import PlanReferences

    destination = context.model_copy(
        update={"workspace_id": "fresh-workspace", "context_hash": "0" * 64}
    )
    destination = destination.model_copy(
        update={
            "context_hash": digest(destination.model_dump(mode="json", exclude={"context_hash"}))
        }
    )
    selection = ContextSelection(paths=("source.py",))
    hash_value = correction_context_hash(record.snapshot, destination, selection)
    proposal = DevelopmentProposal(
        context_hash=hash_value,
        summary="Repair configured value",
        completion_criteria=("The accessor returns the repaired value.",),
        files=(
            dict(
                operation="modify",
                path="source.py",
                before_digest=sha256(b"value = 2\n").hexdigest(),
                text="value = 3\n",
            ),
        ),
    )
    references = PlanReferences(original_plan_hash="a" * 64, authorization_reference="new-approval")
    net = compile_correction(proposal, record.snapshot, destination, selection, references)
    changes = {item.path: item for item in net.changes}
    assert changes["source.py"].before.digest == sha256(b"value = 1\n").hexdigest()
    assert changes["source.py"].after.text == "value = 3\n"
    assert (
        changes["new.py"].operation == "add"
        and changes["new.py"].after.text == files["new.py"].content.text
    )
    restored = proposal.model_copy(
        update={"files": (proposal.files[0].model_copy(update={"text": "value = 1\n"}),)}
    )
    only_preserved = compile_correction(
        restored, record.snapshot, destination, selection, references
    )
    assert [item.path for item in only_preserved.changes] == ["new.py"]
    with pytest.raises(DomainError):
        compile_correction(
            proposal.model_copy(update={"context_hash": "0" * 64}),
            record.snapshot,
            destination,
            selection,
            references,
        )

    (target / "source.py").write_bytes(b"drift after snapshot\n")
    assert service.capture(*args) == record  # Historical snapshot, no freshness claim.
    with app.state.repository.session_factory() as session:
        assert len(session.scalars(select(DevelopmentBaselineRow)).all()) == 1
        event = session.scalar(
            select(AuditEventRow).where(AuditEventRow.event_type == "development.baseline.captured")
        )
        assert "value =" not in str(event.payload) and authority.lease_token not in str(
            event.payload
        )
        state = session.get(SystemStateRow, 1)
        state.emergency_stop = True
        session.commit()
    with pytest.raises(DomainError):
        service.capture(*args)


@pytest.mark.asyncio
async def test_source_drift_and_replaced_backup_cannot_become_baseline(development):
    app, actor, _, authority, _, target, _, context = development
    service, iteration, request = await completed(development)
    args = (actor, context.workspace_id, authority, iteration.dispatch_id, request)
    (target / "source.py").write_bytes(b"value = 3\n")
    with pytest.raises(DomainError) as drift:
        service.capture(*args)
    assert drift.value.code == "DEVELOPMENT_SOURCE_STATE_CONFLICT"
    (target / "source.py").write_bytes(b"value = 2\n")
    backup = next(target.glob(".jarvis-edit-*.before"))
    replacement = target / "replacement.tmp"
    replacement.write_bytes(backup.read_bytes())
    import os

    os.replace(replacement, backup)
    with pytest.raises(DomainError):
        service.capture(*args)
    with app.state.repository.session_factory() as session:
        assert not session.scalars(select(DevelopmentBaselineRow)).all()


@pytest.mark.asyncio
async def test_coherently_rehashed_baseline_cannot_replace_its_audit(development):
    app, actor, _, authority, _, _, _, context = development
    service, iteration, request = await completed(development)
    args = (actor, context.workspace_id, authority, iteration.dispatch_id, request)
    record = service.capture(*args)
    from copy import deepcopy
    from datetime import timedelta

    with app.state.repository.session_factory() as session:
        row = session.get(DevelopmentBaselineRow, record.snapshot.baseline_id)
        payload = deepcopy(row.payload)
        snapshot = dict(payload["snapshot"])
        snapshot["captured_at"] = (
            (record.snapshot.captured_at + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
        )
        snapshot["native_verification"]["source_inspection"]["measured_at"] = snapshot[
            "captured_at"
        ]
        snapshot["snapshot_hash"] = digest(
            {key: value for key, value in snapshot.items() if key != "snapshot_hash"}
        )
        payload["snapshot"] = snapshot
        row.payload = payload
        row.integrity_digest = digest(payload)
        session.commit()
    with pytest.raises(DomainError):
        service.capture(*args)


def test_populated_baseline_migration_refuses_loss_of_snapshot_evidence(tmp_path):
    from datetime import UTC, datetime

    from alembic import command
    from sqlalchemy import create_engine, text

    from tests.test_development_request import migration_config

    config = migration_config(tmp_path / "baseline-migration.db")
    command.upgrade(config, "head")
    engine = create_engine(config.get_main_option("sqlalchemy.url"))
    try:
        with engine.begin() as connection:
            # Placeholder projections test migration retention only; not admissible source evidence.
            connection.execute(
                text(
                    "INSERT INTO tasks (id,title,description,original_request,creator,priority,status,progress,status_message,retry_count,maximum_retries,schema_version,payload,created_at,updated_at) VALUES ('task-migration','Migration','','','operator','medium','queued',0,'',0,2,'1.0','{}',:now,:now)"
                ),
                {"now": datetime.now(UTC)},
            )
            connection.execute(
                text(
                    "INSERT INTO development_admissions (task_id,payload,integrity_digest) VALUES ('task-migration','{}',:hash)"
                ),
                {"hash": "a" * 64},
            )
            connection.execute(
                text(
                    "INSERT INTO development_dispatches (id,task_id,ordinal,payload,integrity_digest) VALUES (:id,'task-migration',1,'{}',:hash)"
                ),
                {"id": "b" * 64, "hash": "c" * 64},
            )
            connection.execute(
                text(
                    "INSERT INTO development_baselines (id,dispatch_id,task_id,payload,integrity_digest) VALUES (:id,:dispatch,'task-migration','{}',:hash)"
                ),
                {"id": "d" * 64, "dispatch": "b" * 64, "hash": "e" * 64},
            )
        with pytest.raises(RuntimeError, match="Export development baseline recovery"):
            command.downgrade(config, "20261013_development_dispatch")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT COUNT(*) FROM development_baselines")) == 1
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "20261013_development_baselines"
            )
    finally:
        engine.dispose()
