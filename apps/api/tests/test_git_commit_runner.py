"""Native effect composition tests; a test adapter is never B acceptance evidence."""

import pytest

from app.agent_runtime.authorization import RuntimeActorContext
from app.core.errors import DomainError
from app.models.git_execution import RenewNativeCommitApproval
from app.self_build.commit_journal import NativeCommitJournal
from app.self_build.commit_runner import execute, require_validation

pytest_plugins = ["tests.test_git_commit_completion"]


def test_missing_validation_consumer_refuses_before_accessing_native_state():
    with pytest.raises(DomainError) as error:
        execute(object(), object(), "workspace", object(), None)
    assert error.value.code == "GIT_COMMIT_VALIDATION_UNAVAILABLE"


def test_bool_and_async_validation_callbacks_never_authorize_native_effects():
    class BoolGate:
        def require_commit(self, *args):
            return True

    class AsyncGate:
        async def require_commit(self, *args):
            pytest.fail("async authority must not execute")

    for gate in (BoolGate(), AsyncGate()):
        with pytest.raises(DomainError) as error:
            require_validation(gate, object(), "workspace", object())
        assert error.value.code == "GIT_COMMIT_VALIDATION_UNAVAILABLE"


@pytest.mark.timeout(600)  # Two real native executions plus recovery and fixture setup on Windows.
def test_index_ack_loss_resumes_composed_native_commit(context, monkeypatch):
    c = context
    calls = []

    class FixtureGate:
        # Tests the native composition only. This is not a receipt verifier or
        # an isolation boundary and must never be configured in production.
        def require_commit(self, actor, workspace_id, request):
            assert actor == c.actor
            assert workspace_id == c.record.workspace_id
            assert request.expected_plan_hash == c.record.plan.plan_hash
            calls.append(workspace_id)

    original = NativeCommitJournal.transition

    def interrupted(self, actor, request, expected, action, fs_guard, **claims):
        if action == "index_verified":
            raise RuntimeError("lost index acknowledgement")
        return original(self, actor, request, expected, action, fs_guard, **claims)

    gate = FixtureGate()
    with monkeypatch.context() as patch:
        patch.setattr(NativeCommitJournal, "transition", interrupted)
        with pytest.raises(RuntimeError, match="lost index acknowledgement"):
            c.native.execute(c.actor, c.record.workspace_id, c.request, validation_gate=gate)
    pending = c.journal.read(c.actor, c.record.workspace_id, c.record.command_id)
    assert pending.state == "index_prepared"
    recovered = c.native.recovery(c.actor, c.record.workspace_id, c.record.command_id)
    assert recovered.position.index == "exact_tree"
    operator = RuntimeActorContext(
        actor_id=c.app.state.remote_control_service.access.actor_id, stable_key="persisted-operator"
    )
    renewed = c.native.renew_approval(
        operator,
        RenewNativeCommitApproval(
            workspace_id=c.record.workspace_id,
            command_id=c.record.command_id,
            expected_plan_hash=c.record.plan.plan_hash,
            expected_recovery_hash=recovered.recovery_hash,
        ),
    )
    assert renewed.plan == c.record.plan
    current_request = c.request.model_copy(update={"approval_id": renewed.approval_id})
    finished = c.native.execute(
        c.actor, c.record.workspace_id, current_request, validation_gate=gate
    )
    assert finished.state == "checkpointed"
    assert finished.plan == c.record.plan
    assert finished.original_approval_id == c.record.original_approval_id
    assert calls


def test_recovery_renewal_cannot_authorize_a_different_command(context):
    c = context
    recovered = c.native.recovery(c.actor, c.record.workspace_id, c.record.command_id)
    operator = RuntimeActorContext(
        actor_id=c.app.state.remote_control_service.access.actor_id, stable_key="persisted-operator"
    )
    renewed = c.native.renew_approval(
        operator,
        RenewNativeCommitApproval(
            workspace_id=c.record.workspace_id,
            command_id=c.record.command_id,
            expected_plan_hash=c.record.plan.plan_hash,
            expected_recovery_hash=recovered.recovery_hash,
        ),
    )
    request = c.request.model_copy(update={"approval_id": renewed.approval_id})
    # The valid command can still recover its original acknowledged intent.
    assert c.journal.begin(c.actor, c.record.workspace_id, request) == c.record
    foreign = request.model_copy(update={"command_id": "unapproved-command-b"})
    with pytest.raises(DomainError) as error:
        c.journal.begin(c.actor, c.record.workspace_id, foreign)
    assert error.value.code == "GIT_COMMIT_EXECUTION_APPROVAL_REQUIRED"
    with pytest.raises(DomainError) as error:
        c.journal.read(c.actor, c.record.workspace_id, foreign.command_id)
    assert error.value.code == "GIT_COMMIT_EXECUTION_NOT_FOUND"
    assert c.journal.read(c.actor, c.record.workspace_id, request.command_id) == c.record
