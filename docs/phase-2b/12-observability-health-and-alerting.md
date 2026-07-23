# 12 - Observability, Health, and Alerting

This document defines how the Phase 2B system makes its internal state, health, and decisions visible to the operator without requiring them to read raw command-line output.

## Observable System Behavior

### Structured Logs
The worker emits structured JSON logs (or structured text logs) for debugging.
* **Included:** Worker lifecycle events (startup, shutdown, polling started), database connection status, local model server HTTP status codes, lease claims/releases.
* **Explicitly Excluded:** Raw prompt contents and raw file contents must NOT be written to standard logs to prevent log leakage of sensitive user data or massive context bloat.

### Audit Events (SQLite)
Every significant state change is written to the Phase 2A audit log system.
* Task claimed by worker.
* Plan generated (includes the structured JSON plan, but not the raw system prompt).
* Tool execution started.
* Tool execution completed (includes parameters and result summary).
* Approval requested.
* Task completed or failed.

### Metrics & Health State
The system exposes a health endpoint (e.g., `/api/health`) that reports:
* **Worker State:** Healthy, Degraded, Unavailable, Paused (Emergency Stop).
* **Model Provider Health:** Is the local LLM server reachable?
* **Queue Depth:** Number of tasks `queued`.
* **Active Tasks:** Number of tasks `in_progress`.
* **Blocked Tasks:** Number of tasks `waiting_for_approval`.
* **Failed Tasks:** Number of tasks `failed`.
* **Last Activity:** Timestamp of the last successful worker heartbeat or task completion.

## Health States Defined

* **Healthy:** Worker is polling or executing normally. Database and Model Server are reachable.
* **Degraded:** Worker is running, but experiencing high retry rates (e.g., Model server is slow or timing out, or disk space is low).
* **Unavailable:** Database is unreachable, or worker process has crashed.
* **Paused:** The system is in an `emergency_stop` state. Polling and execution are halted.

## Visibility Surfaces

### 1. API Health Response (`/health`)
Provides the raw metrics and health states defined above. Used by local monitoring scripts or the UI.

### 2. System Page (Frontend)
The existing Phase 2A System page is extended to show:
* The current state of the real autonomous worker (Idle, Executing, Polling).
* The connection status to the local model provider (e.g., "Ollama: Connected").
* A visual queue of tasks.

### 3. Daily Summary (Database)
A daily aggregate view queryable by the API, showing tasks processed, success rates, and the number of model failures.

### 4. Operator Alerts
Because Phase 2B targets a local desktop user, "alerts" consist of UI notifications for tasks requiring approval (`Orange`/`Red` actions) or tasks that have `Failed` after exhausting retries. We do not implement email or SMS alerting in this phase.
