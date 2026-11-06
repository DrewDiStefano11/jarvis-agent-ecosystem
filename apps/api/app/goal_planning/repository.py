"""Immutable captured scope plus current consent; native runs own recovery position."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class GoalPlanningPolicyRow(Base):
    __tablename__ = "goal_planning_policies"
    __table_args__ = (
        CheckConstraint("maximum_goals >= 1 AND maximum_goals <= 20"),
        CheckConstraint("admitted_goals >= 0 AND admitted_goals <= maximum_goals"),
    )
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean)
    operator_id: Mapped[str] = mapped_column(ForeignKey("identity_agents.id"))
    worker_actor_id: Mapped[str] = mapped_column(ForeignKey("identity_agents.id"))
    maximum_goals: Mapped[int] = mapped_column(Integer)
    admitted_goals: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    policy_hash: Mapped[str] = mapped_column(String(64))
    scope_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class GoalPlanningAdmissionRow(Base):
    __tablename__ = "goal_planning_admissions"
    goal_id: Mapped[str] = mapped_column(ForeignKey("workspace_goals.id"), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), unique=True)
    policy_id: Mapped[str] = mapped_column(ForeignKey("goal_planning_policies.id"))
    policy_hash: Mapped[str] = mapped_column(String(64))
    worker_actor_id: Mapped[str] = mapped_column(ForeignKey("identity_agents.id"))
    run_id: Mapped[str] = mapped_column(String(120), unique=True)
    task_input_hash: Mapped[str] = mapped_column(String(64))
    context_assembly_id: Mapped[str] = mapped_column(ForeignKey("context_assemblies.id"))
    context_input_hash: Mapped[str] = mapped_column(String(64))
    last_error_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
