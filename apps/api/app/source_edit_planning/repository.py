"""Dedicated immutable source plan evidence on the existing control-plane database."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SourceEditingPlanRow(Base):
    __tablename__ = "source_edit_plans"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    runtime_run_id: Mapped[str] = mapped_column(ForeignKey("agent_runtime_runs.run_id"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("development_workspaces.id"), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("identity_agents.id"))
    command_id: Mapped[str] = mapped_column(String(120))
    request_hash: Mapped[str] = mapped_column(String(64))
    plan_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (
        UniqueConstraint("actor_id", "command_id", name="uq_source_plan_actor_command"),
    )
