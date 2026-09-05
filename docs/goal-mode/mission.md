# AI Hub completion mission

## Starting point and protected work

- Repository: DrewDiStefano11/jarvis-agent-ecosystem.
- Initial checkout: clean `main`, SHA `784ec98b26d1d2070161acb5f498af998d6ea02a`.
- Mission branch: `astra/goal-complete-ai-hub`.
- Exact starting SHA: `a7124c66fe302aee339efe7ea34e7a7d4bbc0a48` (PR #55).
- Dependency chain: main → #54 (`c4f70b432bd5b51e65c7b963837368e008722175`) → #55.
- Separate draft #53: Windows supervisor at `317866b575a0848982f9ccead74dfc28d8cdb6e9`, based on pre-#52 main. Its 32-file implementation and review findings are protected; it has not been merged or overwritten.
- No PR merges, branch resets, force pushes, or removed regression tests.

## Baseline actually run

- Python 3.12.13; Node 24.19.0; pnpm 11.19.0. CI uses Python 3.12 and Node 22 on Windows.
- Isolated backend editable install with development dependencies succeeded; frontend frozen lockfile install succeeded.
- Backend Ruff formatting and lint passed; pytest: 739 passed, two dependency deprecation warnings (Starlette/httpx and AnyIO portal alias).
- Frontend typecheck, lint, 25 Vitest tests, and production build passed.
- Baseline migrations/runtime were not run before edits; post-change migration and real-process results are recorded below.

## Architecture and dependency model

SQLite/Alembic → repository transactions + audit/outbox → identity/RBAC and runtime ledger → exact fenced task leases → separately enabled local planning worker → immutable context assembly → loopback provider → bounded typed result → durable deterministic review → completion/revision/escalation → persisted task and execution history.

HTTP/WebSocket → `apps/web/src/state/AppStore.tsx` → dashboard/tasks/details/office. At baseline, the UI exposed primarily simulated agents, with no planning submission/result inspection and a CSS eight-zone office. The mission now connects the separate identity/runtime workforce through Planning and the shared Office view; see the implementation record below.

Existing providers are neutral adapters but real execution is explicitly local-only. Tools, cloud execution, general autonomous task decomposition and Business Lab are not implemented. Preserve current security and permission boundaries while integrating supported functionality.

## Acceptance tracking

- [x] Fresh database migration and documented setup.
- [ ] User creates a task, explicitly selects authorized local planning, and queues it.
- [x] Existing worker leases exact work, calls configured provider, persists and reviews results.
- [x] Operator sees progress, actual output, review outcome and durable history.
- [ ] Office uses the shared authoritative state and existing reference navigation where available.
- [ ] Crash, failed provider, cancellation and reconnect paths remain valid.
- [ ] Full validation and running frontend/backend interaction checks.
- [ ] Mission branch pushed; CI and review checked against exact head.

## Findings and work in progress

P1: PR #55 preparation scanner excluded runs with any historical execution, stranding revisions after unblock/begin. Scanner now distinguishes active-attempt/active-execution rows from historical rows; claimed revisions re-derive their checkpoint from authorized durable history. Deterministic crash tests cover both boundaries.

P2: Post-task-failure crash lost `review_revision_exhausted`. Recovery now reads the integrity-checked review decision before terminalizing the execution. Deterministic regression covers that commit window.

P1/product: real local planning workflow has no ordinary operator UI.

P2/product: office and dashboard expose simulated workforce only; no real office navigation exists in this checkout.

External verification constraint: Windows supervisor native runtime requires Windows. Real model inference requires a configured local model service; deterministic transport tests must be labeled accordingly.

## Implemented and locally verified

- Recovery scanner now distinguishes historical executions from the active attempt;
  revision checkpoint lineage survives both preparation crash windows.
- Review-revision exhaustion survives a crash after the authoritative task failure.
- Planning workspace submits approved context and durable create/queue commands,
  reuses IDs after lost acknowledgements, reads authorized history and full model
  results, and clears disclosed state when the actor changes or access fails.
- Task details link to planning results and expose authoritative cancellation;
  creation, cancellation and emergency-stop failures are visible and recoverable.
- Explicit CLI provisions only per-task runtime permissions and refuses inactive
  identities. It never enables inference, grants tools, or grants runtime.admin.
- Office replaces the CSS grid using the original prototype renderer/camera and
  pinned artwork, shares Hub state, labels demonstration agents, and offers
  candidate geometry inspection without inventing workforce placement.
- Added immutable asset installation, current local setup instructions, and a
  separate-process HTTP smoke harness using the actual frontend submission code.
- Added a Linux CI browser job with screenshot artifacts, without changing the
  existing Windows jobs or upgrading existing dependency versions. Playwright
  1.62.1 is the sole new direct dependency, used only for browser validation.
- Removed the external font request so the local Hub shell does not depend on a
  third-party font service; existing system/monospace fallbacks remain available.

## Validation evidence

- Backend: **746 passed**, same two baseline dependency deprecations. Ruff format
  and lint passed for all 101 backend files.
- Frontend: **45 passed** across four files, typecheck, lint and production build
  passed. Fourteen original coordinate/gesture cases are preserved; candidate
  counts and approval boundary, control errors, and authorized result UI are tested.
- Blank SQLite database migrated through all seven revisions to `20260905_06`.
  Downgrade to `20260729_04`, upgrade back to head and final current check passed.
- `scripts/smoke-local-planning.py` ran the actual API process and separate worker,
  submitted through the frontend's real planning function, replayed the submission,
  observed exactly one actual HTTP request to the fixture provider, and verified
  completed task + persisted reviewed result. Repeated after final script changes.
- HTTP adapter integration tests verify success, exhausted-provider human-review
  pause, authorization, and identical durable result readback after API restart.
- The original office image was verified by SHA-256; the reference repo was not
  modified. No production approval, live desk binding, or movement was fabricated.

## Current blockers and unfinished work

External/environment:

- Real model quality and successful real inference require an installed loopback
  model, unavailable here. Deterministic HTTP execution is never labeled real inference.
- Cloud Browser returned `ERR_BLOCKED_BY_CLIENT` for localhost. Local Chromium was
  absent, and its installer exhausted download attempts with timeouts. Local
  screenshot comparison and browser interactions remain unverified. CI now carries
  the reproducible browser test and screenshot collection.
- Native Windows supervisor runtime cannot be validated in this Linux environment.
- Native Git push lacks credentials; use the authorized GitHub connector to publish
  commit objects and the dedicated branch without altering shared review branches.

Critical unfinished:

- Measured office registration, approved route geometry, identity-to-desk bindings,
  and backend movement intent remain necessary for a real moving workforce. The
  canonical navigation/sprite implementation remains in the prototype, not replaced.
- General autonomous coding/research, tool execution and cloud escalation are beyond
  the current narrow planning contract and still require designed, permissioned
  integrations. Business Lab is not implemented.
- Human-review pauses can be inspected/cancelled; a complete operator correction and
  resubmission UX remains unfinished. A page reload does not retain a pending form's
  submission ID; operators must inspect durable history before submitting again.

Noncritical unfinished: production sprite animation, Nexus tube effects, full
history pagination controls, and unified presentation of simulated and real
workforce capabilities. The simulation's pre-existing performance metrics remain
explicit fixtures, not model-execution telemetry.

Highest-value next objective: verified office registration and durable worker desk/
movement contracts, reusing the existing prototype routing and sprite system.
GitHub exact-head CI/review outcomes will be recorded after publication.


## Continued integration after initial publication

Initial publication: draft PR #56, commit
`a964d028d3cbc507577e22d2afcae312e692401e`. Its CI run `33940781604` passed the
frontend, repository-integrity and real browser jobs. The backend job was still
running when further improvements began. Browser screenshots were retrieved and
visually inspected, overcoming the local browser-access limitation through CI.

The next highest-value gap was repeated terminal provisioning for each new task.
The Hub now has an explicit per-task **Prepare local planner** action backed by
Pydantic request/response contracts and the same provisioning service as the CLI.
It reuses the configured identity, preserves denials, refuses suspended identities,
rejects non-loopback callers, and cannot enable a model or grant tools/admin.
The browser golden path now uses this action rather than shell provisioning after
task creation. CLI provisioning remains available for initial/headless setup.

Visual inspection also corrected default browser button/link styling, mobile
framing, and the misleading presentation of an identity's stale offline record
beside a healthy worker. Identity enablement and actual worker health are now
shown separately. Exact live desk placement is still intentionally unclaimed.

CI now cancels superseded/duplicate runs for the same branch and bounds job time;
every current-head validation gate remains enabled.

Exact-head workflow `33947089245` later exposed a remaining provisioning race:
three task-scoped open grants could be inserted twice because their generated
start times differed. Alembic revision `20260905_06` now makes active open-ended
global and scoped grant convergence a database invariant, preserves finite
intervals and revoked append-only history, and refuses legacy duplicates rather
than silently rewriting authorization records.
Local validation for this correction passed Ruff across all 106 Python files,
**753 backend tests**, frontend typecheck, ESLint, **47 frontend tests**, the
production build, and the real API + separate worker HTTP fixture smoke.
