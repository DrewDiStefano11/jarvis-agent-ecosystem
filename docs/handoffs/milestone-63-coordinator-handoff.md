# Milestone #63 — Coordinator / Synthesis / Retry Engine

## Stack and authorization

- Original dependency #62 SHA: `4ffb459e245e2f179a6982acdb4d4c3b7543e885`.
- Original `origin/main`: `d9198b8c3cd73e65ea9d2d6b8571ad35f962e797`.
- Branch: `codex/coordinator-synthesis-retry`.
- Development worktree: `C:\Jarvis\coordinator-synthesis-63`.
- #62 worktree and `C:\Users\ddist\JARVIS-RUN` are untouched.
- User explicitly authorized pushed phase checkpoints and an eventual review PR; no merge is authorized.
- #62 was OPEN at inspection. GitHub metadata is authoritative for all final heads.
- Latest observed #62 head: `0c10d2a7ac88af3db31e099a70b342f821b3b542`. This changes capability-inference failure behavior, its fake router, and browser synchronization. It has not been incorporated. Reassess at the next coherent checkpoint before touching request classification.

## Current state — incomplete milestone

Phase A implements internal durable coordinator preparation and a canonical ready-node reservation. It does **not** expose a new API, dispatch model requests, or alter ordinary worker execution. Phases B–D remain required. Do not describe the milestone or runtime acceptance as complete.

## Architecture

`CoordinatorService` consumes the authoritative active #62 decomposition, exact parent planning runtime, team selection, and grounded assembly. `CoordinationRepository` serializes its small aggregate with the existing `SystemStateRow` first-write fence. One row per decomposition and one per parent runtime are database uniqueness invariants. A hash binds the full graph, so in-place operator edits cannot silently change reserved work.

Claims require the existing `RuntimeExecutionFence` and current `TaskLeaseRow`. They retain a deterministic child runtime/attempt preparation reference. There is no new lease table, scheduler, worker identity, task identity, or authorization grant. The existing worker policy allows exactly one execution per process; readiness preserves every independent node, while this first claim primitive reserves one at a time.

Runtime authorization now optionally accepts an existing SQLAlchemy session. The existing identity permission evaluator and administrative override semantics run inside the coordinator write transaction. Existing callers retain their prior behavior.

## Lifecycle

Current implementation only persists `active` coordination and `pending -> claimed` node preparation. Readiness is derived from the graph and eligible identities. Claiming does not itself begin a runtime attempt. Task cancellation, pause/review, terminal state, emergency stop, stale graph, expired lease, changed objective/team, ineligible specialist, and revoked worker permissions reject reservation.

The selected manager must already be an enabled active planning/coordinator identity without catalog activation provenance. Imported specialists cannot become the manager. No identity is provisioned or elevated by coordination; existing #61 manager selection remains authoritative.

## Planned execution, retry, replan and synthesis work

Next integrate preparation references with existing authorized child runtime commands and the parent's exact task lease. Dispatch only bounded intellectual work through the existing local model router; unsupported actions must block. Persist validated specialist results and criteria assessments before unlocking dependencies. Use the existing retry policy's delay calculation with durable eligibility timestamps and bounded attempts, not sleeps. Preserve successful results and prior graph versions. #62 currently forbids automatic graph replacement once task execution has started: any replan must respect that constraint and use bounded auditable recovery/reselection rather than bypassing it.

Manager synthesis must be a distinct request with durable provenance and idempotent finalization through `TaskLeaseRepository.complete_task`. No synthesis/result path is implemented yet. Crash recovery currently proves that a reserved reference survives expiry and cannot be reserved twice; resuming actual inference remains Phase C work.

## Files and migration

- `apps/api/app/coordination/{repository,service}.py`: preparation and fenced claim boundary.
- `apps/api/app/models/coordination.py`: bounded contracts and derived readiness.
- `apps/api/app/db/models.py`: `CoordinationRow`.
- `apps/api/migrations/versions/20260907_11_task_coordination.py`: narrow table, task/decomposition/runtime FKs and uniqueness; populated downgrade refuses before DDL.
- `apps/api/app/agent_runtime/authorization.py`, `apps/api/app/identity/service.py`: optional transaction-bound authorization reads.
- `apps/api/app/main.py`, `apps/api/app/models/domain.py` and revision assertions: advance advertised schema head.
- `apps/api/tests/test_coordination*.py`: migrated isolated DB, concurrency, authority, lifecycle and migration coverage.

## Validation at this checkpoint

- Focused preparation/claim/operator suite: **17 passed**; migration roundtrip: **1 passed** (18 focused tests total). A prior combined run also passed all 18. Final manager checks preserve #61's preexisting planning identity without requiring an elevation.
- Frontend typecheck, ESLint, **101 Vitest tests**, and production build: passed.
- Full backend suite was launched before this checkpoint and remains running. It has reported failures in inherited worker tests; final names/details are not available until completion. Do not claim full backend success. Process/log ownership is recorded below so continuation can collect it.
- Scripts Ruff format/check and `git diff --check`: passed.
- Inherited #62 Ruff failures at the original dependency: formatting and unused `result` in `tests/test_task_decomposition.py`. These are not #63 changes; do not disguise their source.
- Existing real API/worker/browser smoke was launched on an isolated temporary DB and is running. It is a regression check, not acceptance of the unfinished coordinator execution flow. Final exact-head Actions and #63 runtime acceptance remain pending.

## Security review

Preparation/claims write only coordinator state, audit and outbox rows. Tests snapshot permissions, roles, ranks, activation, lifecycle and system flags after fixture provisioning and verify no changes. There are no workspace/tool grant writes or executable tool transports in this module. Runtime authorization, task lease, stop and active plan checks occur before each state mutation. Unsupported external actions and milestones #64/#65/#66 remain unimplemented.

## Exact next actions

1. Collect the running full backend and existing runtime/browser regression results. Review any failure against the recorded #62 dependency before fixing it.
2. Verify Phase A's pushed checkpoint/local and remote heads match. Update this handoff with actual validation outcomes.
3. Evaluate latest #62 contract changes; checkpoint before any stack update. Never modify its worktree/history.
4. Implement Phase B results/criteria/execution and durable bounded retries on the existing runtime, then validate and push.
5. Implement Phase C synthesis, crash recovery, terminal summaries, bounded replan/reselection and push.
6. Implement Phase D task-state UI/API integration, full security/concurrency/migration/runtime/browser acceptance, self-review and push.
7. After #62 merges, reconcile #63 onto merged main so the final PR contains only #63 changes. Rerun full acceptance and exact-head Actions; open/ready the review PR, never merge it.

## Local tooling

This worktree has its own ignored `.venv` and `apps/web/node_modules`. From `apps/api`, use `../../.venv/Scripts/python.exe -m pytest` and `-m ruff`. Isolated pytest base directories are outside the repo under `C:\Jarvis\test-tmp-63-*`; backend Phase A log is `C:\Jarvis\63-backend-phase-a.log`. No runtime DB, sidecar, environment file, dependency, or build may be staged.

Active tool sessions at first checkpoint: full backend `68585`, existing browser smoke `96759`. Browser log: `C:\Jarvis\63-browser-phase-a.log`; browser artifacts: `C:\Jarvis\63-browser-phase-a`. These are session-local convenience references, not durable acceptance evidence. The git handoff must ultimately record completed checks.
