"""Create the frozen Phase 2A durable control-plane schema."""

import sqlalchemy as sa
from alembic import op

revision = "20260720_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "departments",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("manager_agent_id", sa.String(80), nullable=True),
        sa.Column("schema_version", sa.String(20), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("command_type", sa.String(120), nullable=False),
        sa.Column("canonical_request_hash", sa.String(64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("response_body", sa.JSON(), nullable=False),
        sa.Column("created_resource_id", sa.String(80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expiration_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("idempotency_key", "command_type"),
    )
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("event_type", sa.String(120), nullable=False),
        sa.Column("envelope", sa.JSON(), nullable=False),
        sa.Column("correlation_id", sa.String(80), nullable=False),
        sa.Column("event_session_id", sa.String(80), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("publish_attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_publish_error", sa.Text(), nullable=True),
        sa.UniqueConstraint("event_session_id", "sequence_number"),
    )
    op.create_index("ix_outbox_events_event_session_id", "outbox_events", ["event_session_id"])
    op.create_index("ix_outbox_events_event_type", "outbox_events", ["event_type"])
    op.create_index("ix_outbox_events_status", "outbox_events", ["status"])
    op.create_table(
        "system_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("emergency_stop", sa.Boolean(), nullable=False),
        sa.Column("simulator_status", sa.String(40), nullable=False),
        sa.Column("event_session_id", sa.String(80), nullable=False),
        sa.Column("current_sequence_number", sa.Integer(), nullable=False),
        sa.Column("seed_data_version", sa.String(30), nullable=False),
        sa.Column("last_successful_startup", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_clean_shutdown", sa.DateTime(timezone=True), nullable=True),
        sa.Column("startup_was_clean", sa.Boolean(), nullable=False),
        sa.Column("last_workflow_run_id", sa.String(80), nullable=True),
        sa.Column("last_checkpoint_id", sa.String(80), nullable=True),
        sa.Column("recovery_status", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "agents",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("role", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("department_id", sa.String(80), nullable=False),
        sa.Column("manager_id", sa.String(80), nullable=True),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("previous_status", sa.String(40), nullable=True),
        sa.Column("current_task_id", sa.String(80), nullable=True),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("status_message", sa.Text(), nullable=False),
        sa.Column("deployment_status", sa.String(40), nullable=False),
        sa.Column("is_temporary", sa.Boolean(), nullable=False),
        sa.Column("schema_version", sa.String(20), nullable=False),
        sa.Column("version", sa.String(30), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"]),
    )
    op.create_index("ix_agents_department_id", "agents", ["department_id"])
    op.create_index("ix_agents_status", "agents", ["status"])
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("original_request", sa.Text(), nullable=False),
        sa.Column("parent_task_id", sa.String(80), nullable=True),
        sa.Column("project_id", sa.String(80), nullable=True),
        sa.Column("creator", sa.String(120), nullable=False),
        sa.Column("assigned_manager_id", sa.String(80), nullable=True),
        sa.Column("priority", sa.String(20), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("status_message", sa.Text(), nullable=False),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("maximum_retries", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(20), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["assigned_manager_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["parent_task_id"], ["tasks.id"]),
    )
    op.create_index("ix_tasks_parent_task_id", "tasks", ["parent_task_id"])
    op.create_index("ix_tasks_status", "tasks", ["status"])
    op.create_table(
        "approvals",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("task_id", sa.String(80), nullable=False),
        sa.Column("requesting_agent_id", sa.String(80), nullable=False),
        sa.Column("action_type", sa.String(120), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("risk_level", sa.String(20), nullable=False),
        sa.Column("affected_resources", sa.JSON(), nullable=False),
        sa.Column("exact_action_preview", sa.Text(), nullable=False),
        sa.Column("expected_outcome", sa.Text(), nullable=False),
        sa.Column("reversal_method", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("reviewed_by", sa.String(120), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("schema_version", sa.String(20), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["requesting_agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
    )
    op.create_index("ix_approvals_status", "approvals", ["status"])
    op.create_index("ix_approvals_task_id", "approvals", ["task_id"])
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("task_id", sa.String(80), nullable=False),
        sa.Column("producing_agent_id", sa.String(80), nullable=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("artifact_type", sa.String(80), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("content_reference", sa.Text(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("version", sa.String(30), nullable=False),
        sa.Column("schema_version", sa.String(20), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["producing_agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
    )
    op.create_index("ix_artifacts_task_id", "artifacts", ["task_id"])
    op.create_table(
        "notifications",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("notification_type", sa.String(40), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("related_task_id", sa.String(80), nullable=True),
        sa.Column("related_agent_id", sa.String(80), nullable=True),
        sa.Column("is_read", sa.Boolean(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("schema_version", sa.String(20), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["related_agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["related_task_id"], ["tasks.id"]),
    )
    op.create_table(
        "task_agents",
        sa.Column("task_id", sa.String(80), primary_key=True),
        sa.Column("agent_id", sa.String(80), primary_key=True),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
    )
    op.create_table(
        "task_blockers",
        sa.Column("task_id", sa.String(80), primary_key=True),
        sa.Column("blocker_task_id", sa.String(80), primary_key=True),
        sa.ForeignKeyConstraint(["blocker_task_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
    )
    op.create_table(
        "task_dependencies",
        sa.Column("task_id", sa.String(80), primary_key=True),
        sa.Column("dependency_task_id", sa.String(80), primary_key=True),
        sa.Column("dependency_type", sa.String(30), nullable=False),
        sa.ForeignKeyConstraint(["dependency_task_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
    )
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("correlation_id", sa.String(80), nullable=False),
        sa.Column("root_task_id", sa.String(80), nullable=False),
        sa.Column("workflow_type", sa.String(80), nullable=False),
        sa.Column("workflow_version", sa.String(30), nullable=False),
        sa.Column("current_step_index", sa.Integer(), nullable=False),
        sa.Column("current_step_identifier", sa.String(120), nullable=True),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pause_reason", sa.Text(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("checkpoint_id", sa.String(80), nullable=True),
        sa.Column("resume_eligibility", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["root_task_id"], ["tasks.id"]),
    )
    op.create_index("ix_workflow_runs_correlation_id", "workflow_runs", ["correlation_id"])
    op.create_index("ix_workflow_runs_status", "workflow_runs", ["status"])
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("event_type", sa.String(120), nullable=False),
        sa.Column("actor", sa.String(120), nullable=False),
        sa.Column("agent_id", sa.String(80), nullable=True),
        sa.Column("task_id", sa.String(80), nullable=True),
        sa.Column("approval_id", sa.String(80), nullable=True),
        sa.Column("previous_state", sa.String(80), nullable=True),
        sa.Column("new_state", sa.String(80), nullable=True),
        sa.Column("correlation_id", sa.String(80), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("event_session_id", sa.String(80), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("schema_version", sa.String(20), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["approval_id"], ["approvals.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
    )
    op.create_index("ix_audit_events_correlation_id", "audit_events", ["correlation_id"])
    op.create_index("ix_audit_events_event_session_id", "audit_events", ["event_session_id"])
    op.create_index("ix_audit_events_event_type", "audit_events", ["event_type"])
    op.create_index("ix_audit_events_task_id", "audit_events", ["task_id"])
    op.create_table(
        "workflow_checkpoints",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("workflow_run_id", sa.String(80), nullable=False),
        sa.Column("workflow_version", sa.String(30), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("step_identifier", sa.String(120), nullable=False),
        sa.Column("root_task_id", sa.String(80), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["root_task_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"]),
    )
    op.create_index(
        "ix_workflow_checkpoints_workflow_run_id",
        "workflow_checkpoints",
        ["workflow_run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_workflow_checkpoints_workflow_run_id", table_name="workflow_checkpoints")
    op.drop_table("workflow_checkpoints")
    op.drop_index("ix_audit_events_task_id", table_name="audit_events")
    op.drop_index("ix_audit_events_event_type", table_name="audit_events")
    op.drop_index("ix_audit_events_event_session_id", table_name="audit_events")
    op.drop_index("ix_audit_events_correlation_id", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_workflow_runs_status", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_correlation_id", table_name="workflow_runs")
    op.drop_table("workflow_runs")
    op.drop_table("task_dependencies")
    op.drop_table("task_blockers")
    op.drop_table("task_agents")
    op.drop_table("notifications")
    op.drop_index("ix_artifacts_task_id", table_name="artifacts")
    op.drop_table("artifacts")
    op.drop_index("ix_approvals_task_id", table_name="approvals")
    op.drop_index("ix_approvals_status", table_name="approvals")
    op.drop_table("approvals")
    op.drop_index("ix_tasks_status", table_name="tasks")
    op.drop_index("ix_tasks_parent_task_id", table_name="tasks")
    op.drop_table("tasks")
    op.drop_index("ix_agents_status", table_name="agents")
    op.drop_index("ix_agents_department_id", table_name="agents")
    op.drop_table("agents")
    op.drop_table("system_state")
    op.drop_index("ix_outbox_events_status", table_name="outbox_events")
    op.drop_index("ix_outbox_events_event_type", table_name="outbox_events")
    op.drop_index("ix_outbox_events_event_session_id", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_table("idempotency_records")
    op.drop_table("departments")
