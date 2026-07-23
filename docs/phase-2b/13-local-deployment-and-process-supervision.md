# 13 - Local Deployment and Process Supervision

This document outlines how the Phase 2B system is deployed and kept running on a local Windows machine. The goal is to provide a reliable, "always-on" experience for the local user without requiring complex containerization.

## Process Model

Phase 2B relies on three primary components running locally:
1. **The API / Worker Process:** A single Python process running FastAPI (using Uvicorn) that also hosts the asynchronous background Worker loop. (As recommended in Document 02).
2. **The Frontend:** Either a local Vite dev server (in development) or served as static files by the FastAPI process (in production-like local mode).
3. **The Local Model Server:** An external, user-managed process (e.g., Ollama, LM Studio, or vLLM) running on a specific local port (e.g., 11434 or 1234).

> **Note on Docker:** Docker is explicitly *not required* for Phase 2B. The system must run natively via Python to interact cleanly with local workspaces without complex volume mounting.

## Environment Configuration

Configuration is managed via a `.env` file (parsed by `pydantic-settings`):
* `JARVIS_DATABASE_URL`: Path to the SQLite database (e.g., `sqlite:///./data/jarvis.db`).
* `JARVIS_DATA_DIRECTORY`: Path for logs, temporary sandboxes, and outputs.
* `JARVIS_MODEL_PROVIDER_URL`: The URL of the local model server (e.g., `http://localhost:11434/v1`).
* `JARVIS_MODEL_NAME`: The specific model to use (e.g., `llama3.1-8b-instruct`).

## Process Startup and Supervision

To achieve continuous operation, the Python process must be supervised.

### Recommended: Windows Task Scheduler or Simple Batch Script
For the simplest Phase 2B deployment:
* A `start_jarvis.bat` script activates the Python virtual environment and runs `uvicorn app.main:app`.
* A user can configure Windows Task Scheduler to run this script on login.
* The script loops to automatically restart Uvicorn if it crashes unexpectedly:
  ```bat
  :loop
  call venv\Scripts\activate
  uvicorn app.main:app --port 8000
  echo "Jarvis crashed. Restarting in 5 seconds..."
  timeout /t 5
  goto loop
  ```

### Advanced: Windows Service (Optional)
Tools like NSSM (Non-Sucking Service Manager) can wrap the Python process as a background Windows Service, allowing it to start on boot (before login) and automatically restart on failure. This is supported but not strictly required for the Phase 2B definition of done.

## Lifecycle Management

* **Process Startup Order:** The Model Server should ideally start first. However, the Jarvis Worker is resilient; if it starts before the model server, health checks will report `Degraded` and task execution will back off and retry until the model server is available.
* **Graceful Shutdown:** When the user closes the command prompt window or the OS shuts down, Uvicorn receives a SIGINT. The FastAPI lifespan event signals the Worker to cancel. The Worker finishes its current Green step, writes a checkpoint, releases its SQLite lease, and allows the process to exit cleanly.
* **Forced Shutdown:** If the process is killed via Task Manager (SIGKILL) or power loss, the lease remains in the database. Recovery happens automatically upon the next boot when the lease expires (after 30 seconds).

## Edge Cases

* **Port Conflicts:** The API binds strictly to `127.0.0.1:8000` to prevent exposure to the local network. If port 8000 is in use, the supervisor script fails visibly.
* **Laptop Sleep and Wake:** SQLite handles sleep seamlessly. Network connections to the model server may drop, triggering a retryable error in the worker. Leases may expire during sleep; the worker handles this upon waking by reclaiming the task.
* **Storage Exhaustion:** If the `data/` directory disk fills up, SQLite writes will fail. The worker catches `OperationalError`, halts execution, transitions to `Degraded`, and waits for the user to free space.
* **Updates and Rollbacks:** Because state is durably stored in SQLite and heavily checkpointed, updating the codebase simply requires shutting down the API, running `git pull`, running Alembic migrations, and restarting. Rollback requires restoring a SQLite backup.
