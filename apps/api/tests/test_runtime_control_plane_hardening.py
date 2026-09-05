from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.agent_runtime.errors import RuntimePermissionDeniedError
from app.autonomous_worker.__main__ import _run_once_resilient
from app.db.models import ModelExecutionRow
from tests.test_autonomous_worker import VALID_RESULT, FakeRouter, worker_fixture


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["lease_cancelled", "emergency_stop"])
async def test_checkpoint_commit_revalidates_live_execution_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    run_id = f"run-checkpoint-boundary-{boundary}"
    router = FakeRouter([json.dumps(VALID_RESULT)])
    app, client, _, worker = worker_fixture(tmp_path, router=router, run_id=run_id)
    service = app.state.autonomous_worker_service
    original_checkpoint = service._checkpoint
    boundary_changed = False

    def change_boundary_before_commit(snapshot, actor, execution, attempt_id, name):
        nonlocal boundary_changed
        if not boundary_changed:
            boundary_changed = True
            if boundary == "lease_cancelled":
                app.state.task_leases.cancel_task(execution.taskId)
            else:
                app.state.repository.emergency_stop = True
                app.state.repository.persist()
        return original_checkpoint(snapshot, actor, execution, attempt_id, name)

    monkeypatch.setattr(service, "_checkpoint", change_boundary_before_commit)
    try:
        assert await _run_once_resilient(service, worker.id) is None
        assert boundary_changed is True
        assert router.requests == []
        assert app.state.agent_runtime_service.repository.list_checkpoints(run_id) == []
    finally:
        client.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_target_suspension_immediately_before_result_write_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "run-target-suspended-before-persist"
    router = FakeRouter([json.dumps(VALID_RESULT)])
    app, client, _, worker = worker_fixture(tmp_path, router=router, run_id=run_id)
    service = app.state.autonomous_worker_service
    repository = app.state.model_execution_repository
    runtime = app.state.agent_runtime_service.repository.load_run(run_id)
    assert runtime is not None
    original_persist_result = repository.persist_result

    def suspend_target_then_persist(*args, **kwargs):
        app.state.identity_service.transition(runtime.specification.agent_id, "suspended")
        return original_persist_result(*args, **kwargs)

    monkeypatch.setattr(repository, "persist_result", suspend_target_then_persist)
    try:
        with pytest.raises(RuntimePermissionDeniedError):
            await service.run_once(worker.id)
        stored = repository.get_by_run(run_id)
        assert stored is not None
        assert stored.result is None
        assert stored.resultHash is None
    finally:
        client.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_corrupt_recovery_row_does_not_starve_later_valid_candidate(tmp_path: Path) -> None:
    run_id = "run-corrupt-recovery-page"
    router = FakeRouter([json.dumps(VALID_RESULT)])
    app, client, _, worker = worker_fixture(tmp_path, router=router, run_id=run_id)
    repository = app.state.model_execution_repository
    try:
        completed = await app.state.autonomous_worker_service.run_once(worker.id)
        assert completed is not None
        valid_execution_id = "exec-valid-recovery-candidate"
        now = datetime.now(UTC)
        with repository.sessions.begin() as session:
            row = session.get(ModelExecutionRow, completed.executionId)
            assert row is not None
            valid_values = {
                column.name: getattr(row, column.name)
                for column in ModelExecutionRow.__table__.columns
            }
            valid_values.update(
                {
                    "execution_id": valid_execution_id,
                    "runtime_attempt_id": f"{row.runtime_attempt_id}-valid",
                    "stage": "result_persisted",
                    "updated_at": now + timedelta(seconds=1),
                    "completed_at": None,
                    "failure_code": None,
                }
            )
            session.add(ModelExecutionRow(**valid_values))
            row.stage = "result_persisted"
            row.result_json = VALID_RESULT | {"summary": "Corrupt persisted result"}
            row.updated_at = now
            row.completed_at = None

        recoverable = [
            execution
            for page in repository.iter_recoverable_result_pages(skip_corrupt=True)
            for execution in page
        ]
        assert [execution.executionId for execution in recoverable] == [valid_execution_id]
        status = repository.status(
            enabled=True,
            execution_mode="local_only",
            provider_ready=True,
        )
        assert status.status == "degraded"
        assert status.reasonCode == "model_result_corrupt"
    finally:
        client.__exit__(None, None, None)
