"""Real source reads composed under existing native Windows observation pins."""

import os
from contextlib import ExitStack
from hashlib import sha256
from pathlib import Path

from app.core.errors import DomainError
from app.models.development_agent import ContextSelection, RepositoryContext
from app.models.development_validation import CodeState, FileChange
from app.models.source_change import SourceContent, SourceEntry, SourceObservation, Topology
from app.models.workspace_creation import CreateWorkspaceRequest
from app.self_build.change_verifier import INTERNAL, ChangeVerifier
from app.self_build.policy import digest
from app.tool_execution.filesystem import open_directory, open_file, read_bytes, repository_parts


def refuse(code):
    raise DomainError(code, "Development context failed a required evidence check.", 409) from None


class ContextCommitGuard:
    """Recheck native pins using the caller's transaction without a nested writer."""

    def __init__(self, publication_guard, transaction_guard):
        self.publication_guard = publication_guard
        self.transaction_guard = transaction_guard

    def __call__(self):
        self.publication_guard()

    def in_transaction(self, session):
        self.transaction_guard(session)


class RepositoryContextReader:
    """Internal only. Every read needs the original live lease and creation approval."""

    def __init__(self, app):
        self.creation = app.state.self_build_creation_service

    def inspect(
        self, actor, workspace_id, authority, selection, *, expected_hash=None, finalize=None
    ):
        try:
            authority = CreateWorkspaceRequest.model_validate(
                authority.model_dump(mode="json", warnings=False)
            )
            selection = ContextSelection.model_validate(
                selection.model_dump(mode="json", warnings=False)
            )
        except (ValueError, TypeError, AttributeError):
            refuse("DEVELOPMENT_REQUEST_INVALID")
        record, nonce, private_digest = self.creation.current(actor, workspace_id, authority)
        checkpoint, _, _ = self.creation.checkpoint(actor, record, private_digest)
        if record.state != "ready" or checkpoint is None:
            refuse("DEVELOPMENT_CHECKPOINT_REQUIRED")

        transaction_session = None

        def authority_check():
            if transaction_session is None:
                current, current_nonce, current_digest = self.creation.current(
                    actor, workspace_id, authority
                )
            else:
                row, plan, attempt = self.creation.fence(
                    actor, workspace_id, authority, transaction_session
                )
                current, current_nonce, current_digest = self.creation.intent(row)
                if current.plan != plan or current.attempt_id != attempt:
                    refuse("DEVELOPMENT_OWNER_CHANGED")
            if (current, current_nonce, current_digest) != (record, nonce, private_digest):
                refuse("DEVELOPMENT_OWNER_CHANGED")
            if self.creation.checkpoint(actor, current, current_digest)[0] is None:
                refuse("DEVELOPMENT_CHECKPOINT_REQUIRED")

        policy = self.creation.workspace.policies.get(record.plan.workspace.repository_id)
        verifier = ChangeVerifier(self.creation.git.tool(), authority_check)
        target = Path(policy.worktree_root) / workspace_id

        def capture(measured, publication_guard):
            # Source/metadata/ownership pins and namespace watches remain held.
            if not measured.index_clean:
                refuse("DEVELOPMENT_INDEX_CONFLICT")
            contents = {item.path: item.identity for item in measured.current_inventory}
            entries = []
            directory_paths = set()

            def walk_error(error):
                raise error

            for directory, children, files in os.walk(
                target, followlinks=False, onerror=walk_error
            ):
                publication_guard()
                for leaf in children:
                    path = (Path(directory) / leaf).relative_to(target).as_posix()
                    directory_paths.add(path)
                    entries.append(SourceEntry(path=path, kind="directory"))
                for leaf in files:
                    path = (Path(directory) / leaf).relative_to(target).as_posix()
                    if path in INTERNAL:
                        # Native source plans retain sibling topology for the
                        # verified creation marker/Git pointer, never their bytes.
                        entries.append(SourceEntry(path=path, kind="file"))
                        continue
                    repository_parts(path)
                    if path not in contents:
                        refuse("DEVELOPMENT_INVENTORY_CONFLICT")
                    content = None
                    if path in selection.paths:
                        with ExitStack() as stack:
                            parent = open_directory(stack, target, internal=True)
                            parts = repository_parts(path)
                            for part in parts[:-1]:
                                parent = open_directory(
                                    stack, parent.path / part, parent, part, internal=True
                                )
                            with open_file(parent, parts[-1], os.O_RDONLY, internal=True) as fd:
                                raw = read_bytes(fd, 65536)
                        actual = contents[path]
                        if (
                            sha256(raw).hexdigest() != actual.sha256
                            or len(raw) != actual.byte_count
                        ):
                            refuse("DEVELOPMENT_CONTENT_CHANGED")
                        try:
                            text = raw.decode("utf-8")
                        except UnicodeError:
                            refuse("DEVELOPMENT_SOURCE_ENCODING")
                        content = SourceContent(
                            text=text,
                            digest=actual.sha256,
                            byte_count=actual.byte_count,
                            mode=actual.mode,
                        )
                    entries.append(SourceEntry(path=path, kind="file", content=content))
                    if len(entries) > 4096:
                        refuse("DEVELOPMENT_CONTEXT_LIMIT")
            if {
                entry.path
                for entry in entries
                if entry.kind == "file" and entry.path not in INTERNAL
            } != set(contents):
                refuse("DEVELOPMENT_INVENTORY_CONFLICT")
            for path in selection.paths:
                if path in directory_paths:
                    refuse("DEVELOPMENT_FILE_REQUIRED")
                if path not in contents:
                    # Absent leaves are measured; missing parents are not invented.
                    entries.append(SourceEntry(path=path, kind="absent"))
            observation = SourceObservation(
                repository_identity=measured.repository_identity,
                head_sha=measured.head_sha,
                base_sha=measured.original_base_sha,
                root=Topology(kind="directory"),
                entries=tuple(entries),
            )
            state = CodeState(
                repository_identity=measured.repository_identity,
                head_sha=measured.head_sha,
                base_sha=measured.original_base_sha,
                changes=tuple(
                    FileChange(
                        path=item.path,
                        before_hash=item.before.sha256 if item.before else None,
                        after_hash=item.after.sha256 if item.after else None,
                        before_mode=item.before.mode if item.before else None,
                        after_mode=item.after.mode if item.after else None,
                    )
                    for item in measured.changes
                ),
            )
            payload = dict(
                schema_version="1.0",
                task_id=record.plan.workspace.task_id,
                runtime_run_id=record.plan.workspace.runtime_run_id,
                runtime_attempt_id=record.attempt_id,
                workspace_id=workspace_id,
                creation_operation_id=record.operation_id,
                creation_plan_hash=record.plan.plan_hash,
                native_inventory_digest=measured.inventory_digest,
                selection=selection.model_dump(mode="json"),
                observation=observation.model_dump(mode="json"),
                code_state=state.model_dump(mode="json"),
                content_scope="selected_files",
            )
            context = RepositoryContext(**payload, context_hash=digest(payload))
            if expected_hash is not None and expected_hash != context.context_hash:
                refuse("DEVELOPMENT_CONTEXT_STALE")
            publication_guard()

            def transaction_guard(session):
                nonlocal transaction_session
                if transaction_session is not None:
                    refuse("DEVELOPMENT_TRANSACTION_CONFLICT")
                transaction_session = session
                try:
                    publication_guard()
                finally:
                    transaction_session = None

            guard = ContextCommitGuard(publication_guard, transaction_guard)
            result = context if finalize is None else finalize(context, guard)
            publication_guard()
            return result

        try:
            return verifier.inspect(policy, record, nonce, finalize=capture)
        except (OSError, UnicodeError, ValueError):
            refuse("DEVELOPMENT_CONTEXT_UNAVAILABLE")
