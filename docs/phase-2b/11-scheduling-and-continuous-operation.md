# 11 - Scheduling and Continuous Operation

This document designs the earliest safe form of continuous operation for the Phase 2B autonomous worker. It ensures the worker can process a backlog of tasks unattended while preventing runaway resource consumption or infinite failure loops.

## Required in Phase 2B

### Continuous Queue Polling
The Worker Manager runs an infinite asynchronous loop (`while not shutdown_event.is_set():`). It polls the SQLite `tasks` table for work.

### Configurable Poll Interval and Backoff
* **Default Interval:** 1 second.
* **Empty Queue Backoff:** If no tasks are found, the interval increases (e.g., 1s -> 2s -> 5s -> max 10s) to reduce database load.
* **Error Backoff:** If a transient error occurs (e.g., local model server timeout), the worker backs off exponentially (e.g., 5s, 10s, 30s) before attempting the task again.

### Resource Limits and Constraints
To prevent an unattended worker from burning CPU/GPU cycles indefinitely:
* **Maximum Task Runtime:** A single task is hard-capped (e.g., 15 minutes). If exceeded, the task is marked `Failed` (Timeout).
* **Maximum Model Calls Per Task:** A task is limited to a strict number of model inferences (e.g., 20 calls). If exceeded, the task requires human intervention.
* **Maximum Retries:** A task can only retry a step/plan a limited number of times (e.g., 3 times) before failing.

### Operation Management
* **Start-on-boot Design:** The worker is designed to be launched via standard OS mechanisms (e.g., a batch script triggered by Windows Task Scheduler on login) without requiring complex container orchestration.
* **Quiet Shutdown:** When the OS shuts down, the worker catches SIGINT/SIGTERM, finishes its current step, releases its task lease, and exits quietly.
* **Overnight Queue Operation:** The worker safely processes the queue overnight. If an Orange/Red action is hit, the worker leaves the task `waiting_for_approval` and proceeds to the next queued task, preventing the entire queue from stalling on a single blocked item.
* **Daily Summary Concept:** (Conceptual for Phase 2B UI) A user waking up should see a summary: "Processed 10 tasks. 8 Completed, 1 Failed, 1 Waiting for Approval."

## Deferred Scheduling Features

The following features are **not** implemented in Phase 2B, but the architecture must not preclude them:
* Cron-like recurring schedules (e.g., "Run this analysis every Monday").
* Conditional monitors (e.g., "Run when a file changes").
* Multi-machine queues or distributed scheduling (e.g., Celery/Redis).
* Complex dependency scheduling (Task B starts only when Task A completes).

## Environmental Considerations

* **Model Server Unavailable:** If Ollama/LM Studio is down, the worker logs the error, backs off, and keeps the task in the queue. It does not fail the task immediately.
* **Memory/Disk Pressure:** If the worker detects disk space is critically low (e.g., < 1GB), it suspends polling and transitions to a `Degraded` health state.
* **Duplicate-run Prevention:** Handled strictly by the SQLite lease mechanism described in Document 05.
* **Laptop Sleep Behavior:** When a Windows laptop sleeps, network connections to local models may drop and timers freeze. Upon wake, leases may have expired. The worker is designed to handle this gracefully by recognizing expired leases and reclaiming them idempotently.
