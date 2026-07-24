DATABASE_REVISION = "20260724_01"

SCHEMA = """
CREATE TABLE IF NOT EXISTS supervisor_state (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    paused INTEGER NOT NULL DEFAULT 0,
    emergency_stop INTEGER NOT NULL DEFAULT 0,
    current_worker_instance_id TEXT,
    current_worker_pid INTEGER,
    current_worker_start_token TEXT,
    last_start_attempt_at REAL,
    last_successful_ready_at REAL,
    last_worker_exit_at REAL,
    restart_attempt_count INTEGER NOT NULL DEFAULT 0,
    restart_window_started_at REAL,
    crash_loop_detected INTEGER NOT NULL DEFAULT 0,
    next_restart_at REAL,
    last_error_json TEXT,
    started_at REAL,
    stopped_at REAL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS worker_instances (
    instance_id TEXT PRIMARY KEY,
    pid INTEGER NOT NULL,
    process_start_token TEXT NOT NULL,
    process_create_time REAL NOT NULL,
    executable TEXT,
    command_line TEXT,
    scenario TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at REAL NOT NULL,
    ready_at REAL,
    last_heartbeat_at REAL,
    last_heartbeat_sequence INTEGER,
    shutdown_requested_at REAL,
    stopped_at REAL,
    exit_code INTEGER,
    exit_reason TEXT,
    log_stdout_path TEXT NOT NULL,
    log_stderr_path TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS supervisor_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    timestamp REAL NOT NULL,
    event_type TEXT NOT NULL,
    worker_instance_id TEXT,
    previous_state TEXT,
    new_state TEXT,
    severity TEXT NOT NULL,
    details_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS supervisor_lease (
    lease_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    pid INTEGER NOT NULL,
    start_token TEXT NOT NULL,
    acquired_at REAL NOT NULL,
    renewed_at REAL NOT NULL,
    expires_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS runtime_metrics (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    completed_workers INTEGER NOT NULL DEFAULT 0,
    failed_workers INTEGER NOT NULL DEFAULT 0,
    forced_terminations INTEGER NOT NULL DEFAULT 0,
    restart_count INTEGER NOT NULL DEFAULT 0,
    recovery_count INTEGER NOT NULL DEFAULT 0,
    unexpected_error_count INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS ix_worker_instances_status
    ON worker_instances(status);
CREATE INDEX IF NOT EXISTS ix_worker_instances_started_at
    ON worker_instances(started_at);
CREATE INDEX IF NOT EXISTS ix_supervisor_events_timestamp
    ON supervisor_events(timestamp);
"""
