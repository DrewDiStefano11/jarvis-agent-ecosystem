"""Durable bounded proposals on native runtime/workspace authorization and outbox."""

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.core.errors import DomainError
from app.development_validation.planner import digest
from app.models import source_edit_planning as planning_contracts
from app.models.development_validation import CodeState
from app.models.source_change import SourceChangeIntent, SourceObservation
from app.models.source_edit_planning import (
    EditingPlan,
    EditingPlanRecord,
    PersistEditingPlanRequest,
)
from app.repositories.task_leases import _utc
from app.source_change.planner import plan_source_changes, verify_source_plan
from app.source_edit_planning import projection
from app.source_edit_planning import repository as planning_repository
from app.source_edit_planning.projection import project_source_state
from app.source_edit_planning.repository import SourceEditingPlanRow

MAX_PLANS_PER_WORKSPACE = 32
MAX_RECORD_BYTES = 32 * 1024 * 1024
EDITING_POLICY_DIGEST = sha256(
    b"".join(
        Path(path).read_text(encoding="utf-8").encode()
        for path in (
            __file__,
            planning_contracts.__file__,
            projection.__file__,
            planning_repository.__file__,
        )
    )
).hexdigest()


def refuse(code):
    raise DomainError(code, "Source editing plan failed a required integrity check.", 409) from None


class SourceEditingPlanService:
    def __init__(self, app):
        self.workspace = app.state.self_build_workspace_service
        self.repository = self.workspace.repository

    @staticmethod
    def record(row):
        try:
            result = EditingPlanRecord.model_validate(row.payload)
            expected_id = digest(["source-edit-plan-v1", result.actor_id, result.command_id])
            original_request = PersistEditingPlanRequest(
                workspace_id=result.plan.workspace_id,
                expected_workspace_version=result.plan.workspace_version,
                expected_workspace_plan_hash=result.plan.workspace_plan_hash,
                command_id=result.command_id,
                source_intent=SourceChangeIntent.model_validate(
                    result.plan.source_plan.model_dump(
                        mode="json",
                        include={
                            "schema_version",
                            "source",
                            "references",
                            "changes",
                        },
                    )
                ),
            )
            expected_request_hash = digest(
                {
                    "request": original_request.model_dump(mode="json"),
                    "source_plan_hash": result.plan.source_plan.plan_hash,
                    "original_code_state": result.plan.original_code_state.model_dump(mode="json"),
                }
            )
            if (
                result.record_id != expected_id
                or result.record_id != row.id
                or result.actor_id != row.actor_id
                or result.command_id != row.command_id
                or result.plan.workspace_id != row.workspace_id
                or result.plan.runtime_run_id != row.runtime_run_id
                or result.plan.task_id != row.task_id
                or result.request_hash != row.request_hash
                or result.request_hash != expected_request_hash
                or result.plan.plan_hash != row.plan_hash
                or result.plan.editing_policy_digest != EDITING_POLICY_DIGEST
                or result.created_at != _utc(row.created_at)
                or digest(result.plan.model_dump(mode="json", exclude={"plan_hash"}))
                != result.plan.plan_hash
            ):
                raise ValueError("inconsistent source plan record")
            verify_source_plan(result.plan.source_plan, result.plan.observation)
            expected_state, validation = project_source_state(
                result.plan.source_plan,
                result.plan.original_code_state,
            )
            if (
                expected_state != result.plan.expected_code_state
                or validation != result.plan.validation_plan
            ):
                raise ValueError("inconsistent expected state")
            return result
        except (ValueError, TypeError, AttributeError, DomainError):
            refuse("SOURCE_EDIT_RECORD_INVALID")

    def persist(self, actor, request, observation, original_code_state, *, commit_guard=None):
        try:
            return self._persist(
                actor, request, observation, original_code_state, commit_guard=commit_guard
            )
        except SQLAlchemyError:
            # Database exceptions can contain bound source payloads in their SQL
            # parameters. Never pass those exceptions or chains to public/log output.
            refuse("SOURCE_EDIT_STORAGE_FAILED")

    def _persist(self, actor, request, observation, original_code_state, *, commit_guard=None):
        # Internal trusted observation/state arguments. No model/API importer exists.
        try:
            request = PersistEditingPlanRequest.model_validate(
                request.model_dump(mode="json", warnings=False)
            )
            observation = SourceObservation.model_validate(
                observation.model_dump(mode="json", warnings=False)
            )
            original_code_state = CodeState.model_validate(
                original_code_state.model_dump(mode="json", warnings=False)
            )
        except (ValueError, TypeError, AttributeError, DomainError):
            refuse("SOURCE_EDIT_REQUEST_INVALID")
        with self.repository.leases._write() as session:
            workspace_row = self.repository.lookup(session, request.workspace_id)
            self.workspace.authorize(actor, workspace_row.task_id, session, write=True)
            reservation = self.repository.contract(workspace_row)
            run = self.repository.runtime(session, workspace_row.runtime_run_id)
            policy = self.workspace.policies.get(workspace_row.repository_id)
            if workspace_row.state != "reserved" or self.repository.reason(
                session, workspace_row, digest(policy.model_dump())
            ):
                refuse("SOURCE_EDIT_OWNER_INACTIVE")
            if (
                request.expected_workspace_version != workspace_row.version
                or request.expected_workspace_plan_hash != reservation.plan.plan_hash
                or request.source_intent.references.original_plan_hash != reservation.plan.plan_hash
                or request.source_intent.references.authorization_reference
                != workspace_row.approval_id
            ):
                refuse("SOURCE_EDIT_PROVENANCE_CONFLICT")
            if (
                request.source_intent.source.repository_identity
                != workspace_row.repository_identity
                or request.source_intent.source.base_sha != reservation.plan.base_sha
            ):
                refuse("SOURCE_EDIT_SOURCE_CONFLICT")
            source_plan = plan_source_changes(request.source_intent, observation)
            expected_state, validation = project_source_state(source_plan, original_code_state)
            request_hash = digest(
                {
                    "request": request.model_dump(mode="json"),
                    "source_plan_hash": source_plan.plan_hash,
                    "original_code_state": original_code_state.model_dump(mode="json"),
                }
            )
            record_id = digest(["source-edit-plan-v1", actor.actor_id, request.command_id])
            existing = session.get(SourceEditingPlanRow, record_id)
            if existing is not None:
                result = self.record(existing)
                if result.request_hash != request_hash:
                    refuse("SOURCE_EDIT_COMMAND_CONFLICT")
                if commit_guard is not None:
                    commit_guard(session)
                return result
            count = session.scalar(
                select(func.count())
                .select_from(SourceEditingPlanRow)
                .where(
                    SourceEditingPlanRow.workspace_id == workspace_row.id,
                )
            )
            if count >= MAX_PLANS_PER_WORKSPACE:
                refuse("SOURCE_EDIT_PLAN_LIMIT")
            payload = {
                "schema_version": "1.0",
                "editing_policy_digest": EDITING_POLICY_DIGEST,
                "workspace_id": workspace_row.id,
                "workspace_version": workspace_row.version,
                "workspace_plan_hash": reservation.plan.plan_hash,
                "task_id": workspace_row.task_id,
                "runtime_run_id": run.run_id,
                "runtime_version": run.version,
                "runtime_attempt_id": run.active_attempt_id,
                "source_plan": source_plan.model_dump(mode="json"),
                "observation": observation.model_dump(mode="json"),
                "original_code_state": original_code_state.model_dump(mode="json"),
                "expected_code_state": expected_state.model_dump(mode="json"),
                "validation_plan": validation.model_dump(mode="json"),
                "state": "proposed",
                "mutation_supported": False,
                "evidence_status": "unmeasured",
            }
            result = EditingPlanRecord(
                record_id=record_id,
                actor_id=actor.actor_id,
                command_id=request.command_id,
                request_hash=request_hash,
                plan=EditingPlan(**payload, plan_hash=digest(payload)),
                created_at=datetime.now(UTC),
            )
            if len(result.model_dump_json().encode("utf-8")) > MAX_RECORD_BYTES:
                refuse("SOURCE_EDIT_RECORD_LIMIT")
            session.add(
                SourceEditingPlanRow(
                    id=record_id,
                    task_id=workspace_row.task_id,
                    runtime_run_id=run.run_id,
                    workspace_id=workspace_row.id,
                    actor_id=actor.actor_id,
                    command_id=request.command_id,
                    request_hash=request_hash,
                    plan_hash=result.plan.plan_hash,
                    created_at=result.created_at,
                    payload=result.model_dump(mode="json"),
                )
            )
            self.repository.leases._add_event(
                session,
                "self_build.source_edit_plan.persisted",
                "Source edit proposal persisted",
                task_id=workspace_row.task_id,
                actor_identity_id=actor.actor_id,
                payload={
                    "recordId": record_id,
                    "planHash": result.plan.plan_hash,
                    "workspaceId": workspace_row.id,
                    "runtimeRunId": run.run_id,
                    "mutationSupported": False,
                    "evidenceStatus": "unmeasured",
                },
            )
            if commit_guard is not None:
                commit_guard(session)
        self.repository.leases.repository.refresh_event_cursor()
        return result

    def read(self, actor, record_id):
        try:
            return self._read(actor, record_id)
        except SQLAlchemyError:
            refuse("SOURCE_EDIT_STORAGE_FAILED")

    def _read(self, actor, record_id):
        with self.repository.sessions() as session:
            row = session.get(SourceEditingPlanRow, record_id)
            if row is None:
                raise DomainError(
                    "SOURCE_EDIT_PLAN_NOT_FOUND", "Source edit plan was not found.", 404
                )
            self.workspace.authorize(actor, row.task_id, session)
            decision = self.workspace.identity.check_permission_resource_access(
                actor.actor_id,
                "self_build.workspace",
                "task",
                row.task_id,
                session=session,
            )
            if not decision.allowed:
                raise DomainError(
                    "SOURCE_EDIT_PERMISSION_DENIED",
                    "Protected source proposal access is denied.",
                    403,
                )
            result = self.record(row)
            workspace_row = self.repository.lookup(session, row.workspace_id)
            reservation = self.repository.contract(workspace_row)
            if (
                reservation.plan.task_id != result.plan.task_id
                or reservation.plan.runtime_run_id != result.plan.runtime_run_id
                or reservation.plan.plan_hash != result.plan.workspace_plan_hash
                or result.plan.source_plan.references.original_plan_hash
                != reservation.plan.plan_hash
                or result.plan.source_plan.references.authorization_reference
                != workspace_row.approval_id
                or result.plan.source_plan.source.repository_identity
                != reservation.plan.repository_identity
                or result.plan.source_plan.source.base_sha != reservation.plan.base_sha
                or result.plan.workspace_version > workspace_row.version
            ):
                refuse("SOURCE_EDIT_RECORD_INVALID")
            return result
