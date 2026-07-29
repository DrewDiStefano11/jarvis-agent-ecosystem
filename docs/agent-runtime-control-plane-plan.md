# Durable runtime control plane replacement plan

Living execution plan for draft PR #46, a clean replacement that supersedes stale draft PR #40.

## Starting state

- Remote state fetched before planning or editing.
- Required base SHA: `aaa823d17417bcbc906e99b03a00494d94ddd79a`.
- Existing PR branch: `arena/019fadd4-jarvis-agent-ecosystem`.
- Previous reviewed head: `8fe8ded3c5cbb68619e476f3c2dd3c98cfdedb74`.
- Remote branch head verified unchanged before this repair pass.
- PR #40 remains reference-only; no merge, rebase, cherry-pick, branch-history reuse, close, or modification.

## Repository foundations to preserve

- Alembic is authoritative; no `create_all` startup substitute.
- Route handlers remain thin and contain no SQL.
- SQLAlchemy repositories and explicit transactions own persistence.
- Identity/RBAC, hierarchical role assignments, task leases, context assembly, audit, transactional outbox, durable workflow ledger, processed-command idempotency, normalized envelopes/errors, health reporting, correlation-id preservation, and model-provider phase gates remain authoritative.

## Repair objectives for PR #46

1. **Service-boundary runtime authorization**
   - Add a trusted local-control-plane actor boundary for runtime routes.
   - Resolve the actor through `IdentityService`; reject missing, unknown, inactive, suspended, or retired identities with bounded runtime errors.
   - Enforce command/read permissions in `AgentRuntimeService` before protected reads or mutations.
   - Require any body `actor_reference` to exactly match the verified actor, then populate the command with the verified actor.
   - Use existing identity permission/role/scope/resource-policy checks. Do not create a parallel RBAC store.
   - Runtime permission keys are grouped by operation class: read, create, queue, execute, pause, cancel, checkpoint, complete, recover, admin.
   - For this local phase, runtime scoped permissions evaluate against the authoritative runtime task scope (`resource_type="task"`, `resource_id=task_id`); admin uses `administrative_function/agent_runtime`.
   - Exact command replay is restricted to the same verified actor recorded with the processed command.
2. **Migration downgrade safety**
   - Move correlation-ID representability checks to the very start of downgrade.
   - Add unsafe downgrade preservation and safe downgrade/upgrade tests with non-empty runtime/audit/outbox data.
3. **Runtime audit attribution**
   - Attribute audit rows to the verified actor, not body-supplied `actor_reference`.
   - Record command type, run/task/target-agent identifiers, prior/new state, event IDs, bounded authorization decision metadata, and correlation ID.
   - Avoid duplicate mutation audit rows on exact replay.

## Progress log

- Completed mandatory reread of AGENTS, PR #46 diff/comments/review state, runtime service/router/repository/contracts, identity service/models/tests, migration, audit/outbox models, runtime SQL tests, and migration tests.
- Codex review on prior head produced additional valid findings: runtime audits were not visible through the in-process audit endpoint, runtime outbox exhaustion health counted the wrong status, and migration docs mis-described `20260729_04`.
- Implementation will address these findings together with the three confirmed blockers.

## Non-goals

No autonomous scheduling, model execution, OpenHands, browser automation, shell tools for agents, external integrations, office prototype integration, WebSocket orchestration beyond current contracts, public remote access, persistent cost accounting, speculative capabilities, or misleading placeholders.
