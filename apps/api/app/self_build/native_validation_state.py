"""C-owned native state capture for B's validation journal; never test evidence.

The trusted host supplies the workspace-to-native-command binding. No model/API
state, receipt, signing key, command execution or approval is imported here.
"""

from app.models.development_validation import CodeState, FileChange
from app.self_build.commit_process import refuse
from app.self_build.commit_recovery_read import inspect_recovery


class NativeValidationStateReader:
    def __init__(self, native_service, command_for_workspace):
        if not callable(command_for_workspace):
            raise ValueError("protected native command binding is required")
        self.service = native_service
        self.command_for_workspace = command_for_workspace

    def capture(self, actor, workspace_id, command_id):
        def captured(preview, history, program, guard):
            record = history.current
            proposal = record.plan.preparation.plan.proposal
            source = history.source_snapshot
            # Tests must observe exactly the source that the commit contains.
            # Extra dirty/untracked changes cannot influence validation of a
            # smaller committed selection and then disappear from publication.
            if tuple(item.path for item in proposal.files) != tuple(
                item.path for item in source.changes
            ):
                refuse("GIT_VALIDATION_SELECTION_INCOMPLETE")
            if preview.position.branch == "parent":
                head = proposal.parent_sha
            elif preview.position.branch == "exact_commit":
                head = program.commit_sha
            else:
                refuse("GIT_VALIDATION_HEAD_UNVERIFIED")
            value = CodeState(
                repository_identity=source.repository_identity,
                head_sha=head,
                base_sha=source.original_base_sha,
                changes=tuple(
                    FileChange(
                        path=item.path,
                        before_hash=None if item.before is None else item.before.sha256,
                        after_hash=None if item.after is None else item.after.sha256,
                        before_mode=None if item.before is None else item.before.mode,
                        after_mode=None if item.after is None else item.after.mode,
                    )
                    for item in source.changes
                ),
            )
            guard()
            return value

        return inspect_recovery(self.service, actor, workspace_id, command_id, finalize=captured)

    def __call__(self, row, session):
        from app.agent_runtime.authorization import RuntimeActorContext

        command_id = self.command_for_workspace(row, session)
        actor = RuntimeActorContext(actor_id=row.actor_id, stable_key="native-validation-owner")
        return self.capture(actor, row.id, command_id)
