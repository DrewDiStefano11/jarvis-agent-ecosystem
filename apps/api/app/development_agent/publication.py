"""Protected-reader adapter boundary. No external history payload importer."""

from typing import Protocol

from app.development_agent.context import refuse
from app.models.development_publication import PublicationSourceAnchor, PublicationSourceReference
from app.self_build.policy import digest


class PublicationSourceReader(Protocol):
    """Read historical references under the authenticated execution owner's scope.

    The adapter must enforce durable owner/RBAC access. This identity is not the
    independent operator who approves the destination plan and grants no effects.
    """

    def read(
        self, execution_actor, workspace_id: str, reference: PublicationSourceReference
    ) -> PublicationSourceAnchor: ...


def read_anchor(app, execution_actor, baseline, reference):
    reader = getattr(app.state, "development_publication_source_reader", None)
    if reader is None:
        refuse("DEVELOPMENT_PUBLICATION_LINK_UNAVAILABLE")
    anchor = PublicationSourceAnchor.model_validate(
        reader.read(execution_actor, baseline.context.workspace_id, reference).model_dump(
            mode="json"
        )
    )
    if (
        anchor.reference != reference
        or anchor.commit_sha != reference.expected_head_sha
        or anchor.task_id != baseline.task_id
        or anchor.workspace_id != baseline.context.workspace_id
        or anchor.source_edit_record_id != baseline.editing_record_id
        or anchor.source_edit_plan_hash != baseline.editing_plan_hash
        or anchor.mutation_operation_id != baseline.mutation_operation_id
        or anchor.mutation_checkpoint_id != baseline.mutation_checkpoint_id
        or anchor.parent_code_state_hash
        != digest(baseline.context.code_state.model_dump(mode="json"))
        or anchor.parent_sha != baseline.context.code_state.head_sha
    ):
        refuse("DEVELOPMENT_PUBLICATION_LINK_CONFLICT")
    return anchor


def verify_destination(baseline, destination, anchor):
    if anchor is None:
        valid = (
            destination.code_state.head_sha == baseline.context.code_state.head_sha
            and destination.code_state.base_sha == baseline.context.code_state.base_sha
        )
    else:
        valid = (
            destination.code_state.head_sha == anchor.commit_sha
            and destination.code_state.base_sha == anchor.commit_sha
            and anchor.parent_code_state_hash
            == digest(baseline.context.code_state.model_dump(mode="json"))
        )
        observed = {entry.path: entry for entry in destination.observation.entries}
        # Every original admitted selected path must have the captured committed bytes.
        for entry in baseline.context.observation.entries:
            if entry.path not in baseline.context.selection.paths:
                continue
            current = observed.get(entry.path)
            if (
                current is None
                or current.kind != entry.kind
                or current.content != entry.content
                or (entry.kind == "file" and entry.content is None)
            ):
                valid = False
    if not valid or destination.code_state.changes:
        refuse("DEVELOPMENT_CORRECTION_CONTEXT_INVALID")
