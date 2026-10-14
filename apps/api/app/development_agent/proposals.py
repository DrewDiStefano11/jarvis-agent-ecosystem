"""Translate untrusted model file text using independently measured originals."""

from hashlib import sha256

from app.development_agent.context import RepositoryContextReader, refuse
from app.models.development_agent import DevelopmentProposal, RepositoryContext
from app.models.source_change import (
    FileIdentity,
    PlanReferences,
    SourceChange,
    SourceChangeIntent,
    SourceContent,
    SourceObservation,
)
from app.models.source_edit_planning import PersistEditingPlanRequest
from app.self_build.policy import digest
from app.source_change.planner import observation_identity, plan_source_changes
from app.source_edit_planning.service import SourceEditingPlanService


def mutation_observation(context, changed_paths):
    """Match native immediate-sibling observations without inventing source evidence."""
    selected = set(changed_paths)
    parents = {""}
    for path in selected:
        parts = path.split("/")
        parents.update("/".join(parts[:depth]) for depth in range(1, len(parts)))
    entries = tuple(
        entry if entry.path in selected else entry.model_copy(update={"content": None})
        for entry in context.observation.entries
        if entry.path.rpartition("/")[0] in parents
        and (entry.kind != "absent" or entry.path in selected)
    )
    return SourceObservation.model_validate(
        context.observation.model_copy(update={"entries": entries}).model_dump(mode="json")
    )


def compile_proposal(proposal, context, references, *, allow_delete=False):
    """No filesystem/model calls, actor choice, approval or mutation effect."""
    try:
        proposal = DevelopmentProposal.model_validate(
            proposal.model_dump(mode="json", warnings=False)
        )
        context = RepositoryContext.model_validate(context.model_dump(mode="json", warnings=False))
        references = PlanReferences.model_validate(
            references.model_dump(mode="json", warnings=False)
        )
    except (ValueError, TypeError, AttributeError):
        refuse("DEVELOPMENT_PROPOSAL_INVALID")
    if (
        proposal.context_hash != context.context_hash
        or context.context_hash != digest(context.model_dump(mode="json", exclude={"context_hash"}))
        or context.code_state.repository_identity != context.observation.repository_identity
        or context.code_state.base_sha != context.observation.base_sha
        or context.code_state.head_sha != context.observation.head_sha
    ):
        refuse("DEVELOPMENT_CONTEXT_STALE")
    selected = set(context.selection.paths)
    observed = {item.path: item for item in context.observation.entries}
    changes = []
    for file in proposal.files:
        if file.path not in selected:
            refuse("DEVELOPMENT_PATH_NOT_SELECTED")
        before = observed.get(file.path)
        if before is None:
            refuse("DEVELOPMENT_CONTEXT_INCOMPLETE")
        if file.operation == "delete" and not allow_delete:
            refuse("DEVELOPMENT_DELETE_DENIED")
        if file.operation == "add":
            if before.kind != "absent":
                refuse("DEVELOPMENT_ALREADY_EXISTS")
            original = None
            mode = "100644"
        else:
            if before.kind != "file" or before.content is None:
                refuse("DEVELOPMENT_CONTEXT_INCOMPLETE")
            if file.before_digest != before.content.digest:
                refuse("DEVELOPMENT_BEFORE_CONFLICT")
            original = FileIdentity(**before.content.model_dump(exclude={"text"}))
            mode = original.mode
        after = None
        if file.text is not None:
            raw = file.text.encode("utf-8")
            after = SourceContent(
                text=file.text,
                digest=sha256(raw).hexdigest(),
                byte_count=len(raw),
                mode=mode,
            )
        try:
            changes.append(
                SourceChange(operation=file.operation, path=file.path, before=original, after=after)
            )
        except ValueError:
            refuse("DEVELOPMENT_PROPOSAL_INVALID")
    observation = mutation_observation(context, (change.path for change in changes))
    intent = SourceChangeIntent(
        source=observation_identity(observation),
        references=references,
        changes=tuple(changes),
    )
    plan_source_changes(intent, observation)
    return intent


class DevelopmentProposalService:
    """Persist exact proposals while fresh source and native authority remain pinned."""

    def __init__(self, app):
        self.reader = RepositoryContextReader(app)
        self.plans = SourceEditingPlanService(app)

    def persist(
        self,
        actor,
        workspace_id,
        authority,
        selection,
        command_id,
        proposal,
        *,
        allow_delete=False,
        commit_guard=None,
        baseline=None,
        repair_selection=None,
        expected_native_context_hash=None,
    ):
        # Re-measure after inference. A model's before hash is never native evidence.
        def finalize(context, guard):
            with self.plans.repository.sessions() as session:
                row = self.plans.repository.lookup(session, workspace_id)
                reservation = self.plans.repository.contract(row)
                references = PlanReferences(
                    original_plan_hash=reservation.plan.plan_hash,
                    authorization_reference=row.approval_id,
                )
            if baseline is None:
                intent = compile_proposal(proposal, context, references, allow_delete=allow_delete)
            else:
                from app.development_agent.corrections import compile_correction

                intent = compile_correction(
                    proposal,
                    baseline,
                    context,
                    repair_selection,
                    references,
                    allow_delete=allow_delete,
                )
            guard()

            def final_commit(session):
                guard.in_transaction(session)
                if commit_guard is not None:
                    commit_guard(session)

            return self.plans.persist(
                actor,
                PersistEditingPlanRequest(
                    workspace_id=workspace_id,
                    expected_workspace_version=reservation.version,
                    expected_workspace_plan_hash=reservation.plan.plan_hash,
                    command_id=command_id,
                    source_intent=intent,
                ),
                mutation_observation(context, (change.path for change in intent.changes)),
                context.code_state,
                commit_guard=final_commit,
            )

        return self.reader.inspect(
            actor,
            workspace_id,
            authority,
            selection,
            expected_hash=proposal.context_hash
            if baseline is None
            else expected_native_context_hash,
            finalize=finalize,
        )
