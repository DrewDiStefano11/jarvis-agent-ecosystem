"""Private immutable baseline projection; native checkpoints own acknowledgement."""

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DevelopmentBaselineRow(Base):
    __tablename__ = "development_baselines"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dispatch_id: Mapped[str] = mapped_column(ForeignKey("development_dispatches.id"), unique=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("development_admissions.task_id"))
    payload: Mapped[dict] = mapped_column(JSON)
    integrity_digest: Mapped[str] = mapped_column(String(64))
