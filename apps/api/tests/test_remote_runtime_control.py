"""Remote requests reuse the production runtime ledger and live native RBAC."""

import pytest

from app.agent_runtime.errors import RuntimeActorMismatchError, RuntimePermissionDeniedError
from app.core.errors import DomainError
from app.models.agent_runtime import (
    ConfirmPauseCommand,
    CreateAgentRunCommand,
    QueueAgentRunCommand,
    RequestCancellationCommand,
    RequestPauseCommand,
    ResumeAgentRunCommand,
)
from app.models.identity import AssignPermissionRequest, CreatePermissionRequest
from tests.agent_runtime_testkit import make_spec, ts
from tests.test_remote_goal_submission import configured_service, grant_control

pytest_plugins = ["tests.test_remote_control_access"]


def queued_remote_run(remote_access):
    app, actor, remote, _ = configured_service(remote_access)
    control_scope = grant_control(app, actor)
    for index, key in enumerate(
        ["runtime.create", "runtime.queue", "runtime.pause", "runtime.cancel", "runtime.read"]
    ):
        permission = app.state.identity_service.create_definition(
            "permission",
            CreatePermissionRequest(
                stable_key=key,
                display_name=key,
                resource_type="task",
                action=f"remote_fixture_{index}",
            ),
        )
        app.state.identity_service.assign_permission(
            actor.actor_id,
            AssignPermissionRequest(
                permission_id=permission.id,
                effect="allow",
                resource_type="task",
                resource_id="task-remote-runtime",
            ),
        )
    runtime = app.state.agent_runtime_service
    runtime.handle_authorized(
        CreateAgentRunCommand(
            specification=make_spec(task_id="task-remote-runtime", run_id="run-remote-runtime"),
            command_id="remote-fixture-create",
            expected_run_version=0,
            timestamp=ts(0),
        ),
        actor,
    )
    runtime.handle_authorized(
        QueueAgentRunCommand(
            run_id="run-remote-runtime",
            command_id="remote-fixture-queue",
            expected_run_version=1,
            timestamp=ts(1),
        ),
        actor,
    )
    return app, actor, remote, control_scope


def pause_request():
    return RequestPauseCommand(
        run_id="run-remote-runtime",
        command_id="remote-pause",
        expected_run_version=2,
        timestamp=ts(2),
        reason_code="operator_request",
        detail="Pause requested by authenticated operator",
    )


def test_remote_pause_resume_cancel_reuses_native_ledger(remote_access):
    app, actor, remote, _ = queued_remote_run(remote_access)
    paused = remote.runtime_command(actor, pause_request())
    assert paused.snapshot.state.value == "pause_requested"
    assert remote.runtime_command(actor, pause_request()).idempotent_replay
    # Confirmation belongs to the native worker, never the remote request surface.
    app.state.agent_runtime_service.handle_authorized(
        ConfirmPauseCommand(
            run_id="run-remote-runtime",
            command_id="worker-confirm-pause",
            expected_run_version=3,
            timestamp=ts(3),
        ),
        actor,
    )
    resumed = remote.runtime_command(
        actor,
        ResumeAgentRunCommand(
            run_id="run-remote-runtime",
            command_id="remote-resume",
            expected_run_version=4,
            timestamp=ts(4),
        ),
    )
    assert resumed.snapshot.state.value == "queued"
    cancelled = remote.runtime_command(
        actor,
        RequestCancellationCommand(
            run_id="run-remote-runtime",
            command_id="remote-cancel",
            expected_run_version=5,
            timestamp=ts(5),
            reason_code="operator_request",
            detail="Cancel requested by authenticated operator",
            requester_reference=actor.actor_id,
        ),
    )
    assert cancelled.snapshot.state.value == "cancelled"
    assert cancelled.events[0].actor_reference == actor.actor_id
    with pytest.raises(ValueError, match="worker or completion"):
        remote.runtime_command(
            actor,
            ConfirmPauseCommand(
                run_id="run-remote-runtime",
                command_id="forged-worker-confirm",
                expected_run_version=6,
                timestamp=ts(6),
            ),
        )


@pytest.mark.parametrize("scope", ["remote", "native"])
def test_remote_runtime_commit_rechecks_revocation(remote_access, monkeypatch, scope):
    app, actor, remote, control_scope = queued_remote_run(remote_access)
    if scope == "native":
        permission = next(
            item
            for item in app.state.identity_service.list_definitions("permission", 0, 100)
            if item.stable_key == "runtime.pause"
        )
        control_scope = dict(
            permission_id=permission.id,
            resource_type="task",
            resource_id="task-remote-runtime",
        )
    original = remote.runtime_repository.commit_command

    def revoke_before_commit(*args, **kwargs):
        app.state.identity_service.assign_permission(
            actor.actor_id, AssignPermissionRequest(effect="deny", **control_scope)
        )
        return original(*args, **kwargs)

    monkeypatch.setattr(remote.runtime_repository, "commit_command", revoke_before_commit)
    with pytest.raises((DomainError, RuntimePermissionDeniedError)):
        remote.runtime_command(actor, pause_request())
    snapshot = remote.runtime_repository.load_run("run-remote-runtime")
    assert snapshot.version == 2 and snapshot.state.value == "queued"
    assert remote.runtime_repository._commit_authorizer.get() is None


def test_remote_cancellation_rejects_spoofed_requester(remote_access):
    _, actor, remote, _ = queued_remote_run(remote_access)
    with pytest.raises(RuntimeActorMismatchError):
        remote.runtime_command(
            actor,
            RequestCancellationCommand(
                run_id="run-remote-runtime",
                command_id="spoofed-remote-cancel",
                expected_run_version=2,
                timestamp=ts(2),
                reason_code="operator_request",
                detail="Spoofed requester",
                requester_reference="someone-else",
            ),
        )
