"""Bounded advisory discovery from one read snapshot; never admits tasks."""

from sqlalchemy import select, text

from app.models.improvement_backlog import ImprovementCandidate
from app.models.self_improvement import Analysis
from app.self_improvement.backlog_repository import ImprovementBacklogRepository
from app.self_improvement.backlog_selection import ordered_candidates
from app.self_improvement.engine import digest
from app.self_improvement.repository import ImprovementRecordRow

# Match the native selection request's maximum persisted-analysis batch.
DISCOVERY_BASELINE_LIMIT = 8


class CandidateRepository:
    def __init__(self, sessions):
        self.sessions = sessions

    def get_candidates(self, *, limit=20, offset=0):
        if not 1 <= limit <= 100 or not 0 <= offset <= 100_000:
            raise ValueError("invalid candidate page bounds")
        with self.sessions() as session:
            # Python sqlite's legacy transaction mode does not BEGIN on SELECT.
            # An explicit read transaction keeps analyses and admissions consistent.
            if session.get_bind().dialect.name == "sqlite":
                session.execute(text("BEGIN"))
            rows = session.scalars(
                select(ImprovementRecordRow)
                .where(ImprovementRecordRow.kind == "analysis")
                .order_by(ImprovementRecordRow.created_at.desc(), ImprovementRecordRow.id)
                .limit(DISCOVERY_BASELINE_LIMIT)
            ).all()
            analyses = [Analysis.model_validate(row.payload) for row in rows]
            admitted, scopes = ImprovementBacklogRepository.admission_state_in_session(
                session, [analysis.baseline.id for analysis in analyses]
            )
            result = []
            seen = set(scopes)
            for candidate in ordered_candidates(analyses):
                proposal = candidate.proposal
                if proposal.id in admitted or candidate.scope_key in seen:
                    continue
                seen.add(candidate.scope_key)
                result.append(
                    ImprovementCandidate(
                        baseline_id=candidate.analysis.baseline.id,
                        proposal_id=proposal.id,
                        weakness_id=proposal.weakness_id,
                        scope_key=candidate.scope_key,
                        priority=proposal.priority,
                        work_kind=candidate.work_kind,
                        evidence_ids=proposal.evidence_ids,
                        experiment_digest=digest(proposal.experiment)
                        if proposal.experiment is not None
                        else None,
                    )
                )
            return result[offset : offset + limit]
