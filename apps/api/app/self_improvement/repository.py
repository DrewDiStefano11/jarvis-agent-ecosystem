"""Append-only bounded analysis records; unique content IDs arbitrate concurrency."""

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, String, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.self_improvement import Analysis, CandidateAttestation, Comparison
from app.services.unit_of_work import UnitOfWork


class ImprovementRecordRow(Base):
    __tablename__ = "self_improvement_records"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)
    baseline_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSON)


class ImprovementRepository:
    def __init__(self, sessions):
        self.sessions = sessions

    def _append(self, id, kind, baseline_id, payload):
        try:
            with UnitOfWork(self.sessions) as uow:
                uow.session.add(
                    ImprovementRecordRow(
                        id=id,
                        kind=kind,
                        baseline_id=baseline_id,
                        created_at=datetime.now(UTC),
                        payload=payload,
                    )
                )
        except IntegrityError:
            with self.sessions() as session:
                row = session.get(ImprovementRecordRow, id)
                if row is None or row.kind != kind or row.baseline_id != baseline_id:
                    raise
                return row.payload
        return payload

    def save_analysis(self, analysis: Analysis) -> Analysis:
        # Validate the complete graph before committing, including caller-created
        # model copies (Pydantic's model_copy deliberately skips validation).
        analysis = Analysis.model_validate_json(analysis.model_dump_json())
        payload = self._append(
            analysis.baseline.id, "analysis", analysis.baseline.id, analysis.model_dump(mode="json")
        )
        return Analysis.model_validate(payload)

    def analysis(self, id: str) -> Analysis:
        with self.sessions() as session:
            row = session.get(ImprovementRecordRow, id)
            if row is None or row.kind != "analysis":
                raise ValueError("unknown baseline")
            return Analysis.model_validate(row.payload)

    def list_analyses(self, limit=20):
        if not 1 <= limit <= 100:
            raise ValueError("invalid limit")
        with self.sessions() as session:
            rows = session.scalars(
                select(ImprovementRecordRow)
                .where(ImprovementRecordRow.kind == "analysis")
                .order_by(ImprovementRecordRow.created_at.desc(), ImprovementRecordRow.id)
                .limit(limit)
            ).all()
            return [Analysis.model_validate(row.payload) for row in rows]

    def save_comparison(self, comparison: Comparison, attestation: CandidateAttestation):
        self._append(
            comparison.id,
            "comparison",
            comparison.before_id,
            {
                "comparison": comparison.model_dump(mode="json"),
                "attestation": attestation.model_dump(mode="json"),
            },
        )
        return comparison

    def comparisons(self, baseline_id, limit=20):
        with self.sessions() as session:
            rows = session.scalars(
                select(ImprovementRecordRow)
                .where(
                    ImprovementRecordRow.kind == "comparison",
                    ImprovementRecordRow.baseline_id == baseline_id,
                )
                .order_by(ImprovementRecordRow.created_at.desc())
                .limit(limit)
            ).all()
            return [Comparison.model_validate(row.payload["comparison"]) for row in rows]
