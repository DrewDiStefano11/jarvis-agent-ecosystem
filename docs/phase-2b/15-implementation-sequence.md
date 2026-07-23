# 15 - Implementation Sequence

This document defines an ordered implementation plan designed to achieve useful autonomy quickly and safely. It ensures foundational contracts are solid before introducing actual LLM inference.

## Stage 1: Contract and Schema Preparation
* **Objective:** Define the Pydantic models for tools, plans, steps, and checkpoints.
* **Files Affected:** `apps/api/app/models/domain.py`, new schema files.
* **Dependencies:** None.
* **Acceptance Criteria:** Pydantic models validate successfully and reject invalid data.
* **Estimated Complexity:** Small.
* **What Must Not Be Included:** Database persistence or worker logic.

## Stage 2: Provider Registry and Fake Model Provider
* **Objective:** Build the ModelProvider protocol and a deterministic FakeProvider for testing.
* **Files Affected:** `apps/api/app/models/providers/*`
* **Dependencies:** Stage 1.
* **Acceptance Criteria:** Fake provider returns hardcoded valid/invalid JSON plans based on prompt keywords.
* **Estimated Complexity:** Small.

## Stage 3: Worker Identity and Atomic Claiming
* **Objective:** Implement the SQLite atomic claim query, lease management, and heartbeat mechanism.
* **Files Affected:** `apps/api/app/repositories/sqlalchemy.py`, `apps/api/app/orchestration/`
* **Dependencies:** Phase 2A database foundation.
* **Acceptance Criteria:** A Python script can claim a task, renew its lease, and another process is prevented from claiming it until it expires.
* **Estimated Complexity:** Medium.

## Stage 4: Headless Worker Loop and State Machine
* **Objective:** Build the infinite background polling loop and integrate it with FastAPI lifecycle events.
* **Files Affected:** `apps/api/app/main.py`, `apps/api/app/workers/`
* **Dependencies:** Stage 3.
* **Acceptance Criteria:** Worker starts with the API, polls for tasks, and shuts down cleanly on SIGINT.
* **Estimated Complexity:** Medium.
* **Risks:** Accidentally blocking the main FastAPI event loop. (Must use `asyncio.sleep` and yield control).

## Stage 5: Planning, Execution, and Validation
* **Objective:** Connect the Worker Loop to the Fake Provider, execute a plan, and validate outputs.
* **Files Affected:** `apps/api/app/validation/`, `apps/api/app/tools/`
* **Dependencies:** Stages 1, 2, and 4.
* **Acceptance Criteria:** A queued task automatically transitions to completed using the Fake Provider. Hallucinated tools fail validation.
* **Estimated Complexity:** Large.

## Stage 6: Checkpointing and Recovery
* **Objective:** Save state to SQLite at defined boundaries.
* **Files Affected:** `apps/api/app/workers/`
* **Dependencies:** Stage 5.
* **Acceptance Criteria:** Killing the worker mid-execution and restarting it results in the worker resuming from the last checkpoint without repeating the first step.
* **Estimated Complexity:** Large.

## Stage 7: Retry and Failure Classification
* **Objective:** Handle transient Fake Provider errors and validation failures.
* **Files Affected:** `apps/api/app/workers/`
* **Dependencies:** Stage 5.
* **Acceptance Criteria:** A simulated 429 error causes a backoff retry. 3 consecutive validation errors cause a terminal task failure.
* **Estimated Complexity:** Medium.

## Stage 8: Safe Unattended Testing (The Milestone)
* **Objective:** Prove the system can run the Unattended Queue Scenario (Document 14).
* **Dependencies:** Stages 1-7.
* **Acceptance Criteria:** The Unattended Queue test passes 100% reliably in CI.
* **Earliest Useful Autonomy Milestone:** Reaching this stage proves the system is safe and durable, even if it only uses a Fake Provider.

## Stage 9: Local Model Provider (OpenAI/Ollama)
* **Objective:** Implement the real HTTP clients for OpenAI-compatible endpoints and Ollama.
* **Files Affected:** `apps/api/app/models/providers/`
* **Dependencies:** Stage 2.
* **Acceptance Criteria:** The worker can successfully generate a plan using a locally running instance of `llama3.1-8b-instruct`.
* **Estimated Complexity:** Medium.

## Stage 10: Health and Observability
* **Objective:** Expose worker metrics and provider status to the API.
* **Files Affected:** `apps/api/app/api/endpoints/health.py`
* **Dependencies:** Stage 4, 9.
* **Acceptance Criteria:** `/health` returns accurate queue depth and model provider reachability.
* **Estimated Complexity:** Small.

## Stage 11: Process Supervision Documentation
* **Objective:** Provide the batch scripts and instructions for running the system continuously on Windows.
* **Files Affected:** `README.md`, `scripts/start_jarvis.bat`
* **Dependencies:** None.
* **Acceptance Criteria:** A user can double-click a `.bat` file to start the API and Worker reliably.
* **Estimated Complexity:** Small.
