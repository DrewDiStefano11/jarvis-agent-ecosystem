# 14 - Testing Strategy and Acceptance Scenarios

This document outlines how Phase 2B will be tested to ensure the autonomous worker is safe, reliable, and recovers correctly from failures without relying on real unrestricted tools during CI.

## Testing Strategy Overview

Phase 2B relies heavily on **Fake Model Providers** and **Isolated SQLite Databases**. Tests must prove the state machine and recovery logic work perfectly without requiring a GPU or a running instance of Ollama/vLLM.

## Unit Tests

Unit tests focus on isolated logic without database interaction.
* **Provider Parsing:** Ensure the adapter correctly translates standard requests into provider-specific schemas (e.g., Ollama formatting).
* **Provider Errors:** Ensure HTTP 429s, timeouts, and connection refused errors correctly map to `RetryableError`. Ensure invalid API keys map to `TerminalError`.
* **Lease Calculations:** Verify time arithmetic for lease expiration and heartbeat renewals.
* **Policy Decisions:** Ensure the Policy Engine correctly categorizes known tools as Green/Yellow/Orange/Red/Black based on the registry.
* **Plan and Step Validation:** Ensure valid JSON passes, missing required fields fail, and hallucinated tools throw explicit errors.
* **Completion Validation:** Ensure a task is not marked complete if it lacks a required Step Result.
* **Path Restrictions:** Ensure the `read_file` tool strictly rejects path traversal strings (`../`, absolute paths outside the root).

## Integration Tests

Integration tests use the FastAPI `TestClient`, a temporary SQLite database, and the deterministic `FakeModelProvider`.
* **End-to-End Success:** Enqueue a task, run the worker loop (with the Fake Provider returning a valid plan), execute the simulated tools, and verify the task reaches `Completed`.
* **Database Persistence:** Verify that the task, checkpoints, and audit logs are correctly written to the test database.
* **Concurrent Claims:** Start two worker loops simultaneously. Prove that only one acquires the lease and executes the task, while the other backs off.
* **Approval Pause and Resume:** Have the Fake Provider request an Orange tool. Verify the worker pauses (`waiting_for_approval`). Simulate a user API call to approve it. Verify the worker resumes and completes.
* **Model Timeout / Unavailable:** Configure the Fake Provider to raise a timeout. Verify the worker transitions to `Retrying`, backs off, and eventually fails the task if max retries are exceeded.
* **Invalid Model Response:** Have the Fake Provider return malformed JSON. Verify the Validator catches it, retries, and properly audits the failure.
* **Graceful Shutdown:** Trigger a shutdown event while the worker is executing. Verify it finishes the current step, writes a checkpoint, and exits cleanly.

## Failure-Injection Tests

These tests forcefully interrupt the system to prove durability.
* **Crash before claim commit:** Raise an exception in the repository layer just before the `UPDATE` commit. Verify the task remains `queued`.
* **Crash after claim commit:** Raise an exception immediately after claiming. Verify the lease expires after the simulated time, and the task is reclaimed.
* **Crash during model request:** Raise an exception inside the Provider. Verify the worker re-requests the plan on restart.
* **Crash before checkpoint:** Raise an exception after a tool executes but before the checkpoint commits. Verify the worker safely re-executes the (Green) tool upon restart.
* **Disk Full:** Mock the SQLAlchemy commit to throw an `OperationalError`. Verify the worker transitions to `Degraded` and does not mark the task complete.

## Acceptance Scenarios

The ultimate proof of Phase 2B is the Unattended Queue Scenario.

### Scenario: The Unattended Queue
**Given** the system is running locally with a deterministic Fake Model Provider.
**And** the user enqueues three tasks:
1. Task A: A safe read-only task (Green).
2. Task B: A task requiring an external API call (Orange).
3. Task C: A task that the model hallucinates an invalid tool for.
**When** the worker loop is started and runs unattended for 60 simulated seconds.
**Then** the final database state must show:
* Task A is `Completed`, with a full audit trail and successful checkpoints.
* Task B is `Waiting for Approval`, having safely paused without blocking the queue.
* Task C is `Failed`, having exhausted retries after structural validation rejected the hallucinated tool.
* The system is `Healthy`, with the worker currently `Idle`.

*Note: Phase 2B tests must never use real, unrestricted tools (like actual arbitrary shell execution) to prevent CI environment compromise.*
