# Repository Validation Matrix

## 1. Purpose and scope

This matrix defines the minimum evidence needed before a change can be recommended for merge.

**Important principles:**
* **Passing CI is necessary but may not be sufficient.**
* **Mergeability does not equal correctness.**
* **This matrix does not grant merge authorization.**
* **The exact branch state and head SHA must be reviewed.**
* **Validation requirements increase with the risk of the change.**
* **Independently discovered defects must not be hidden by weakening tests.**

## 2. Terminology

* **Focused test:** A test covering the specific code modified by the change.
* **Full suite:** Execution of all automated tests in the relevant domain.
* **Contract test:** A test verifying exact public API, WebSocket, or serialization schemas.
* **Integration test:** Validation across boundaries. A public-command integration test must use the real public command path rather than directly inserting repository rows.
* **Persistence test:** A test verifying that state is safely written to the durable control-plane SQLite database.
* **Rollback test:** A test that verifies durable state after an injected failure, not merely expecting an exception.
* **Restart test:** A test that must fully stop and reconstruct the relevant application or process to ensure safe initialization and state persistence.
* **Recovery test:** Validation of system behavior after unexpected termination.
* **Concurrency test:** A test executing overlapping operations using independent sessions or clients.
* **Race test:** Focused test designed to expose timing-dependent failure windows.
* **Adversarial test:** Validation against expected malicious inputs, edge cases, or unauthorized actions.
* **Fault injection:** Intentional induction of errors to verify resilient behavior.
* **Platform-specific validation:** Validation targeting specific OS environments, notably Windows file and process constraints.
* **Static analysis:** Automated checks without executing code (lint, format, typecheck).
* **Manual inspection:** Human review of exact implementation, architecture, or behavior.
* **Release evidence:** Artifacts and logs demonstrating successful validation.

**Note:** A simulated test must not be presented as proof of production behavior.

## 3. Risk levels

### Level 0 — Documentation-only, non-operational

**Examples:**
* spelling corrections;
* explanatory prose;
* non-executable diagrams;
* historical notes.

**Minimum validation:**
* exact changed-file inspection;
* Markdown and path review;
* link checking where practical;
* secret and personal-data inspection;
* `git diff --check`.

*Note: Purely non-operational documentation should normally be Level 0. Executable documentation, scripts embedded in docs, operational runbooks, security guidance, migration procedures, or commands capable of affecting state may require Level 1 or higher based on risk.*

### Level 1 — Isolated low-risk implementation

**Examples:**
* pure utility functions;
* isolated presentation components;
* non-stateful helpers.

**Minimum validation:**
* format;
* lint;
* focused tests;
* related package tests;
* exact diff review.

### Level 2 — Public contract or integration behavior

**Examples:**
* API routes;
* request and response models;
* OpenAPI behavior;
* WebSocket contracts;
* frontend/backend integration;
* serialization behavior.

**Minimum validation:**
* contract tests;
* representative runtime validation;
* full relevant suites;
* frontend checks where contracts are consumed;
* compatibility assessment;
* restart testing where state or caches are involved.

### Level 3 — Durable state and lifecycle

**Examples:**
* database models;
* Alembic migrations;
* repositories;
* idempotency;
* transactional outbox;
* task leases;
* workflows and checkpoints;
* startup and shut-down behavior.

**Minimum validation:**
* blank-database migration;
* historical upgrade;
* rollback or fault injection;
* restart persistence;
* audit and outbox consistency;
* concurrency where applicable;
* exact database targeting;
* full backend suite.

### Level 4 — Security, execution, or process control

**Examples:**
* filesystem sandbox;
* subprocess launch and termination;
* worker supervisor;
* authorization;
* approvals controlling external effects;
* credentials;
* tool execution;
* trust-boundary changes.

**Minimum validation:**
* adversarial testing;
* negative tests;
* platform-specific testing;
* race and failure-window testing;
* independent manual security review;
* secret-leakage inspection;
* lifecycle cleanup proof;
* explicit owner review.

## 4. Current repository command table

The following exact commands are verified for the current repository architecture.

### Backend

Working directory: `apps/api`

| Command | Status | Notes |
| :--- | :--- | :--- |
| `ruff format . --check` | Run by CI | Required for format validation |
| `ruff check .` | Run by CI | Required for linting |
| `python -m pytest -q` | Run by CI | Backend unit and integration suite |
| `python -m alembic upgrade head` | Run by CI | Migration to head |
| `python -m alembic current` | Run by CI | Verifies current revision |
| `python -m alembic heads` | Manual | Verifies no split heads |

**Important:** Migration commands must target an explicitly verified temporary or intended database. Never recommend running migration validation against an unidentified development database.

### Frontend

Working directory: `apps/web`

| Command | Status | Notes |
| :--- | :--- | :--- |
| `pnpm install --frozen-lockfile` | Run by CI | Installs exact dependencies |
| `pnpm typecheck` | Run by CI | Verifies TypeScript types |
| `pnpm lint` | Run by CI | Frontend linting |
| `pnpm test` | Run by CI | Frontend test suite |
| `pnpm build` | Run by CI | Verifies production bundle creation |

### Repository-wide checks

Working directory: Repository root

| Command | Status | Notes |
| :--- | :--- | :--- |
| `git diff --check` | Manual | Checks for whitespace errors |
| `git status --short` | Manual | Verifies clean working tree |
| `git diff --name-status <base>...HEAD` | Manual | Verifies exact changed files |

## 5. Master validation matrix

* **R** — Required
* **C** — Conditional, with reason documented
* **A** — Advisory
* **N/A** — Not applicable, with explicit justification. (Do not mark N/A merely because it is inconvenient).

### Documentation and repository process

| Category | Risk | Focused tests | Migration | Manual inspection | Merge blockers |
| :--- | :--- | :--- | :--- | :--- | :--- |
| simple documentation | Level 0 | N/A | N/A | R | Secrets, inaccurate paths |
| operational runbook | Level 1 | N/A | N/A | R | Dangerous untested commands |
| architecture documentation | Level 0 | N/A | N/A | R | Contradictions |
| contributor guidance | Level 0 | N/A | N/A | R | Contradictions |
| pull-request template | Level 0 | N/A | N/A | R | Formatting errors |
| CI workflow | Level 4 | C | N/A | R | Dropped checks, fake success |
| dependency updates | Level 2 | R | C | R | Suite failure |
| formatting-only changes | Level 1 | N/A | N/A | R | Unintended logic changes |
| test-only changes | Risk-based| R | C | R | Hidden defects (see Policy) |

### Backend API and contracts

| Category | Risk | Focused tests | Backend suite | Frontend validation | Merge blockers |
| :--- | :--- | :--- | :--- | :--- | :--- |
| route changes | Level 2 | R | R | R | Contract breakage |
| request/response models| Level 2 | R | R | R | Secret leakage |
| OpenAPI metadata | Level 1 | R | R | R | Missing fields |
| health/status endpoints| Level 2 | R | R | A | False success |
| error handling | Level 2 | R | R | C | Unhandled 500s |
| WebSocket protocol | Level 2 | R | R | R | Serialization failure |
| event-envelope changes | Level 2 | R | R | R | Contract breakage |

### Persistence and lifecycle

| Category | Risk | Migration | Rollback test | Concurrency test | Restart test | Full suite | Merge blockers |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| SQLAlchemy model | Level 3 | R | C | C | C | R | Missing fields, bad migration |
| Alembic migration | Level 3 | R | N/A | N/A | N/A | R | Split heads, failure on empty DB |
| repository method | Level 3 | N/A | R | C | C | R | Data corruption |
| unit-of-work behavior | Level 3 | N/A | R | C | C | R | Partial commits |
| idempotency | Level 3 | N/A | R | R | R | R | Duplicate execution |
| audit | Level 3 | N/A | R | C | N/A| R | Missing records |
| transactional outbox | Level 3 | N/A | R | R | R | R | Lost events |
| task state | Level 3 | N/A | R | C | R | R | Invalid transitions |
| task leases and attempts| Level 3 | N/A | R | R | R | R | Race conditions |
| workflow checkpoints | Level 3 | N/A | R | C | R | R | Resumption failure |
| startup | Level 3 | C | N/A | N/A | R | R | Crash on boot |
| shut-down | Level 3 | N/A | N/A | N/A | R | R | Leaked resources |
| recovery | Level 3 | N/A | R | C | R | R | Incorrect state after failure |

### Security and process control

| Category | Risk | Adversarial test | Platform test | Manual review | Lifecycle cleanup | Merge blockers |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| filesystem paths | Level 4 | R | R | R | N/A | Traversal vulnerability |
| sandbox behavior | Level 4 | R | R | R | R | Escapes |
| subprocess launch | Level 4 | R | R | R | R | Zombies, unhandled output |
| process identity | Level 4 | R | R | R | N/A | Wrong PID targeted |
| worker supervisor | Level 4 | R | R | R | R | Missing heartbeats |
| authorization | Level 4 | R | N/A | R | N/A | Bypass |
| approval enforcement | Level 4 | R | N/A | R | N/A | Unapproved execution |
| tool adapters | Level 4 | R | C | R | C | Unsafe input handling |
| secret handling | Level 4 | R | N/A | R | N/A | Leakage in logs/API |
| external execution | Level 4 | R | R | R | R | Unbounded execution |

### Frontend

| Category | Risk | Frontend test | Typecheck | Lint | Build | Merge blockers |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| components without state | Level 1 | R | R | R | R | Visual regressions |
| shared state/store | Level 2 | R | R | R | R | Stale data |
| API client | Level 2 | R | R | R | R | Contract mismatch |
| contract types | Level 2 | R | R | R | R | Type errors |
| WebSocket client | Level 2 | R | R | R | R | Disconnect loops |
| routing | Level 2 | R | R | R | R | Dead links |
| build configuration | Level 2 | N/A | R | R | R | Build failure |

## 6. Test-only change policy

Do not automatically classify every test-only PR as Level 3. Classify test-only work according to what the tests exercise and the risk they introduce.

**Examples:**
* a typo in a test name may be Level 0 or Level 1;
* an isolated unit-test addition may be Level 1;
* API-contract tests may be Level 2;
* migration, rollback, outbox, idempotency, or lease tests may be Level 3;
* filesystem, subprocess, or authorization adversarial tests may be Level 4.

**Test quality review:**
State that test quality itself must be reviewed. A green test suite is not sufficient when tests:
* bypass public command paths;
* directly insert repository state while claiming integration coverage;
* contain vacuous assertions;
* conditionally skip their principal assertion;
* simulate behavior instead of exercising it;
* hide a production defect through normalization;
* depend on test execution order;
* use the wrong database;
* contain unbounded waits;
* mark unexpected failures as acceptable.

## 7. Defect-preservation policy

When a new test accurately exposes a production defect:
* preserve a focused deterministic failing test;
* explain the expected and actual behavior;
* do not sort, normalize, mock away, or weaken the assertion merely to obtain green CI;
* do not mark a non-deterministic defect as a passing simulation;
* keep the PR draft or blocked;
* separate production fixes from test-only scope unless explicitly authorized;
* rerun the test against the production fix before recommending merge.

## 8. Required evidence standards

For every PR, require:
1. Exact target branch.
2. Exact base SHA.
3. Exact final head SHA.
4. Exact changed-file list.
5. Manual inspection of every changed file.
6. CI status on the exact final head.
7. Focused-test commands and results.
8. Broader regression commands and results.
9. Migration results when applicable.
10. Frontend validation when contracts or shared types are affected.
11. Review status.
12. Unresolved review-thread status.
13. Known defects and limitations.
14. Skipped checks with explanations.
15. Confirmation that no merge occurred without explicit authorization.

Do not imply that merely listing a required reviewer means that review happened.

## 9. Parallel-branch and stale-base policy

For parallel agent work, contributors are required to check:

```powershell
git merge-base <current-main> HEAD
git diff <current-main>...HEAD
git diff --name-status <current-main>...HEAD
```

A PR should generally be recreated from current `main` when:
* it claims one or two files but includes many unrelated files;
* merged parallel work is embedded in its branch;
* the branch is based on superseded implementation;
* rebasing would make it difficult to prove the final scope;
* the PR description no longer matches the actual diff.

Useful work may be selectively ported to a clean branch rather than rewritten from scratch.

## 10. Current validation gaps

The following are verified current gaps requiring manual inspection until automated controls are introduced:
* **no dedicated Markdown lint:** Current impact: formatting errors; Temporary control: manual inspection; Recommended: automated markdown linting; Does not block current project phase.
* **no automated secret-scanning job:** Current impact: potential leaks; Temporary control: manual diff inspection; Recommended: secret scanning action; Does not block current project phase.
* **no browser end-to-end suite:** Current impact: missed UI regressions; Temporary control: manual preview; Recommended: Playwright or Cypress; Does not block current project phase.
* **no multi-process SQLite concurrency suite:** Current impact: potential locking issues in production; Temporary control: careful architecture review; Recommended: multi-process test suite; Does not block current project phase.
* **limited Windows junction/reparse-point testing:** Current impact: path bugs; Temporary control: manual testing on Windows; Recommended: platform-specific automated tests; Does not block current project phase.
* **no formal release artifact validation:** Current impact: broken builds; Temporary control: manual build check; Recommended: CI build step validation; Does not block current project phase.
* **no automated backup/restore rehearsal:** Current impact: uncertain recovery; Temporary control: manual testing; Recommended: CI restore job; Does not block current project phase.
