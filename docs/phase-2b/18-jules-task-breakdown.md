# 18 - Jules Task Breakdown

This document breaks the Phase 2B implementation into small, independently reviewable tasks suitable for an AI coding agent (like Jules).

## Task 1: Phase 2B Contracts and Schemas
* **Objective:** Define the Pydantic models for tools, structured plans, and execution steps.
* **Scope:** Create `apps/api/app/models/domain.py` additions (or new schema files). Include schemas for `ModelRequest`, `ModelResponse`, `PlanSchema`, and `ToolRequest`.
* **Forbidden Files:** Do not modify existing API routes, database models, or frontend code.
* **Dependencies:** None.
* **Acceptance Criteria:** Pydantic models instantiate correctly and validate expected JSON.
* **Tests:** Unit tests in `tests/test_schemas.py` proving validation and failure modes.
* **Risk Level:** Low.
* **Base Branch:** `feature/phase-2-durable-control-plane` (or `main` if merged).
* **Parallelizable:** Yes (can run alongside DB migrations).

## Task 2: Provider Registry and Fake Provider
* **Objective:** Create the ModelProvider protocol and a deterministic FakeProvider.
* **Scope:** Create `apps/api/app/models/providers/base.py` and `fake.py`.
* **Forbidden Files:** FastAPI routes, SQLite repositories.
* **Dependencies:** Task 1.
* **Acceptance Criteria:** The FakeProvider returns a hardcoded valid JSON plan when `generate_structured` is called with specific test prompts.
* **Tests:** Unit tests verifying FakeProvider behavior.
* **Risk Level:** Low.

## Task 3: Worker Identity and Atomic Claiming
* **Objective:** Implement the SQLite atomic claim query and lease mechanics.
* **Scope:** Modify `apps/api/app/repositories/sqlalchemy.py` to add `claim_task`, `renew_lease`, and `release_task` methods. Update `apps/api/app/db/models.py` to add `worker_id` and `lease_expires_at` to the Task model. Generate an Alembic migration.
* **Forbidden Files:** Frontend code, SimulatorEngine.
* **Dependencies:** Phase 2A database foundation.
* **Acceptance Criteria:** `claim_task` successfully assigns a task and prevents a second call from claiming it until `lease_expires_at` passes.
* **Tests:** Integration tests using a temporary SQLite DB simulating concurrent claims and lease expiration.
* **Risk Level:** Medium (Database locking risks).

## Task 4: Headless Worker Loop
* **Objective:** Build the infinite background polling loop.
* **Scope:** Create `apps/api/app/orchestration/manager.py`. Tie the manager's `start()` and `stop()` to FastAPI's lifespan events in `app/main.py`.
* **Forbidden Files:** SimulatorEngine, ModelProviders.
* **Dependencies:** Task 3.
* **Acceptance Criteria:** The app starts, logs that the worker is polling, and shuts down cleanly on SIGINT without throwing asyncio errors.
* **Tests:** Unit tests mocking the repository to ensure the loop polls and sleeps correctly.
* **Risk Level:** Medium (Asyncio lifecycle issues).

## Task 5: Planning, Execution, and Validation
* **Objective:** Connect the Worker Loop to the Fake Provider and build the execution state machine.
* **Scope:** Create `apps/api/app/workers/executor.py`. Implement the loop that calls the provider, validates the plan, and simulates tool execution.
* **Forbidden Files:** Real external APIs.
* **Dependencies:** Tasks 1, 2, and 4.
* **Acceptance Criteria:** A queued task transitions to `Completed` automatically using the Fake Provider.
* **Tests:** Integration test proving the full state machine transition from Queued -> Completed.
* **Risk Level:** High (Core logic).

## Task 6: Step Checkpointing
* **Objective:** Save execution state to SQLite at defined boundaries.
* **Scope:** Update `executor.py` and `sqlalchemy.py` to write JSON checkpoints before/after tool executions.
* **Forbidden Files:** Frontend code.
* **Dependencies:** Task 5.
* **Acceptance Criteria:** Interrupting the `executor.py` loop and restarting it resumes from the exact checkpoint without re-running previous steps.
* **Tests:** Failure-injection tests proving recovery semantics.
* **Risk Level:** High.

## Task 7: Tool Registry and Safe Tools
* **Objective:** Implement `read_file` and `list_files` tools with path normalization security.
* **Scope:** Create `apps/api/app/tools/registry.py` and `apps/api/app/tools/filesystem.py`.
* **Dependencies:** Task 1.
* **Acceptance Criteria:** `read_file` strictly rejects path traversal attempts.
* **Tests:** Unit tests aggressively trying to break out of the `workspace_root`.
* **Risk Level:** High (Security boundary).
* **Parallelizable:** Yes (can be done anytime after Task 1).

## Task 8: Retry, Failure Classification, and Approvals
* **Objective:** Handle transient errors, max retries, and the Orange/Red approval pause.
* **Scope:** Update `executor.py` to handle exceptions, backoff, and transition to `waiting_for_approval`.
* **Dependencies:** Task 6.
* **Acceptance Criteria:** Simulated 429s trigger retry logic. Orange tools trigger a pause.
* **Tests:** Integration tests verifying backoff timers and approval pausing.
* **Risk Level:** Medium.

## Task 9: End-to-End Unattended Test
* **Objective:** Prove the system works autonomously with a Fake Provider.
* **Scope:** Create `tests/test_unattended_queue.py`.
* **Dependencies:** Tasks 1-8.
* **Acceptance Criteria:** A single pytest script queues 3 varied tasks, runs the worker manager, and asserts the correct final states without hanging.
* **Risk Level:** Low.

## Task 10: OpenAI-Compatible Local Provider
* **Objective:** Implement the real HTTP client for local LLMs.
* **Scope:** Create `apps/api/app/models/providers/openai_compatible.py`.
* **Dependencies:** Task 2.
* **Acceptance Criteria:** The provider can successfully parse a Pydantic schema request into an OpenAI API payload and parse the response back.
* **Tests:** Mocked HTTPX tests verifying JSON payload structure. (Do not require a real LLM in CI).
* **Risk Level:** Medium.
