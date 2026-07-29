# Durable runtime control plane replacement plan

Living execution plan for a clean replacement that supersedes stale draft PR #40.

## Starting state

- Remote state fetched before planning or editing.
- Required base SHA: `aaa823d17417bcbc906e99b03a00494d94ddd79a`.
- Arena session branch: `arena/019fadd4-jarvis-agent-ecosystem`.
- This branch starts at the required base SHA. Arena requires work to remain on this session branch, so no alternate branch is created.
- PR #40 will be inspected only for changed files, patches, review history, tests, and intended behavior; none of its commits will be merged, rebased, cherry-picked, or reused as branch history.

## Repository foundations to preserve

- Alembic is authoritative; no `create_all` startup substitute.
- Route handlers remain thin and contain no SQL.
- SQLAlchemy repositories and explicit transactions own persistence.
- Identity/RBAC, hierarchical role assignments, task leases, context assembly, audit, transactional outbox, durable workflow ledger, processed-command idempotency, normalized envelopes/errors, health reporting, correlation-id preservation, and model-provider phase gates remain authoritative.

## Implementation phases

1. Complete repository reconnaissance.
   - Read AGENTS, README, migrations, current API routes, schemas, services, repositories, models, DB sessions, errors, health, audit, outbox, task leases, context assembler, identity/RBAC, durable workflow ledger, provider foundation, tests, and docs.
   - Inspect PR #40 file list, patch, review history, tests, and intended behavior only as reference.
2. Add durable runtime persistence.
   - Add one migration from the current main Alembic head.
   - Add SQLAlchemy rows for runtime executions, attempts, events, checkpoints, processed command results, and deterministic indexes/constraints.
   - Implement an SQLAlchemy runtime repository with atomic event/projection/processed-command/audit/outbox writes, optimistic concurrency, replay validation, deterministic projection rebuild, pagination, and bounded integrity checks.
3. Add service/API control plane.
   - Preserve current state machine terminology and command contracts.
   - Add typed API routes using normalized response/error envelopes and no route SQL.
   - Enforce authorization at service boundaries, including actor identity, role, assignment scope, inactive/expired/cross-scope cases, and read-vs-command permissions.
   - Add bounded runtime health/integrity summary integrated with top-level health degradation.
4. Harden provider adapter exception chains.
   - Prevent malformed JSON, response-contract validation, and translated HTTP failures from retaining raw provider data or credential-bearing request objects via `__cause__` or `__context__`.
   - Add focused Ollama and OpenAI-compatible regressions without enabling live providers.
5. Add focused tests.
   - State/forbidden transitions, idempotency precedence, stale versions, rollback seams, restart/replay, projection rebuild, concurrency/races, checkpoint stale writes, corruption detection, pagination, bounded health degradation, RBAC/scope enforcement, audit/outbox atomicity, migrations/head checks, provider sanitization, secret/runtime-artifact scans.
6. Validate locally.
   - Backend Ruff format, Ruff lint, full pytest, focused runtime tests, Alembic heads, blank upgrade, upgrade/downgrade/upgrade, concurrency/restart tests.
   - Frontend typecheck, lint, tests, production build.
   - Repository `git diff --check`, changed-file audit, secret scan, runtime DB/sidecar scan, dependency/build artifact scan, exact migration-head inspection.
7. Draft PR and review loop.
   - Open a draft PR from `arena/019fadd4-jarvis-agent-ecosystem` only after local validation passes.
   - PR body explicitly supersedes PR #40, includes required impact/validation evidence, and states no merge attempted.
   - Request fresh Codex review on the final SHA; address valid findings with regressions; repeat until CI/review requirements are satisfied while remaining draft and unmerged.

## Non-goals

No autonomous scheduling, model execution, OpenHands, browser automation, shell tools for agents, external integrations, office prototype integration, WebSocket orchestration beyond current contracts, public remote access, persistent cost accounting, speculative capabilities, or misleading placeholders.
