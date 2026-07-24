from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def now_utc() -> datetime:
    return datetime.now(UTC)


class DepartmentRow(Base):
    __tablename__ = "departments"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text)
    manager_agent_id: Mapped[str | None] = mapped_column(String(80))
    schema_version: Mapped[str] = mapped_column(String(20), default="1.0")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class AgentRow(Base):
    __tablename__ = "agents"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    role: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text)
    department_id: Mapped[str] = mapped_column(ForeignKey("departments.id"), index=True)
    # Self-referential manager integrity is validated by manifests and seeding.
    manager_id: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(40), index=True)
    previous_status: Mapped[str | None] = mapped_column(String(40))
    current_task_id: Mapped[str | None] = mapped_column(String(80))
    progress: Mapped[int] = mapped_column(Integer, default=0)
    status_message: Mapped[str] = mapped_column(Text)
    deployment_status: Mapped[str] = mapped_column(String(40))
    is_temporary: Mapped[bool] = mapped_column(Boolean, default=False)
    schema_version: Mapped[str] = mapped_column(String(20), default="1.0")
    version: Mapped[str] = mapped_column(String(30), default="1.0.0")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TaskRow(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    original_request: Mapped[str] = mapped_column(Text)
    parent_task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), index=True)
    project_id: Mapped[str | None] = mapped_column(String(80))
    creator: Mapped[str] = mapped_column(String(120))
    assigned_manager_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"))
    priority: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(40), index=True)
    progress: Mapped[int] = mapped_column(Integer)
    status_message: Mapped[str] = mapped_column(Text)
    result: Mapped[str | None] = mapped_column(Text)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    maximum_retries: Mapped[int] = mapped_column(Integer, default=2)
    schema_version: Mapped[str] = mapped_column(String(20), default="1.0")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApprovalRow(Base):
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    requesting_agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"))
    action_type: Mapped[str] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    risk_level: Mapped[str] = mapped_column(String(20))
    affected_resources: Mapped[list[str]] = mapped_column(JSON)
    exact_action_preview: Mapped[str] = mapped_column(Text)
    expected_outcome: Mapped[str] = mapped_column(Text)
    reversal_method: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30), index=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(120))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)
    schema_version: Mapped[str] = mapped_column(String(20), default="1.0")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ArtifactRow(Base):
    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    producing_agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"))
    name: Mapped[str] = mapped_column(String(200))
    artifact_type: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text)
    content_reference: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    version: Mapped[str] = mapped_column(String(30), default="1.0.0")
    schema_version: Mapped[str] = mapped_column(String(20), default="1.0")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ContextAssemblyRow(Base):
    __tablename__ = "context_assemblies"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    project_id: Mapped[str] = mapped_column(String(120), index=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    input_hash: Mapped[str] = mapped_column(String(64), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64), index=True)
    policy_version: Mapped[str] = mapped_column(String(80))
    included_source_count: Mapped[int] = mapped_column(Integer)
    excluded_source_count: Mapped[int] = mapped_column(Integer)
    redaction_count: Mapped[int] = mapped_column(Integer)
    injection_finding_count: Mapped[int] = mapped_column(Integer)
    conflict_count: Mapped[int] = mapped_column(Integer)
    schema_version: Mapped[str] = mapped_column(String(20), default="1.0")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NotificationRow(Base):
    __tablename__ = "notifications"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    notification_type: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text)
    related_task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    related_agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"))
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    schema_version: Mapped[str] = mapped_column(String(20), default="1.0")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEventRow(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(120), index=True)
    actor: Mapped[str] = mapped_column(String(120), default="system")
    agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"))
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), index=True)
    approval_id: Mapped[str | None] = mapped_column(ForeignKey("approvals.id"))
    previous_state: Mapped[str | None] = mapped_column(String(80))
    new_state: Mapped[str | None] = mapped_column(String(80))
    correlation_id: Mapped[str] = mapped_column(String(80), index=True)
    sequence_number: Mapped[int] = mapped_column(Integer)
    event_session_id: Mapped[str] = mapped_column(String(80), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    schema_version: Mapped[str] = mapped_column(String(20), default="1.0")


class SystemStateRow(Base):
    __tablename__ = "system_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    emergency_stop: Mapped[bool] = mapped_column(Boolean, default=False)
    simulator_status: Mapped[str] = mapped_column(String(40), default="idle")
    event_session_id: Mapped[str] = mapped_column(String(80))
    current_sequence_number: Mapped[int] = mapped_column(Integer, default=0)
    seed_data_version: Mapped[str] = mapped_column(String(30), default="2.0")
    last_successful_startup: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_clean_shutdown: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    startup_was_clean: Mapped[bool] = mapped_column(Boolean, default=True)
    last_workflow_run_id: Mapped[str | None] = mapped_column(String(80))
    last_checkpoint_id: Mapped[str | None] = mapped_column(String(80))
    recovery_status: Mapped[str] = mapped_column(String(40), default="none")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class WorkflowRunRow(Base):
    __tablename__ = "workflow_runs"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    correlation_id: Mapped[str] = mapped_column(String(80), index=True)
    root_task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
    workflow_type: Mapped[str] = mapped_column(String(80))
    workflow_version: Mapped[str] = mapped_column(String(30))
    current_step_index: Mapped[int] = mapped_column(Integer, default=0)
    current_step_identifier: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(40), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pause_reason: Mapped[str | None] = mapped_column(Text)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    checkpoint_id: Mapped[str | None] = mapped_column(String(80))
    resume_eligibility: Mapped[bool] = mapped_column(Boolean, default=True)


class WorkflowCheckpointRow(Base):
    __tablename__ = "workflow_checkpoints"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workflow_run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    workflow_version: Mapped[str] = mapped_column(String(30))
    step_index: Mapped[int] = mapped_column(Integer)
    step_identifier: Mapped[str] = mapped_column(String(120))
    root_task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OutboxEventRow(Base):
    __tablename__ = "outbox_events"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(120), index=True)
    envelope: Mapped[dict[str, Any]] = mapped_column(JSON)
    correlation_id: Mapped[str] = mapped_column(String(80))
    event_session_id: Mapped[str] = mapped_column(String(80), index=True)
    sequence_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), index=True, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    publish_attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_publish_error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("event_session_id", "sequence_number"),)


class IdempotencyRecordRow(Base):
    __tablename__ = "idempotency_records"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    command_type: Mapped[str] = mapped_column(String(120))
    canonical_request_hash: Mapped[str] = mapped_column(String(64))
    response_status: Mapped[int] = mapped_column(Integer)
    response_body: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_resource_id: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expiration_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("idempotency_key", "command_type"),)


class TaskAgentRow(Base):
    __tablename__ = "task_agents"
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), primary_key=True)


class TaskDependencyRow(Base):
    __tablename__ = "task_dependencies"
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    dependency_task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    dependency_type: Mapped[str] = mapped_column(String(30), default="requires")


class TaskBlockerRow(Base):
    __tablename__ = "task_blockers"
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    blocker_task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
