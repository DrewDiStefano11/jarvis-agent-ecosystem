"""Task-scoped publication authority; all external effects require genuine B evidence."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

from app.agent_runtime.authorization import RuntimeActorContext
from app.core.errors import DomainError
from app.db.models import AuditEventRow, TaskRow
from app.github_publication.validation_consumer import require_guard
from app.models import publication_lifecycle as contracts
from app.models.publication_lifecycle import PublicationPlan
from app.self_build.policy import digest

APPROVED = "github.publication.approved"


def fail(code, status=409):
    raise DomainError(
        code, "Publication is waiting for required protected authority or evidence.", status
    )


class PublicationService:
    def __init__(
        self,
        native,
        *,
        target,
        publisher_ids,
        validation,
        broker=None,
        hosted_ci_guard=None,
        configuration_guard=None,
    ):
        self.native, self.target = native, target
        self.publisher_ids = tuple(publisher_ids)
        if (
            not self.publisher_ids
            or len(self.publisher_ids) > 256
            or len(set(self.publisher_ids)) != len(self.publisher_ids)
            or any(type(value) is not int or value <= 0 for value in self.publisher_ids)
        ):
            fail("GITHUB_PUBLISHER_AUTHORITY_REQUIRED", 403)
        self.validation, self.broker, self.hosted_ci_guard = validation, broker, hosted_ci_guard
        self.configuration_guard = configuration_guard
        self.policy_digest = sha256(
            (
                "".join(
                    path.name + ":" + path.read_text(encoding="utf-8")
                    for path in sorted(Path(__file__).parent.glob("*.py"))
                )
                + Path(contracts.__file__).read_text(encoding="utf-8")
                + repr(target)
                + repr(self.publisher_ids)
            ).encode()
        ).hexdigest()

    def permission(self, actor, row, session):
        decision = self.native.preparation.workspace.identity.check_permission_resource_access(
            actor.actor_id, "self_build.github.publish", "task", row.task_id, session=session
        )
        if not decision.allowed:
            fail("GITHUB_PUBLICATION_PERMISSION_DENIED", 403)

    def configured(self, plan):
        if self.configuration_guard is not None:
            require_guard(self.configuration_guard, "GITHUB_PUBLICATION_PROFILE_CHANGED")
        self.target.branch(plan.branch)
        if (
            plan.repository_id != self.target.repository_id
            or plan.repository_identity.casefold()
            != f"github.com/{self.target.owner}/{self.target.name}".casefold()
            or plan.base_branch != self.target.base_branch
            or plan.publisher_ids != self.publisher_ids
            or plan.implementation_policy_digest != self.policy_digest
        ):
            fail("GITHUB_PUBLICATION_POLICY_CHANGED")

    def preview(self, actor, workspace_id, native_command_id, validation_journal_id):
        from app.self_build.commit_journal import NativeCommitJournal

        history = NativeCommitJournal(self.native).history(actor, workspace_id, native_command_id)
        record = history.current
        proposal = record.plan.preparation.plan.proposal
        if record.state != "checkpointed":
            fail("GITHUB_NATIVE_COMMIT_PENDING")
        if self.validation.service is None:
            fail("GITHUB_VALIDATION_ISOLATION_UNAVAILABLE")
        checked = self.validation.service.read(actor, workspace_id, validation_journal_id)
        execution = checked.intent.plan
        with self.native.repository.sessions() as session:
            row, attempt, *_ = self.native.preparation.live(actor, workspace_id, session)
            self.permission(actor, row, session)
            task = session.get(TaskRow, row.task_id)
            if task is None:
                fail("GITHUB_PUBLICATION_TASK_NOT_FOUND")
            payload = dict(
                workspace_id=workspace_id,
                task_id=row.task_id,
                task_title=task.title,
                task_summary=task.description[:3000],
                actor_id=row.actor_id,
                runtime_run_id=row.runtime_run_id,
                attempt_id=attempt,
                native_command_id=native_command_id,
                native_plan_hash=record.plan.plan_hash,
                repository_id=self.target.repository_id,
                repository_identity=proposal.repository_identity,
                branch=proposal.branch,
                base_branch=self.target.base_branch,
                approved_base_sha=history.source_snapshot.original_base_sha,
                source_base_sha=history.source_snapshot.original_base_sha,
                parent_sha=proposal.parent_sha,
                commit_sha=proposal.prospective_commit_sha,
                expected_remote_sha=None,
                source_snapshot_digest=record.plan.snapshot_digest,
                validation_journal_id=validation_journal_id,
                validation_execution_plan_hash=execution.execution_plan_hash,
                validation_code_state_hash=execution.validation.code_state_hash,
                validation_journal_digest=checked.digest,
                publisher_ids=list(self.publisher_ids),
                implementation_policy_digest=self.policy_digest,
            )
        plan = PublicationPlan(**payload, plan_hash=digest(payload))
        self.configured(plan)
        # Preview is planning only. It does not assert that receipts passed.
        return plan

    def approve(self, operator, plan, *, expected_plan_hash, valid_for_seconds=900):
        self.configured(plan)
        if (
            expected_plan_hash != plan.plan_hash
            or type(valid_for_seconds) is not int
            or not 1 <= valid_for_seconds <= 3600
        ):
            fail("GITHUB_PUBLICATION_PLAN_CHANGED")
        from app.self_build.commit_recovery_read import inspect_recovery

        def persist(preview, history, program, guard):
            if (
                history.current.state != "checkpointed"
                or history.current.plan.plan_hash != plan.native_plan_hash
                or program.commit_sha != plan.commit_sha
                or self.preview(
                    operator, plan.workspace_id, plan.native_command_id, plan.validation_journal_id
                )
                != plan
            ):
                fail("GITHUB_PUBLICATION_PLAN_CHANGED")
            return self._persist_approval(operator, plan, valid_for_seconds, guard)

        return inspect_recovery(
            self.native, operator, plan.workspace_id, plan.native_command_id, finalize=persist
        )

    def _persist_approval(self, operator, plan, valid_for_seconds, guard):
        with self.native.repository.leases._write() as session:
            self.configured(plan)
            row, attempt, *_ = self.native.preparation.live(operator, plan.workspace_id, session)
            self.native.preparation.operator(operator, row, session)
            self.permission(operator, row, session)
            if (
                attempt != plan.attempt_id
                or row.task_id != plan.task_id
                or row.actor_id != plan.actor_id
            ):
                fail("GITHUB_PUBLICATION_OWNER_CHANGED")
            expiry = datetime.now(UTC) + timedelta(seconds=valid_for_seconds)
            guard(check_authority=False)
            event = self.native.preparation.git.event(
                session,
                row,
                operator,
                APPROVED,
                dict(plan=plan.model_dump(mode="json"), expiresAt=expiry.isoformat()),
            )
            result = dict(
                approval_id=event.id, approved_by=operator.actor_id, expires_at=expiry, plan=plan
            )
            guard(check_authority=False)
            self.configured(plan)
        self.native.repository.leases.repository.refresh_event_cursor()
        return result

    def live(self, actor, request, plan, session):
        if self.configuration_guard is not None:
            require_guard(self.configuration_guard, "GITHUB_PUBLICATION_PROFILE_CHANGED")
        self.configured(plan)
        row, attempt, *_ = self.native.preparation.live(
            actor, plan.workspace_id, session, request=request
        )
        self.permission(actor, row, session)
        if (
            row.task_id != plan.task_id
            or row.actor_id != plan.actor_id
            or row.runtime_run_id != plan.runtime_run_id
            or attempt != plan.attempt_id
            or request.expected_plan_hash != plan.plan_hash
        ):
            fail("GITHUB_PUBLICATION_OWNER_CHANGED")
        event = session.get(AuditEventRow, request.approval_id)
        try:
            payload = event.payload["payload"]
            expiry = datetime.fromisoformat(payload["expiresAt"])
            valid = (
                event.event_type == APPROVED
                and event.task_id == row.task_id
                and event.actor not in {row.actor_id, actor.actor_id}
                and PublicationPlan.model_validate(payload["plan"]) == plan
                and expiry.tzinfo is not None
                and expiry > datetime.now(UTC)
            )
        except (AttributeError, KeyError, ValueError, TypeError):
            valid = False
        if not valid:
            fail("GITHUB_PUBLICATION_APPROVAL_REQUIRED", 403)
        operator = RuntimeActorContext(
            actor_id=event.actor, stable_key="persisted-publication-operator"
        )
        self.native.preparation.operator(operator, row, session)
        self.permission(operator, row, session)
        if self.configuration_guard is not None:
            require_guard(self.configuration_guard, "GITHUB_PUBLICATION_PROFILE_CHANGED")
        return row

    def require_effect(self, actor, request, plan):
        with self.native.repository.sessions() as session:
            self.live(actor, request, plan, session)
        self.validation.require(actor, plan)
        if not callable(self.hosted_ci_guard):
            fail("GITHUB_HOSTED_CI_COST_UNVERIFIED")
        require_guard(self.hosted_ci_guard, "GITHUB_HOSTED_CI_COST_UNVERIFIED")
        if self.broker is None:
            fail("GITHUB_CREDENTIAL_UNAVAILABLE", 503)
