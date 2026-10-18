"""Protected append-only correction workspace lineage projection."""

from sqlalchemy import JSON, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DevelopmentLineageRow(Base):
    __tablename__ = "development_lineages"
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("development_workspaces.id"), primary_key=True
    )
    task_id: Mapped[str] = mapped_column(ForeignKey("development_admissions.task_id"))
    ordinal: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)
    integrity_digest: Mapped[str] = mapped_column(String(64))
    __table_args__ = (UniqueConstraint("task_id", "ordinal"),)
