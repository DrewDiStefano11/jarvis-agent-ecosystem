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

## PR #40 behavior-level parity audit

PR #40 head inspected as reference only: `5777faaa5792c9f08811338cecb96d2559b18730` (base `afb67d2e5058a8ba925f9ae8d714896c7ba66b0e`). No PR #40 commits, migrations, or branch history were reused.

| PR #40 behavior or invariant | PR #40 source/test reference | Corresponding PR #46 implementation | Corresponding PR #46 test | Parity status | Required action |
|---|---|---|---|---|---|
| Runtime HTTP success responses use standard `{data, meta}` envelope. | `test_agent_runtime_api_envelope.py` | `agent_runtime/router.py` with `TypedApiResponse` | `test_agent_runtime_sql_control_plane.py`, existing API envelope tests | Covered | None |
| Runtime command errors are bounded and stable. | `test_control_plane_integration.py`, `test_control_plane_fresh_findings.py` | `main.py` runtime error handler; `agent_runtime/errors.py` | runtime auth/API tests and existing contract tests | Covered differently | Keep bounded metadata only |
| SQL runtime command effects commit event/projection/processed-command/audit/outbox atomically. | `test_control_plane_integration.py` | `SqlAlchemyAgentRuntimeRepository.commit_command()` | `test_agent_runtime_sql_control_plane.py`, existing repository/idempotency tests | Covered | Added auth-aware audit checks |
| Runtime audit visible through `/api/audit-events` in same process. | Codex finding on PR #46; related PR #40 integration coverage | `main.py` reloads shared audit projection; runtime audit writes shared `audit_events` | `test_agent_runtime_sql_control_plane.py` | Covered | Fixed and tested |
| Runtime audit attribution includes actor, command, run, task, target agent, previous/new state, event IDs. | `test_control_plane_integration.py` | `_store_audit()` uses verified actor and bounded authorization payload | `test_agent_runtime_sql_control_plane.py` | Covered differently | Uses verified actor, not body actor |
| Runtime outbox exhausted health degrades top-level health. | `test_control_plane_review_findings_08.py` | `health_status()` counts `status='failed'` and attempts >= max | `test_agent_runtime_sql_control_plane.py` | Covered | Fixed actual outbox convention |
| Runtime health handles stale/unavailable/malformed/corrupt states with bounded details. | `test_control_plane_fresh_findings.py`, `test_control_plane_review_findings_08.py` | `main.py` runtime health normalization plus repository health probe | existing persistence/health tests and runtime SQL health regression | Covered | None |
| Correlation IDs preserved exactly to 120 characters across audit/outbox/runtime. | `20260728_08`, review findings tests | `models/constraints.py`; consolidated `20260729_04` widens columns | migration and contract tests | Covered differently | Single consolidated migration replaces old chain |
| Exactly one Alembic head. | `test_agent_runtime_migrations.py` | consolidated migration from `a87a487dd714` to `20260729_04` | migration validation and tests | Covered | None |
| Blank DB upgrade and previous-head upgrade. | `test_agent_runtime_migrations.py` | Alembic migration `20260729_04` | `test_persistence.py`, validation commands | Covered | None |
| Unsafe correlation-ID narrowing fails before destructive DDL. | PR #40 guarded downgrade behavior in `20260728_08` | `20260729_04` calls `_reject_unrepresentable_downgrade()` first | `test_agent_runtime_migration_downgrade.py` | Covered | Fixed ordering |
| Runtime checkpoint IDs may be reused across different runs without cross-run mixing. | `test_control_plane_integration.py` cross-run checkpoint tests; `20260727_07` | ORM uses composite run/checkpoint keys; repository deletes/regenerates per run projections | existing checkpoint tests; migration constraint tests | Covered differently | Existing current-schema constraints preserve run isolation |
| Same-run checkpoint conflicts are deterministic. | `test_control_plane_integration.py` | runtime service checkpoint conflict checks | `test_agent_runtime_checkpoints.py`, idempotency tests | Covered | None |
| Recovery cannot resume from another run's checkpoint. | `test_control_plane_integration.py`, checkpoint tests | runtime service `_find_checkpoint()` operates on current aggregate only | `test_agent_runtime_checkpoints.py`, `test_agent_runtime_recovery.py` | Covered | None |
| Parent run references persist and restart lineage works. | `test_control_plane_integration.py` lineage tests | runtime run snapshot/specification persisted in SQL | `test_agent_runtime_lineage_pagination.py`, SQL restart test | Covered | Added lineage auth/restart coverage |
| Lineage authorizes every ancestor. | New Codex finding on PR #46 | `lineage_authorized()` resolves incrementally and authorizes parent before adding entry | `test_agent_runtime_lineage_pagination.py` | Covered | Fixed |
| Missing parent lineage is bounded. | `test_agent_runtime_api_envelope.py` lineage coverage | `resolve_lineage()` returns missing parent marker from authorized child reference | `test_agent_runtime_lineage_pagination.py` | Covered | None |
| Cycle/malformed lineage fails safely. | PR #40 lineage/replay coverage | cycle detection in runtime service | `test_agent_runtime_lineage_pagination.py` | Covered | None |
| Concurrent identical non-create command produces one mutation and one replay. | `test_agent_runtime_concurrent_idempotency.py` | post-lock processed-command recheck plus repository commit lock for SQLite safety | `test_agent_runtime_sql_control_plane.py`, existing idempotency tests | Covered | Strengthened SQLite serialization |
| Duplicate concurrent command writes no duplicate event/processed/audit/outbox rows. | `test_agent_runtime_concurrent_idempotency.py` | unique constraints plus replay return | SQL runtime concurrency and idempotency tests | Covered | None |
| Changed command content returns `command_conflict`; stale different command returns version conflict. | `test_agent_runtime_concurrent_idempotency.py` | service/repository idempotency precedence | `test_agent_runtime_sql_control_plane.py`, existing idempotency tests | Covered | None |
| Idempotent replay is actor-restricted. | Not in PR #40; required by PR #46 review scope | processed-command stores `verified_actor_id`; service/repository enforce | `test_agent_runtime_authorization.py` | Covered differently | New stricter behavior |
| Protected reads require verified actor and permissions. | `test_runtime_authorization_bootstrap_migration.py` (bootstrap catalog) | `IdentityRuntimeAuthorizer` over existing `IdentityService` | `test_agent_runtime_authorization.py`, lineage tests | Covered differently | Uses current RBAC, no bootstrap migration chain |
| Role/permission assignment scope exactness preserved. | `test_identity_rbac.py` in PR #40 and main | Existing `IdentityService.check_permission()` and `_role_scope_matches()` | existing `test_identity_rbac.py`, runtime auth scope tests | Covered | None |
| List pagination must not leak unauthorized totals and must not loop. | New Codex finding on PR #46; PR #40 API pagination coverage | `list_runs_authorized()` returns forward raw cursor or `None` on exhaustion | `test_agent_runtime_lineage_pagination.py` | Covered | Fixed final-page cursor |
| Intermediate migrations `20260727_04` through `20260728_08`. | PR #40 migration chain | Consolidated single migration `20260729_04` from current main head | migration tests and parity audit | Intentionally obsolete | Clean PR #46 must not recreate stale five-revision chain |
| Runtime authorization catalog/bootstrap migration. | `20260727_06`, `test_runtime_authorization_bootstrap_migration.py` | Current PR uses existing identity definitions created by administrators/tests; no hardcoded runtime bootstrap catalog | runtime authorization tests | Covered differently | Avoids duplicate permission system |
| PR #40 docs for runtime control plane. | `docs/agent-runtime-control-plane.md`, docs changes | PR #46 plan and migrations docs document current behavior | plan doc, PR body | Covered differently | Kept concise current docs; old draft docs remain reference-only |
| Provider exception-chain hardening. | Not PR #40 runtime scope; PR #45 follow-up | Ollama/OpenAI-compatible adapters sanitize exception chains | `test_model_provider_adapters.py` | Covered | None |

### PR #40 content intentionally not ported

- The five-step PR #40 migration chain is obsolete for PR #46 because this branch starts from PR #45's merge commit and uses one consolidated Alembic revision from `a87a487dd714` to `20260729_04`.
- PR #40's runtime authorization bootstrap migration is not ported because PR #46 now evaluates runtime permissions through the existing identity/RBAC service and tests create explicit permission definitions; this avoids a parallel authorization catalog.
- PR #40 route behavior that trusted local callers without verified actors is superseded by PR #46's stricter `X-Jarvis-Actor-Id` verified local-control-plane boundary.

## Parent-link authorization repair

- Create commands remain authorized against the child run's `task_id` using `runtime.create` before any parent lookup, preserving child authorization precedence.
- External protected create now requires a fully existing and authorized parent/ancestor chain whenever `parent_run_id` is present. Each existing parent/ancestor requires `runtime.read`; `runtime.admin` remains the bounded override.
- Missing parents, unreadable parents, explicit parent denies, expired parent grants, missing grandparents, and unreadable grandparents all fail with the same bounded `runtime_parent_unavailable` response. The response contains no parent/ancestor run IDs, task IDs, agent IDs, states, correlation IDs, assignment IDs, role IDs, SQL, paths, or tracebacks.
- Existing persisted or imported legacy/corrupt missing ancestry can still be represented by the lineage reader as `missing_parent_id`; the external create route no longer introduces new unresolved parent references.
- Exact replay still requires the same verified actor plus current child and parent/ancestor authorization. Permission removal or parent/ancestor deletion prevents returning the stored protected result and creates no duplicate artifacts.
- Rejected parent-unavailable and lineage-validation failures preserve the zero-artifact contract for runtime runs, events, attempts, checkpoints, processed commands, mutation audits, and runtime outbox rows.
- Regression coverage lives in `apps/api/tests/test_agent_runtime_parent_authorization.py`.
