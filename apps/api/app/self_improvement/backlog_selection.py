"""Deterministic opportunity ordering; model recommendations grant no authority."""

from dataclasses import dataclass

from app.models.self_improvement import Analysis, Proposal, Weakness
from app.self_improvement.engine import digest

PRIORITY = {"critical": 0, "high": 1, "medium": 2, "low": 3}
CONFIDENCE = {"high": 0, "medium": 1, "low": 2}
SELECTABLE = frozenset({"proposed", "needs_evidence", "ready_for_review"})


@dataclass(frozen=True)
class BacklogCandidate:
    analysis: Analysis
    proposal: Proposal
    weakness: Weakness
    scope_key: str

    @property
    def work_kind(self):
        return (
            "prepare_experiment"
            if self.proposal.status == "ready_for_review" and self.proposal.experiment is not None
            else "gather_evidence"
        )


def scope_key(analysis: Analysis, proposal: Proposal) -> str:
    """Match active work across new captures without treating new evidence as approval."""
    observations = {item.id: item for item in analysis.baseline.observations}
    identities = {
        (
            item.source_type,
            item.source_id,
            item.stage,
            item.role,
            item.model,
            item.provider,
            item.metric,
            item.inference_mode,
        )
        for reference in proposal.evidence_ids
        for item in [observations[reference]]
    }
    return digest(["improvement-work-scope-v1", proposal.category, sorted(identities)])


def ordered_candidates(analyses: list[Analysis]) -> list[BacklogCandidate]:
    if len(analyses) > 8:
        raise ValueError("selection accepts at most eight persisted analyses")
    candidates = []
    seen = set()
    for supplied in analyses:
        # Reject invalid graphs constructed with unchecked model_copy updates.
        analysis = Analysis.model_validate_json(supplied.model_dump_json())
        if analysis.baseline.created_at.tzinfo is None:
            raise ValueError("baseline ordering requires timezone-aware capture time")
        if analysis.baseline.id in seen:
            continue
        seen.add(analysis.baseline.id)
        weaknesses = {item.id: item for item in analysis.weaknesses}
        for proposal in analysis.proposals:
            if proposal.status not in SELECTABLE:
                continue
            candidates.append(
                BacklogCandidate(
                    analysis,
                    proposal,
                    weaknesses[proposal.weakness_id],
                    scope_key(analysis, proposal),
                )
            )
    return sorted(
        candidates,
        key=lambda item: (
            PRIORITY[item.proposal.priority],
            CONFIDENCE[item.weakness.confidence],
            -item.weakness.affected_subjects,
            -item.weakness.frequency,
            -item.analysis.baseline.created_at.timestamp(),
            item.analysis.baseline.id,
            item.proposal.id,
        ),
    )
