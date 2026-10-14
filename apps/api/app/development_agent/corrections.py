"""Pure gross-delta compiler. Caller must supply protected baseline and native pins.

No provider, approval, database or filesystem effects. Historical source is
reference data; the physical before identities always come from destination.
"""

from hashlib import sha256

from app.development_agent.context import refuse
from app.development_agent.proposals import compile_proposal, mutation_observation
from app.models.development_agent import DevelopmentProposal
from app.models.source_change import FileIdentity, SourceChange, SourceChangeIntent, SourceContent
from app.self_build.policy import digest
from app.source_change.planner import observation_identity, plan_source_changes


def correction_context_hash(baseline, destination, selection):
    return digest(
        dict(
            baseline_snapshot_hash=baseline.snapshot_hash,
            destination_context_hash=destination.context_hash,
            repair_selection=selection.model_dump(mode="json"),
        )
    )


def compile_correction(
    proposal,
    baseline,
    destination,
    selection,
    references,
    *,
    allow_delete=False,
    publication_source=None,
):
    """Validate logical edits against the baseline, render effects against real originals."""
    if (
        baseline.snapshot_hash
        != digest(baseline.model_dump(mode="json", exclude={"snapshot_hash"}))
        or destination.context_hash
        != digest(destination.model_dump(mode="json", exclude={"context_hash"}))
        or proposal.context_hash != correction_context_hash(baseline, destination, selection)
        or baseline.context.task_id != destination.task_id
        or baseline.context.workspace_id == destination.workspace_id
        or baseline.context.code_state.repository_identity
        != destination.code_state.repository_identity
        or destination.code_state.changes
        or not set(selection.paths).issubset(baseline.context.selection.paths)
        or not set(baseline.context.selection.paths).issubset(destination.selection.paths)
        or any(item.path not in selection.paths for item in proposal.files)
    ):
        refuse("DEVELOPMENT_CORRECTION_CONTEXT_INVALID")
    from app.development_agent.publication import verify_destination

    verify_destination(baseline, destination, publication_source)
    desired = {item.change.path: item.content for item in baseline.files}
    logical = DevelopmentProposal.model_validate(
        proposal.model_copy(update={"context_hash": baseline.context.context_hash}).model_dump(
            mode="json"
        )
    )
    edits = compile_proposal(logical, baseline.context, references, allow_delete=allow_delete)
    for change in edits.changes:
        desired[change.path] = change.after
    observed = {entry.path: entry for entry in destination.observation.entries}
    changes = []
    for path, after in sorted(desired.items()):
        entry = observed.get(path)
        if entry is None or entry.kind not in {"file", "absent"}:
            refuse("DEVELOPMENT_CORRECTION_SOURCE_INCOMPLETE")
        if entry.kind == "file" and entry.content is None:
            refuse("DEVELOPMENT_CORRECTION_SOURCE_INCOMPLETE")
        before = (
            FileIdentity(**entry.content.model_dump(exclude={"text"})) if entry.content else None
        )
        if after is not None:
            # A model requests text, never a mode change. Preserve measured mode
            # for an existing physical file; new physical files are regular 100644.
            raw = after.text.encode("utf-8")
            after = SourceContent(
                text=after.text,
                digest=sha256(raw).hexdigest(),
                byte_count=len(raw),
                mode=before.mode if before else "100644",
            )
        if (before is None and after is None) or (
            before is not None
            and after is not None
            and before == FileIdentity(**after.model_dump(exclude={"text"}))
        ):
            continue
        if after is None and not allow_delete:
            refuse("DEVELOPMENT_DELETE_DENIED")
        changes.append(
            SourceChange(
                operation="delete" if after is None else "add" if before is None else "modify",
                path=path,
                before=before,
                after=after,
            )
        )
    if not changes:
        refuse("DEVELOPMENT_CORRECTION_NO_CHANGES")
    observation = mutation_observation(destination, (item.path for item in changes))
    intent = SourceChangeIntent(
        source=observation_identity(observation), references=references, changes=tuple(changes)
    )
    plan_source_changes(intent, observation)
    return intent
