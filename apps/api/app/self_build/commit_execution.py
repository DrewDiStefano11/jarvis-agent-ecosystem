"""Native execution authority and snapshots; no public execution entry point yet."""

import os
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

from app.core.errors import DomainError
from app.models import git_commit_history as history_contracts
from app.models import git_commit_metadata as metadata_contracts
from app.models import git_execution as contracts
from app.models.git_commit_authorization import CommitSelection, PreviewCommitPreparation
from app.models.git_execution import NativeCommitApproval, NativeCommitPlan
from app.self_build import (
    commit_completion,
    commit_effect_fence,
    commit_index_install,
    commit_journal,
    commit_metadata,
    commit_metadata_rules,
    commit_namespace_watch,
    commit_object_directory,
    commit_object_install,
    commit_position,
    commit_process,
    commit_progress,
    commit_recovery,
    commit_recovery_read,
    commit_reference,
    commit_runner,
    commit_source_namespace,
    commit_staging,
)
from app.self_build.applied_source import AppliedSourceCommitService
from app.self_build.change_verifier import ChangeVerifier
from app.self_build.commit_objects import OBJECT_POLICY_DIGEST, compile_objects
from app.self_build.policy import digest
from app.source_mutation.artifacts import ARTIFACT_POLICY_DIGEST, ArtifactManifestReader
from app.tool_execution.filesystem import open_directory, open_file, read_bytes

EXECUTION_POLICY_DIGEST = sha256(
    (
        "".join(
            Path(module).read_text(encoding="utf-8")
            for module in (
                __file__,
                contracts.__file__,
                metadata_contracts.__file__,
                history_contracts.__file__,
                commit_metadata.__file__,
                commit_completion.__file__,
                commit_effect_fence.__file__,
                commit_runner.__file__,
                commit_metadata_rules.__file__,
                commit_process.__file__,
                commit_position.__file__,
                commit_recovery.__file__,
                commit_recovery_read.__file__,
                commit_reference.__file__,
                commit_index_install.__file__,
                commit_source_namespace.__file__,
                commit_journal.__file__,
                commit_namespace_watch.__file__,
                commit_progress.__file__,
                commit_staging.__file__,
                commit_object_directory.__file__,
                commit_object_install.__file__,
            )
        )
        + OBJECT_POLICY_DIGEST
        + ARTIFACT_POLICY_DIGEST
    ).encode()
).hexdigest()
APPROVED = "self_build.commit_execution.approved"


def refuse(code, status=409):
    raise DomainError(
        code, "Native commit execution failed a required authority or evidence check.", status
    )


class NativeCommitService:
    def __init__(self, app):
        self.app = app
        self.applied = AppliedSourceCommitService(app)
        self.preparation = self.applied.preparation
        self.repository = self.preparation.repository
        self.artifacts = ArtifactManifestReader(app)

    def permission(self, actor, row, session):
        decision = self.preparation.workspace.identity.check_permission_resource_access(
            actor.actor_id, "self_build.git.commit.create", "task", row.task_id, session=session
        )
        if not decision.allowed:
            refuse("GIT_COMMIT_EXECUTION_PERMISSION_DENIED", 403)

    def measured(self, actor, workspace_id, body, callback=None, *, request=None):
        prepared = self.preparation.read(actor, workspace_id, body.preparation_command_id)
        if prepared.state != "checkpointed":
            refuse("GIT_COMMIT_PREPARATION_CHECKPOINT_REQUIRED")
        proposal = prepared.plan.proposal
        evidence = self.applied.source(actor, workspace_id, prepared.plan.source_edit_record_id)
        manifest = self.artifacts.read(actor, prepared.plan.source_edit_record_id)
        selection = CommitSelection(
            source_edit_record_id=prepared.plan.source_edit_record_id,
            selection=tuple(item.path for item in proposal.files),
            metadata=proposal.metadata,
        )
        inspection = self.preparation.inspection_plan(actor, workspace_id, selection)
        inspection_request = PreviewCommitPreparation(
            **selection.model_dump(),
            inspection_approval_id=body.inspection_approval_id,
            expected_inspection_plan_hash=body.expected_inspection_plan_hash,
        )
        acknowledged = self.preparation.creation.read(actor, workspace_id)
        with self.repository.leases._write() as session:
            row, attempt, policy, creation, nonce = self.preparation.live(
                actor, workspace_id, session, request=request
            )
            self.permission(actor, row, session)
            self.preparation.inspection_approval(
                actor, row, inspection, inspection_request, session
            )
            if creation != acknowledged or attempt != proposal.lineage.attempt_id:
                refuse("GIT_COMMIT_EXECUTION_OWNER_CHANGED")

        def authority():
            with self.repository.leases._write() as session:
                current, current_attempt, current_policy, current_creation, current_nonce = (
                    self.preparation.live(actor, workspace_id, session, request=request)
                )
                self.permission(actor, current, session)
                self.preparation.inspection_approval(
                    actor, current, inspection, inspection_request, session
                )
                if (
                    current_attempt != attempt
                    or current_policy != policy
                    or current_creation != creation
                    or current_nonce != nonce
                ):
                    refuse("GIT_COMMIT_EXECUTION_OWNER_CHANGED")
            if (
                self.applied.source(actor, workspace_id, prepared.plan.source_edit_record_id)
                != evidence
                or self.artifacts.read(actor, prepared.plan.source_edit_record_id) != manifest
                or self.preparation.read(actor, workspace_id, body.preparation_command_id)
                != prepared
            ):
                refuse("GIT_COMMIT_EXECUTION_EVIDENCE_CHANGED")

        def finalize(observation, fs_guard):
            from contextlib import ExitStack

            content = {}
            with ExitStack() as stack:
                target = Path(policy.worktree_root) / workspace_id
                directory = open_directory(stack, target, internal=True)
                for item in proposal.files:
                    if item.after is not None:
                        with open_file(directory, item.path, os.O_RDONLY, internal=True) as fd:
                            content[item.path] = read_bytes(fd, item.after.byte_count)
                program = compile_objects(proposal, observation, content)
                metadata = Path(policy.primary_root) / ".git/worktrees" / workspace_id
                index_directory = open_directory(stack, metadata, internal=True)
                with open_file(index_directory, "index", os.O_RDONLY, internal=True) as fd:
                    original_index_identity = os.fstat(fd)
                    original_index_hash = sha256(read_bytes(fd, 8388608)).hexdigest()
            git_metadata = commit_metadata.measure_metadata(policy, authority)
            commit_metadata.reserve_metadata(git_metadata, program)
            if len(observation.model_dump_json().encode()) > 2097152:
                refuse("GIT_COMMIT_SNAPSHOT_LIMIT")
            payload = dict(
                schema_version="1.0",
                operation="git.commit.create",
                preparation=prepared.model_dump(mode="json"),
                applied_source=evidence.model_dump(mode="json"),
                source_artifact_manifest_hash=manifest.manifest_hash,
                object_program_fingerprint=program.fingerprint,
                object_count=len(program.objects),
                object_payload_bytes=sum(len(item.payload) for item in program.objects),
                index_payload_sha256=sha256(program.index_payload).hexdigest(),
                index_payload_bytes=len(program.index_payload),
                original_index_sha256=original_index_hash,
                original_index_device=original_index_identity.st_dev,
                original_index_inode=original_index_identity.st_ino,
                compiler_policy_digest=OBJECT_POLICY_DIGEST,
                execution_policy_digest=EXECUTION_POLICY_DIGEST,
                snapshot_digest=digest(observation.model_dump(mode="json")),
                git_metadata_snapshot_digest=git_metadata.snapshot_digest,
                maximum_output_bytes=262144,
            )
            plan = NativeCommitPlan(**payload, plan_hash=digest(payload))
            commit_metadata.reserve_history(plan, observation, git_metadata)
            authority()
            fs_guard()
            return (
                plan
                if callback is None
                else callback(plan, observation, program, fs_guard, git_metadata)
            )

        from app.self_build.native_artifacts import mutation_artifacts

        native = mutation_artifacts(
            self.app,
            actor,
            self.preparation.source_record(
                actor, workspace_id, prepared.plan.source_edit_record_id
            ),
        )
        return ChangeVerifier(self.preparation.git.tool(), authority).inspect(
            policy, creation, nonce, finalize=finalize, native_artifacts=native.artifacts
        )

    def preview(self, actor, workspace_id, body):
        return self.measured(actor, workspace_id, body)

    def recovery(self, actor, workspace_id, command_id):
        return commit_recovery_read.inspect_recovery(self, actor, workspace_id, command_id)

    def execute(self, actor, workspace_id, request, *, validation_gate=None):
        return commit_runner.execute(self, actor, workspace_id, request, validation_gate)

    def renew_approval(self, operator, body):
        """Approve the original plan after independently measuring partial effects.

        Never replan against changed HEAD/index or replace original source and
        metadata evidence. The caller approves the exact current recovery hash.
        """

        def persist(preview, history, program, guard):
            plan = history.original.plan
            if (
                body.expected_plan_hash != plan.plan_hash
                or body.expected_recovery_hash != preview.recovery_hash
                or history.current.state == "checkpointed"
            ):
                refuse("GIT_COMMIT_RECOVERY_APPROVAL_CHANGED")
            with self.repository.leases._write() as session:
                row, attempt, *_ = self.preparation.live(operator, body.workspace_id, session)
                self.preparation.operator(operator, row, session)
                self.permission(operator, row, session)
                if attempt != plan.preparation.plan.proposal.lineage.attempt_id:
                    refuse("GIT_COMMIT_EXECUTION_OWNER_CHANGED")
                expiry = datetime.now(UTC) + timedelta(seconds=body.valid_for_seconds)
                guard(check_authority=False)
                event = self.preparation.git.event(
                    session,
                    row,
                    operator,
                    APPROVED,
                    dict(
                        plan=plan.model_dump(mode="json"),
                        snapshot=history.source_snapshot.model_dump(mode="json"),
                        gitMetadata=history.metadata_snapshot.model_dump(mode="json"),
                        recovery=preview.model_dump(mode="json"),
                        originalApprovalId=history.original.original_approval_id,
                        expiresAt=expiry.isoformat(),
                    ),
                )
                guard(check_authority=False)
                result = NativeCommitApproval(
                    approval_id=event.id,
                    approved_by=operator.actor_id,
                    expires_at=expiry,
                    plan=plan,
                )
            self.repository.leases.repository.refresh_event_cursor()
            return result

        return commit_recovery_read.inspect_recovery(
            self, operator, body.workspace_id, body.command_id, finalize=persist
        )

    def approve(self, actor, body):
        def persist(plan, snapshot, program, fs_guard, git_metadata):
            with self.repository.leases._write() as session:
                row, attempt, *_ = self.preparation.live(actor, body.workspace_id, session)
                self.permission(actor, row, session)
                self.preparation.operator(actor, row, session)
                if (
                    body.expected_plan_hash != plan.plan_hash
                    or attempt != plan.preparation.plan.proposal.lineage.attempt_id
                ):
                    refuse("GIT_COMMIT_EXECUTION_PLAN_CHANGED")
                expiry = datetime.now(UTC) + timedelta(seconds=body.valid_for_seconds)
                event = self.preparation.git.event(
                    session,
                    row,
                    actor,
                    APPROVED,
                    dict(
                        plan=plan.model_dump(mode="json"),
                        snapshot=snapshot.model_dump(mode="json"),
                        gitMetadata=git_metadata.model_dump(mode="json"),
                        expiresAt=expiry.isoformat(),
                    ),
                )
                fs_guard(check_authority=False)
                result = NativeCommitApproval(
                    approval_id=event.id, approved_by=actor.actor_id, expires_at=expiry, plan=plan
                )
            self.repository.leases.repository.refresh_event_cursor()
            return result

        return self.measured(actor, body.workspace_id, body, persist)
