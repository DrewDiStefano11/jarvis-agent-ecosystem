SCHEMA = """
CREATE TABLE IF NOT EXISTS supervisor_state (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    paused BOOLEAN NOT NULL DEFAULT 0,
    emergency_stop BOOLEAN NOT NULL DEFAULT 0,
    desired_worker_state TEXT,
    current_worker_instance_id TEXT,
    current_worker_pid INTEGER,
    current_worker_start_token TEXT,
    last_start_attempt_at REAL,
    last_successful_ready_at REAL,
    last_worker_exit_at REAL,
    restart_attempt_count INTEGER DEFAULT 0,
    restart_window_started_at REAL,
    crash_loop_detected BOOLEAN DEFAULT 0,
    next_restart_at REAL,
    last_error_json TEXT,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS worker_instances (
    instance_id TEXT PRIMARY KEY,
    worker_name TEXT,
    pid INTEGER,
    process_start_token TEXT,
    process_create_time REAL,
    scenario TEXT,
    status TEXT,
    started_at REAL,
    ready_at REAL,
    last_heartbeat_at REAL,
    shutdown_requested_at REAL,
    stopped_at REAL,
    exit_code INTEGER,
    exit_reason TEXT,
    restart_number INTEGER,
    log_stdout_path TEXT,
    log_stderr_path TEXT,
    metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS supervisor_events (
    event_id TEXT PRIMARY KEY,
    timestamp REAL,
    event_type TEXT,
    worker_instance_id TEXT,
    previous_state TEXT,
    new_state TEXT,
    severity TEXT,
    details_json TEXT
);

CREATE TABLE IF NOT EXISTS restart_attempts (
    attempt_id TEXT PRIMARY KEY,
    timestamp REAL,
    worker_instance_id TEXT,
    attempt_number INTEGER,
    reason TEXT,
    delay_seconds REAL,
    outcome TEXT
);

CREATE TABLE IF NOT EXISTS supervisor_lease (
    lease_id TEXT PRIMARY KEY,
    supervisor_id TEXT,
    pid INTEGER,
    start_token TEXT,
    acquired_at REAL,
    expires_at REAL
);
"""
