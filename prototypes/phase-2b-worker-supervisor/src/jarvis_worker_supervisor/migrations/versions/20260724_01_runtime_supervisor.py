"""Create prototype-local runtime supervisor state.

Revision ID: 20260724_01
Revises:
Create Date: 2026-07-24
"""

from __future__ import annotations

from alembic import op

from jarvis_worker_supervisor.schema import SCHEMA

revision = "20260724_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    driver_connection = op.get_bind().connection.driver_connection
    driver_connection.executescript(SCHEMA)
    supervisor_columns = {
        row[1]
        for row in driver_connection.execute("PRAGMA table_info(supervisor_state)").fetchall()
    }
    for name in ("started_at", "stopped_at"):
        if name not in supervisor_columns:
            driver_connection.execute(f"ALTER TABLE supervisor_state ADD COLUMN {name} REAL")

    worker_columns = {
        row[1]
        for row in driver_connection.execute("PRAGMA table_info(worker_instances)").fetchall()
    }
    if "last_heartbeat_sequence" not in worker_columns:
        driver_connection.execute(
            "ALTER TABLE worker_instances ADD COLUMN last_heartbeat_sequence INTEGER"
        )
    if "executable" not in worker_columns:
        driver_connection.execute(
            "ALTER TABLE worker_instances ADD COLUMN executable TEXT"
        )
    if "command_line" not in worker_columns:
        driver_connection.execute(
            "ALTER TABLE worker_instances ADD COLUMN command_line TEXT"
        )

    lease_columns = {
        row[1]
        for row in driver_connection.execute("PRAGMA table_info(supervisor_lease)").fetchall()
    }
    if lease_columns and "owner_id" not in lease_columns:
        driver_connection.executescript(
            """
            DROP TABLE supervisor_lease;
            CREATE TABLE supervisor_lease (
                lease_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                pid INTEGER NOT NULL,
                start_token TEXT NOT NULL,
                acquired_at REAL NOT NULL,
                renewed_at REAL NOT NULL,
                expires_at REAL NOT NULL
            );
            """
        )


def downgrade() -> None:
    for table in (
        "runtime_metrics",
        "supervisor_lease",
        "supervisor_events",
        "worker_instances",
        "supervisor_state",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table}")
