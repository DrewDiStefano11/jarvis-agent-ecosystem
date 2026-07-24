# Repository Validation Matrix

* **Status:** Current validation reference
* **Repository SHA reviewed:** 7bcddc8d964ebc32672ec1e6ecf1a873a7d4af49
* **Verification Date:** 2026-07-24
* **Intended Audience:** Human contributors, Codex, Jules, and future agents
* **Warning:** This document does not replace reviewing the exact diff and current CI results. Validation requirements must be updated as architecture changes.

## 2. Purpose

This document answers four questions for every change:
1. What must be validated?
2. Where must it be validated?
3. What evidence must be reported?
4. What conditions block merge?

* **Green CI is necessary but not sufficient.**
* **Every changed file must be inspected.**
* **Validation must match the risk introduced by the change.**
* **Tests unrelated to the actual failure window do not prove correctness.**
* **Exact tested head SHA must match the PR head SHA.**

## 3. Validation terminology

* **Required:** Must be performed and pass before merge.
* **Conditional:** Required if specific change criteria are met.
* **Recommended:** Advisory check that adds confidence but is not strictly mandatory.
* **Not applicable:** Does not apply to the change and must be explicitly justified.
* **Merge-blocking:** Condition that prevents merging the pull request.
* **Smoke test:** Basic validation of critical functionality.
* **Unit test:** Focused test of an isolated function or component.
* **Integration test:** Validation across boundaries or subsystems.
* **Contract test:** Verification of API, WebSocket, or serialization schemas.
* **Migration test:** Verification of schema upgrades, downgrades, and data integrity.
* **Recovery test:** Validation of system behavior after failure or restart.
* **Restart test:** Ensuring state persistence and safe initialization on application startup.
* **Concurrency test:** Validation of simultaneous operations for data races or deadlocks.
* **Race test:** Focused test to expose timing-dependent failure windows.
* **Fault injection:** Intentional induction of errors to verify resilient behavior.
* **Platform-specific test:** Validation targeting OS-specific behavior (e.g., Windows paths/processes).
* **Static analysis:** Automated checks without executing code (lint, format, typecheck).
* **Full suite:** Execution of all tests within a domain.
* **Focused suite:** Execution of a specific test file or directory.
* **Regression test:** Verification that existing functionality remains unaffected.
* **Adversarial test:** Validation against expected malicious inputs or state tampering.
* **Manual inspection:** Human review of exact implementation, architecture, or behavior.
* **Release evidence:** Artifacts and logs demonstrating successful validation.

## 4. Validation levels

### Level 0 — Documentation-only
For changes that alter only prose and do not affect executable configuration.
* **Typical changes:** README, Markdown docs, architecture diagrams.
* **Minimum checks:** Markdown validation (if available), link/path verification, secret scan, exact changed-file verification, `git diff --check`.
* **Required evidence:** Changed files match exactly the intended prose changes.
* **Reviewer:** Any contributor.
* **Merge-blocking failures:** Extraneous code/config files changed, absolute/machine paths used.

### Level 1 — Isolated low-risk code
For narrow implementation changes with no API, persistence, lifecycle, security, or concurrency impact.
* **Typical changes:** Utility functions, isolated frontend components without state logic.
* **Minimum checks:** Format, lint, focused tests, full relevant package tests, changed-file inspection.
* **Required evidence:** Local command output, exact SHA validation.
* **Reviewer:** Any contributor.
* **Merge-blocking failures:** Format/lint errors, failing unit tests, unexpected scope creep.

### Level 2 — Contract or integration change
For API, WebSocket, frontend/backend, serialization, or cross-component behavior.
* **Typical changes:** API routes, Pydantic models, WebSocket payload formats, frontend API clients.
* **Minimum checks:** Contract tests, backend full suite, frontend typecheck/lint/test/build, runtime response checks, restart checks where stateful.
* **Required evidence:** Proof of backward compatibility or explicit breaking change notice.
* **Reviewer:** Domain expert (Frontend or Backend).
* **Merge-blocking failures:** Unintentional schema breaks, frontend typecheck failure on backend changes.

### Level 3 — Durable-state or lifecycle change
For migrations, repositories, startup/shutdown, task leases, idempotency, outbox, recovery, or persistent workflows.
* **Typical changes:** Alembic migrations, SQLAlchemy models, task state machines.
* **Minimum checks:** Blank database, historical upgrade, restart, rollback/fault injection, audit/outbox consistency, concurrency where applicable.
* **Required evidence:** Database migration output, restart logs, concurrency assertions.
* **Reviewer:** Backend/Architecture lead.
* **Merge-blocking failures:** Migration head conflicts, audit/outbox inconsistencies, recovery failures.

### Level 4 — Security or process-control change
For filesystem sandbox, subprocess launch/termination, credentials, approvals, tool execution, or trust-boundary changes.
* **Typical changes:** Worker supervisor, Context Assembler boundaries, path resolution.
* **Minimum checks:** Adversarial tests, platform-specific tests (Windows), race/failure-window tests, negative authorization tests, secret-leakage checks, independent security review, manual inspection of exact implementation.
* **Required evidence:** Platform test results, security review sign-off.
* **Reviewer:** Architecture lead (Drew).
* **Merge-blocking failures:** Unmanaged child processes, path traversal vulnerabilities, TOCTOU races, unredacted secrets.

## 5. Current repository validation commands

| Validation purpose | Working directory | Exact command | Current CI coverage | Typical duration | Failure meaning | Notes |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Backend format | `apps/api` | `ruff format . --check` | Yes (Windows) | Fast | Code style violation | |
| Backend lint | `apps/api` | `ruff check .` | Yes (Windows) | Fast | Static analysis/import violation | |
| Focused pytest | `apps/api` | `python -m pytest tests/<path>` | No (runs full) | Fast | Specific logic failure | **Current manual validation** |
| Full backend pytest | `apps/api` | `python -m pytest -q` | Yes (Windows) | Moderate | Backend logic regression | |
| Alembic head check | `apps/api` | `python -m alembic current` / `history` | Yes (Windows) | Fast | Missing or conflicting migrations | |
| Blank-database upgrade | `apps/api` | `python -m alembic upgrade head` | Yes (Windows) | Moderate | Schema definitions or migrations are invalid | |
| Application startup | `apps/api` | `python -m uvicorn app.main:app` | No | Moderate | Lifecycle/startup failure | **Current manual validation** |
| Clean shutdown | `apps/api` | Ctrl+C after startup | No | Moderate | Lifespan/cleanup error | **Current manual validation** |
| Frontend typecheck | `apps/web` | `pnpm typecheck` | Yes (Windows) | Moderate | TypeScript compilation failure | |
| Frontend lint | `apps/web` | `pnpm lint` | Yes (Windows) | Fast | ESLint rule violation | |
| Frontend tests | `apps/web` | `pnpm test` | Yes (Windows) | Moderate | Frontend logic regression | |
| Frontend build | `apps/web` | `pnpm build` | Yes (Windows) | Long | Packaging or static asset failure | |
| Documentation lint | Root | *No dedicated command currently verified* | No | N/A | N/A | **Current gap** |
| Secret scanning | Root | *No dedicated command currently verified* | No | N/A | N/A | **Current gap** |
| Conflict markers/diff | Root | `git diff --check` | No | Fast | Unresolved merges or whitespace | **Current manual validation** |
| Windows validation | Root | CI runs on `windows-latest` | Yes | Env-dependent | Platform incompatibility | |

## 6. Core checks required for every pull request

1. Record exact base SHA.
2. Record exact final head SHA.
3. Compare against actual target branch.
4. Inspect exact changed-file list.
5. Inspect every changed file.
6. Confirm no unrelated files.
7. Run `git diff --check`.
8. Search for conflict markers.
9. Check untracked/generated files.
10. Verify no credentials or secrets.
11. Run applicable formatting and lint.
12. Run focused tests.
13. Run broader regression tests.
14. Verify CI on exact head SHA.
15. Check mergeability.
16. Check reviews.
17. Check unresolved review threads.
18. Report limitations and skipped checks.
19. Do not merge without explicit authorization.

*A stale PR base can make unrelated historical files appear in the diff. Ensure the base is up to date.*

## 7. Master validation matrix

**Legend:**
* **R** — Required
* **C** — Conditional
* **A** — Advisory
* **N/A** — Not applicable (only with justification)

### Documentation & Infrastructure
| Change category | Typical paths | Level | Backend format | Backend lint | Focused tests | Full backend suite | Frontend typecheck | Frontend lint | Frontend tests | Frontend build | Secret-leakage scan | Manual review | Required PR evidence |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Documentation | `docs/`, `README.md` | 0 | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | R | R | Scope inspection |
| Public documentation | `docs/` | 0 | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | R | R | Scope inspection |
| Architecture doc | `docs/` | 0 | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | R | R | Scope inspection |
| Comments only | Any | 0 | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | R | R | Scope inspection |
| Python format-only | `apps/api/` | 1 | R | C | C | N/A | N/A | N/A | N/A | N/A | N/A | R | Formatting output |
| Dependency update | `pyproject.toml`, `package.json` | 2/3 | R | R | R | R | R | R | R | R | R | R | Clean install, build |
| CI workflow | `.github/workflows/` | 4 | N/A | N/A | C | C | C | C | C | R | R | R | Workflow run logic |
| Test-only change | `apps/api/tests/`, `apps/web/tests/` | 1 | R | R | R | C | C | C | C | C | N/A | R | Test stability |
| Performance optim. | Any | 1/2 | R | R | R | R | C | C | C | C | N/A | R | Benchmark data |

### Backend API & Logic
| Change category | Typical paths | Level | Backend format | Backend lint | Focused tests | Full backend suite | API/OpenAPI contract | WebSocket contract | Manual review | Required PR evidence |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Backend utility | `apps/api/app/` | 1 | R | R | R | C | N/A | N/A | R | Test results |
| API route | `apps/api/app/api/` | 2 | R | R | R | R | R | N/A | R | Contract compat |
| Request/response model | `apps/api/app/models/` | 2 | R | R | R | R | R | N/A | R | Contract compat |
| OpenAPI metadata | `apps/api/app/main.py` | 2 | R | R | C | R | R | N/A | R | OpenAPI check |
| Health/status endpoint | `apps/api/app/api/` | 2 | R | R | R | C | R | N/A | R | Route check |

### Persistence & State
| Change category | Typical paths | Level | Blank-database migration | Historical upgrade | Downgrade/re-upgrade | Restart/persistence | Recovery test | Concurrency/race test | Fault injection | Audit consistency | Outbox consistency | Idempotency replay |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Repository method | `apps/api/app/repositories/` | 3 | N/A | N/A | N/A | C | C | C | C | C | C | C |
| Database model | `apps/api/app/db/` | 3 | R | R | A | R | R | R | C | C | C | C |
| Alembic migration | `apps/api/migrations/` | 3 | R | R | C | R | R | N/A | N/A | N/A | N/A | N/A |
| Backup/restore logic | `apps/api/app/services/` | 4 | C | C | N/A | R | R | C | R | N/A | N/A | N/A |
| Reset/destructive maintenance | `apps/api/app/services/` | 4 | N/A | N/A | N/A | C | C | C | C | C | C | C |
| Seed/bootstrap logic | `apps/api/app/services/seed.py` | 3 | R | C | N/A | R | C | C | C | N/A | N/A | C |
| Repository snapshot | `apps/api/app/repositories/` | 3 | N/A | N/A | N/A | R | R | C | C | N/A | N/A | N/A |
| Idempotency | `apps/api/app/main.py`, `apps/api/app/services/` | 3 | C | C | N/A | R | R | R | R | C | C | R |
| Transactional outbox | `apps/api/app/services/events.py` | 3 | R | R | N/A | R | R | R | R | R | R | C |
| Audit system | `apps/api/app/services/events.py` | 3 | R | R | N/A | R | R | R | R | R | R | C |

### Realtime & Workflows
| Change category | Typical paths | Level | WebSocket contract | Audit consistency | Outbox consistency | Restart/persistence | Concurrency/race test | Fault injection |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| WebSocket broker | `apps/api/app/api/` | 2/3 | R | N/A | N/A | R | C | C |
| WebSocket payload | `apps/api/app/models/` | 2 | R | N/A | N/A | C | N/A | N/A |
| Task state | `apps/api/app/models/` | 3 | R | R | R | R | R | C |
| Workflow/checkpoint logic | `apps/api/app/simulator/` | 3 | R | R | R | R | C | R |
| Task leases | `apps/api/app/repositories/task_leases.py` | 3 | C | R | R | R | R | R |
| Worker records | `apps/api/app/models/` | 3 | C | C | C | R | R | C |
| Application startup/shutdown | `apps/api/app/main.py` | 3 | N/A | N/A | N/A | R | R | C | R |

### Process Control, Security & Platform
| Change category | Typical paths | Level | Windows-specific test | Security/adversarial test | Concurrency/race test | Fault injection | Restart/persistence | Manual review |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Worker supervisor | `prototypes/phase-2b-worker-supervisor/` | 4 | R | R | R | R | R | R |
| Subprocess launch | `apps/api/app/` | 4 | R | R | R | R | C | R |
| Process termination| `apps/api/app/` | 4 | R | R | R | R | C | R |
| Filesystem sandbox | `apps/api/app/filesystem/` | 4 | R | R | R | R | C | R |
| File path resolution | `apps/api/app/filesystem/` | 4 | R | R | R | R | C | R |
| Cross-platform behavior | Any | 4 | R | C | C | C | C | R |
| Approvals | `apps/api/app/services/` | 4 | C | R | R | R | R | R |
| Emergency stop | `apps/api/app/services/` | 4 | C | R | R | R | R | R |
| Context Assembler | `apps/api/app/context/` | 4 | C | R | C | R | R | R |
| Trust/provenance rules | `apps/api/app/context/` | 4 | N/A | R | N/A | C | N/A | R |
| Credential redaction | `apps/api/app/context/` | 4 | N/A | R | N/A | C | N/A | R |

### External Integrations
| Change category | Typical paths | Level | API/OpenAPI contract | Security/adversarial test | Fault injection | Manual review |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Tool adapter | `apps/api/app/` | 2 | R | C | C | R |
| Email integration | `apps/api/app/` | 2 | R | C | C | R |

### Frontend Code
| Change category | Typical paths | Level | Frontend typecheck | Frontend lint | Frontend tests | Frontend build | Manual review | Required PR evidence |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Frontend API client | `apps/web/src/` | 2 | R | R | R | R | R | Tests/build |
| Frontend state store| `apps/web/src/` | 2 | R | R | R | R | R | Tests/build |
| Frontend component | `apps/web/src/` | 1 | R | R | R | R | R | Tests/build |
| Frontend routing | `apps/web/src/` | 1/2 | R | R | R | R | R | Tests/build |
| Build configuration | `apps/web/vite.config.ts` | 2 | R | R | R | R | R | Build |

## 8. Backend code validation rules

Ordinary backend changes require:
* Format and lint;
* Direct unit tests;
* Neighboring subsystem tests;
* Full backend suite when shared infrastructure changes;
* No unrelated test rewriting;
* Deterministic assertions;
* Failure-path coverage;
* Resource cleanup.

**Frontend validation is required for backend-only changes when:**
* API response shape changes;
* WebSocket shape changes;
* Enum values change;
* Required/optional fields change;
* Error envelopes change;
* Identifier types change;
* Timestamp representations change.

## 9. API and OpenAPI validation rules

For route, request, response, or schema changes, require:
* Route inventory;
* Request validation;
* Runtime response validation;
* OpenAPI generation;
* Operation-ID uniqueness;
* Success status codes;
* Error status codes;
* Frontend-consumed field verification;
* Frontend full validation;
* Empty-state response;
* Restart response where persistent.

Require an explicit compatibility statement: backward compatible, intentionally breaking, internal-only, or not yet consumed. A breaking change must not be hidden as refactoring.

## 10. WebSocket validation rules

Require validation for:
* Endpoint connection;
* Initial snapshot;
* Event envelope;
* JSON-safe payload;
* Multiple clients;
* Reconnect;
* Disconnect cleanup;
* Ordering;
* Duplicate suppression;
* Slow/failing client isolation;
* Frontend compatibility;
* Secret leakage.

A repository or serialization change requires WebSocket tests even if the WebSocket module itself did not change, if it affects the data pushed to clients.

## 11. Persistence and repository validation rules

For repository and durable-state changes, require:
* Transaction boundary tests;
* Rollback tests;
* Restart persistence;
* Detached returned data;
* Foreign-key integrity;
* Uniqueness constraints;
* Audit/outbox consistency;
* Idempotency consistency where applicable;
* Concurrent access where applicable.

Prohibit completion claims based only on in-memory tests.

## 12. Migration validation rules

For every migration change, require:
* Migration-chain inspection;
* Unique revision ID;
* Correct `down_revision`;
* One Alembic head;
* Blank database to head;
* Upgrade from immediately previous revision;
* Upgrade from relevant historical revision;
* Representative existing data preservation;
* Foreign-key and index verification;
* Downgrade/re-upgrade where supported;
* Application startup after migration;
* Health schema-current result;
* Windows file-handle cleanup for SQLite;
* No migration stamping as a substitute for applying schema changes.

Downgrade testing is advisory (rather than required) for irreversible data transforms, but downgrade limitations must be documented.

## 13. Application lifecycle validation rules

For startup/shutdown changes, require:
* Normal startup;
* Clean shutdown;
* Startup failure;
* Partial startup cleanup;
* Background-task cancellation;
* Engine/session disposal;
* Repeated app creation;
* Multiple app-instance isolation;
* Pending outbox recovery;
* Expired-lease recovery;
* Simulator/workflow recovery;
* No false clean-shutdown marking;
* No leaked asyncio tasks.

Require lifespan tests if FastAPI lifecycle code changes.

## 14. Idempotency validation rules

Require:
* First execution;
* Exact replay;
* Same key/different payload;
* Same key/different command;
* Validation failure;
* Business-rule failure;
* Failure after reservation;
* Transaction rollback;
* Concurrent identical requests;
* Concurrent conflicting requests;
* Restart replay;
* Audit consistency;
* Outbox consistency;
* No duplicate WebSocket success event.

Sequential replay alone does not prove concurrency safety.

## 15. Transactional outbox validation rules

Require:
* Domain + outbox atomicity;
* Rollback creates no event;
* Outbox insertion failure rolls back domain;
* Publication failure remains retryable;
* Publication-status update failure;
* Stable event identity;
* Restart recovery;
* Already-published restart behavior;
* Ordering;
* Batching;
* Concurrent dispatcher behavior;
* Subscriber failure isolation;
* Shutdown during publication;
* Idempotent replay produces no duplicate logical event.

Require the PR to state actual delivery semantics (e.g., at-most-once, at-least-once, effectively-once, unknown). Prohibit unverified claims of exactly-once delivery.

## 16. Audit validation rules

Require:
* Correct actor, target, old/new state, and outcome;
* Rollback creates no false success;
* Idempotent replay does not duplicate success audit;
* Restart persistence;
* Historical immutability;
* JSON-safe payload;
* Secret and token leakage checks;
* Audit/outbox linkage;
* Audit/domain consistency;
* Correlation and causation verification.

Audit integrity tests do not prove cryptographic tamper resistance.

## 17. Task and workflow validation rules

Require:
* Legal and illegal transitions;
* Terminal-state protection;
* Idempotency;
* Restart persistence;
* Workflow checkpoint consistency;
* Pause/resume and interrupted recovery;
* Audit/outbox events;
* Frontend/WebSocket updates;
* Concurrency for mutually exclusive transitions (e.g., complete vs cancel, pause vs finish, recovery vs normal completion).

## 18. Task-lease validation rules

Require tests for:
* Acquisition and exactly one active lease;
* Competing workers;
* Renewal, expiration, and release;
* Completion, failure, and retries (including maximum attempts);
* Wrong worker, stale token, and stale completion rejection;
* Recovery idempotence and restart;
* Concurrent renewal/recovery and completion/recovery;
* Transaction rollback;
* Audit/outbox consistency;
* Token confidentiality.

PID existence is not lease authority. Worker-supervisor process state cannot replace durable lease state.

## 19. Worker-supervisor and process-control validation rules

For subprocess changes, require:
* Launch success;
* Launch failure before process creation;
* Failure after process creation;
* Database-registration failure after launch;
* Process-identity validation;
* PID reuse protection;
* Create-time mismatch;
* Graceful and forced termination;
* Already-exited process handling;
* Supervisor restart;
* Unmanaged-child cleanup and orphan detection;
* Stdout/stderr handling;
* Windows behavior;
* No command injection;
* Exact executable/argument handling;
* No task-state mutation outside control plane.

**Critical failure window:** A child process may already exist when later registration or identity capture fails. Require tests proving that such a child is terminated or safely reconciled.

## 20. Filesystem-security validation rules

For filesystem or path-policy changes, require:
* Allowed-root enforcement and traversal rejection;
* Absolute-path handling;
* Symlink and Windows junction/reparse-point handling;
* Path replacement race and parent-directory replacement race testing;
* File type restrictions;
* Create/write/read/delete separation;
* Permission errors and atomicity;
* Cleanup and secret path leakage checks;
* Platform-specific behavior;
* Adversarial review.

Lexical normalization alone is insufficient. Resolving a path before use does not automatically eliminate TOCTOU races. Windows reparse points require explicit attention. Security-sensitive filesystem changes require independent review.

## 21. Context Assembler validation rules

Require:
* Source-type/trust compatibility;
* Provenance hashes and source approval;
* Project isolation and credential redaction;
* Injection detection and boundary delimiter attacks;
* Deterministic deduplication, ordering, token budgeting, truncation, and minimum context;
* Review-required behavior and `modelRequest` withholding;
* Persistence, idempotency, audit/outbox events, restart;
* JSON-safe WebSocket/API shape;
* No raw secret or matched forbidden excerpt leakage.

Require explicit separation between trusted system/developer instructions, untrusted context, and security-analysis exceptions.

## 22. Approval and emergency-stop validation rules

For approvals, require:
* Request creation, authorized/unauthorized decisions;
* Duplicate and conflicting decisions;
* Blocked-operation enforcement, idempotency, audit/outbox consistency.

For emergency stop, require:
* Activation/repeated activation, clearing/repeated clearing;
* Restart persistence;
* Operation blocking;
* Effect on active tasks, leases, and workflows per current policy;
* Frontend/WebSocket update;
* Audit history retained.

Do not assume emergency stop terminates OS processes unless implementation proves it.

## 23. Frontend validation rules

For frontend changes, require:
* Typecheck, lint, focused tests, full tests, production build;
* Loading/error/empty states;
* Backend contract compatibility;
* Reconnect behavior where real-time state is involved;
* No direct assumption that client state is authoritative;
* No secret exposure;
* Responsive behavior if UI layout changes.

For state-management changes, require reducer/store tests, duplicate event handling, stale snapshot handling, reconnect replacement behavior, and cross-project state isolation where applicable.

## 24. Dependency-update validation rules

For dependency changes, require:
* Lockfile consistency and clean install;
* Format/lint, full backend, full frontend, production build;
* Migration and application startup;
* Security implications, license notes, API behavior on version changes, and deprecation-warning review.

Require focused subsystem regression tests for updates to FastAPI, Pydantic, SQLAlchemy, Alembic, SQLite drivers, WebSockets, process, and filesystem libraries.

## 25. CI-workflow validation rules

For `.github/workflows/` changes, require:
* YAML validity;
* Workflow trigger, permissions, secret exposure, and path-filter reviews;
* Command parity with local development;
* Cache correctness, artifact handling, matrix coverage, and failure behavior;
* Branch-protection implications.

A workflow must not be weakened merely to make a PR green. Require independent confirmation that important jobs were not skipped.

## 26. Test-only change validation rules

Test-only PRs must be reviewed for:
* Whether the tests test production behavior rather than duplicated implementation;
* Deterministic assertions and exact failures;
* Narrow monkeypatches and bounded timeouts;
* Cleaned-up resources and platform compatibility;
* Tests accidentally changing global state;
* False passes due to mocking;
* Clear reporting of discovered production defects.

A test-only PR with intentionally failing tests must be marked draft or blocked.

## 27. Documentation-only validation rules

Require:
* Exactly intended files changed and `git diff --check`;
* Markdown validation and relative-link checks (if available);
* Repository path verification;
* Current vs future labeling;
* No secret or machine-specific paths;
* No unsupported claims;
* No accidental code/configuration changes.

Documentation-only does not exempt the PR from SHA and changed-file verification.

## 28. Performance-change validation rules

For optimizations, require:
* Correctness parity and focused regression tests;
* Detached data and side-effect checks;
* Representative benchmark with methodology;
* Old/new comparison;
* No fragile timing assertions in ordinary tests;
* Memory behavior;
* Frontend/API compatibility;
* Restart and persistence if state code changed.

Prohibit performance claims without measurements.

## 29. Backup, restore, reset, and destructive-maintenance validation rules

Require:
* Temporary databases only;
* Backup integrity and restored application startup;
* Alembic revision and representative data verification;
* Foreign-key integrity;
* Outbox pending-state and audit history behavior;
* Bootstrap duplication check;
* Explicit distinction between safe recovery and destructive reset;
* Owner authorization for destructive actions.

Deleting a database is not a valid substitute for migration or recovery testing.

## 30. Windows-specific validation matrix

| Change type | Windows-specific risk | Required test | Evidence | Merge-blocking condition |
| :--- | :--- | :--- | :--- | :--- |
| Subprocess launch | Shell command handling | CI Windows test | Green CI run | Launch failure on Win |
| Process termination| Different process tree | Process cleanup test | Process logs/CI | Zombie process on Win |
| Process identity | PID handling | ID capture test | Exact process check | Wrong PID captured |
| SQLite locking | File locking differences | Concurrency test | Passing DB test | DB Locked errors |
| Temp DB cleanup | Deletion with open handles | Startup/teardown test | Clean log | File in use error |
| Path resolution | Separators, case, reserved | Path test with \ and / | Explicit path test | Invalid path error |
| Symlinks/junctions | Reparse points | Symlink test | Valid read/write | Traversal/access failure|
| Long paths | MAX_PATH limits | Long path generation | Success log | Path too long error |
| Frontend paths | Vite/pnpm build paths | `pnpm build` on Windows | CI build artifact | Build fails on Win |
| Asyncio loop | Proactor vs Selector | Lifecycle tests | Clean exit | Event loop error |

Linux-only success is insufficient for features expected to run on the user's Windows machine.

## 31. Concurrency and race-test decision table

| Subsystem | Expected invariant | Sync approach | Final-state assertions |
| :--- | :--- | :--- | :--- |
| Duplicate API cmds | One mutation applied | Idempotency key | One outbox event, exact replica returned |
| Task acquisition | One worker acquires lease | DB explicit locking | Only one worker holds valid token |
| Lease renewal/rec. | Renewed or fenced | Conditional update | Lease extended OR token rejected |
| Approval decisions | Only one valid decision | Unique state check | Final approved/rejected, no dup |
| Terminal task trans| Cannot complete & cancel | Status precondition | Single terminal state |
| Outbox dispatch | Message sent once | DB lock/batching | Dispatched flag true, no dup broadcast |
| Audit insertion | Valid history | Sequential writes | Correct historical order, no orphan |
| Bootstrap seeding | Base data exists | Startup lock/check | Exact rows exist without duplicate |
| Startup migration | DB reaches head | External script | Head reached, no concurrent upgrade error |
| Filesystem ops | Atomic write/replace | Temp file + rename | Valid complete file OR original file |
| Process regist. | Verified PID | OS process query | Registered PID matches OS running |
| WebSock broadcast | Ordered delivery | Sequence numbers | Clients received exact matching sequence |

Sequential repeated execution is not a race test.

## 32. Fault-injection decision table

| Failure point | Rollback/recovery behavior | Records must/must not exist | Evidence required |
| :--- | :--- | :--- | :--- |
| Validation | Immediate HTTP 422 | No DB or Outbox records | Test log with 422 |
| Domain write | DB constraint/rollback | No partial domain or audit records| Rollback assertion |
| Audit insertion | Full transaction rollback| No domain records | Transaction trace |
| Outbox insertion | Full transaction rollback| No domain or audit records | Rollback trace |
| Idempotency finish | Operation rolls back | Idempotency key exists as pending | Failed status check |
| Serialization | 500 error | Records exist, but no response | Error log |
| Publication | Event remains pending | Domain/Audit exists, Outbox pending| Retry mechanism log |
| Publication status| May duplicate broadcast| Message dispatched, status pending| Duplicate mitigation log|
| Startup / Shutdown | Safe cleanup | No orphaned DB connections/processes| Clean process exit |
| Subprocess launch | Process not spawned | No worker lease or PID | Error propagation log |
| Post-launch regist.| Orphan process killed | Process terminated, no DB entry | Teardown verification |
| Path resolution | Access denied | File remains untouched | Auth exception |
| Backup/Restore | Safely aborts | DB remains in original state | Checksum/state verif. |

## 33. Required pull-request evidence

Every substantive PR should provide the following template in the description:

```markdown
### PR Validation Evidence
- **PR Number:**
- **Title:**
- **Branch:**
- **Exact Base SHA:**
- **Exact Tested Head SHA:**
- **Exact GitHub Head SHA:**
- **Exact Changed-File List:**
- **Validation Level:**
- **Commands Run:**
- **Focused Test Results:**
- **Full-Suite Results:**
- **Frontend Results:**
- **Migration Results:**
- **Windows Results:**
- **Concurrency Results:**
- **Fault-Injection Results:**
- **Security Results:**
- **CI Status:**
- **Mergeability:**
- **Reviews:**
- **Unresolved Threads:**
- **Skipped Checks & Reasons:**
- **Known Limitations:**
- **Production Defects Found:**
- **Confirmation Not Merged:** [ ] Yes
```

## 34. “Not applicable” rules

To mark a check N/A, a contributor must state:
* Which check;
* Why it does not apply;
* Which changed files were inspected;
* Which contract or subsystem is unaffected;
* What alternative evidence supports that conclusion.

**Invalid N/A examples:**
* “No frontend files changed” when API schemas changed.
* “No migration file changed” when database models changed.
* “No Windows test needed” when process or filesystem behavior changed.
* “No concurrency test needed” for idempotency or leasing.
* “No security test needed” for path handling or credential redaction.
* “No restart test needed” for persistent-state changes.

## 35. Merge-blocking conditions

The following conditions block merge:
* Failing required checks;
* Required check not run;
* Tested SHA differs from PR head;
* Unexpected changed files;
* Stale base causing unrelated diff;
* Unresolved review threads;
* Unreviewed production defect;
* Migration chain ambiguity or multiple Alembic heads;
* Frontend/backend contract mismatch;
* Duplicate active lease;
* False successful audit/outbox state;
* Sensitive data leakage;
* Unmanaged child-process risk;
* Unresolved filesystem race;
* Flaky critical concurrency tests;
* Incomplete rollback;
* Generated files committed accidentally;
* Green CI with skipped required platform job;
* Merge authorization not provided.

Passing CI cannot override a merge-blocking architectural or security issue.

## 36. Validation gaps currently present

**Current gaps identified:**
* **No dedicated documentation linting:** Root level checks missing; recommended Markdown linting integration.
* **No dedicated secret scanning:** Recommended future validation to run automated secret scans in CI.
* **No Windows end-to-end load tests:** Current tests are integration/unit level; a formal load testing approach is missing.
* **No browser end-to-end test:** Relies on frontend unit tests and manual preview. Playwright or Cypress recommended.
* **No multi-process database concurrency test:** Current tests are single-process concurrency. Needed for multi-worker environments.
* **No external backup/restore tool integration test:** Recommended for realistic disaster recovery.
* **No filesystem reparse-point specific test:** Mentioned in rules but lacks explicit automated coverage in current CI.

These gaps indicate areas where manual review must be heightened until automated validation is built.

## 37. Recommended validation order

1. Inspect diff and scope.
2. Run formatting/lint.
3. Run focused tests.
4. Run failure-path tests.
5. Run subsystem integration tests.
6. Run restart/migration checks.
7. Run frontend checks.
8. Run platform-specific checks.
9. Run full suite.
10. Verify GitHub head and CI.
11. Inspect reviews and threads.
12. Produce completion report.

Expensive broad tests should not be the first signal when a focused test can fail faster.

## 38. Reviewer decision checklist

- [ ] Does the validation level match the risk?
- [ ] Were exact commands reported?
- [ ] Were failure windows tested?
- [ ] Was the exact head SHA tested?
- [ ] Are API/frontend contracts preserved?
- [ ] Are migration/restart checks sufficient?
- [ ] Were concurrency races tested where mandatory?
- [ ] Was Windows tested where required?
- [ ] Are audit/outbox/idempotency records consistent?
- [ ] Are secrets protected?
- [ ] Are prototypes isolated?
- [ ] Are limitations clearly stated?
- [ ] Are all required reviews complete?
- [ ] Has Drew explicitly authorized merge?
