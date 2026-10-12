"""One fenced mutation lineage per immutable editing proposal."""

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SourceMutationRow(Base):
    __tablename__ = "source_mutation_intents"
    editing_record_id: Mapped[str] = mapped_column(
        ForeignKey("source_edit_plans.id"), primary_key=True
    )
    operation_id: Mapped[str] = mapped_column(String(120), unique=True)
    payload: Mapped[dict] = mapped_column(JSON)
    integrity_digest: Mapped[str] = mapped_column(String(64))
