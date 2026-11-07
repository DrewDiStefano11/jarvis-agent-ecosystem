# Emergency-stop manager projection implementation plan

> **For agentic workers:** Execute inline using superpowers:executing-plans; use test-driven development and a final branch review. Do not deploy, merge or push.

**Goal:** Preserve native planning-manager assignments without breaking the legacy task foreign key during emergency stop, context saves or ordinary repository persistence.

**Architecture:** The task payload and team-selection record retain native identity IDs. The existing `tasks.assigned_manager_id` column is a legacy simulator projection whose foreign key references `agents`, so it may contain only an ID present in the repository's simulator-agent collection. Use that same projection in task-row construction and context updates. No migration, response-envelope, permission, event-version or checkpoint change.

**Tech stack:** Python 3.12, SQLAlchemy 2.0, FastAPI, Alembic, pytest.

**Spec:** Owner's permanent-host campaign requires authoritative emergency stop, compatible contracts, preserved history, isolated test databases and reviewed promotion. Actual unchanged-main reproduction is retained under `C:/Jarvis/host-campaign-20261106`: emergency-stop HTTP500 with an identity manager copied into the legacy agent foreign key.

## Constraints and decisions

- Work only in this feature worktree; the permanent runtime keeps its reviewed source and has inference/worker disabled.
- Never weaken or disable foreign keys, erase a manager assignment, create simulator stand-ins for real identities, or alter authorization.
- Preserve valid legacy manager projections and native manager payloads across restart.
- Keep audit and transactional outbox behavior intact; exercise stop/resume through the HTTP API.
- Existing baseline system shard continues in the original runtime checkout; do not repeat or disturb it.
- Ruling: implement the supplied repair objective without another design approval, as the owner explicitly authorizes ordinary targeted repairs and continuous progress. Owner approval is still required for push/merge/promotion.

## Review focus

1. Native manager IDs must survive in payloads even though their legacy FK projection is null.
2. Legacy simulator managers must retain their normalized foreign key.
3. Context persistence must use the same projection as global/system persistence.
4. Stop/resume must remain durable and must not infer execution or grant permissions.
5. Tests and populated-copy reproduction must never open the live database for mutation.

## Task 1 — Correct manager projection

Files: modify `apps/api/app/repositories/sqlalchemy.py`; add focused cases to `apps/api/tests/test_persistence.py` and `apps/api/tests/test_context_integration.py`; document the boundary in `docs/architecture` or the existing persistence guide.

- [x] Add a native-manager and legacy-manager emergency-stop/resume/restart regression on temporary migrated databases; observe the native case fail with HTTP500/FK violation.
- [x] Add a native-manager context-save regression; observe the FK failure before implementation.
- [x] Preserve the static task-row factory and use an instance persistence wrapper to project only actual simulator-agent IDs into the legacy manager column; apply the same rule in context updates. Preserve the original payload.
- [x] Run the new tests, then persistence/context/team-selection/worker regressions, Ruff and diff checks. Final targeted run: 163 passed. Frontend: 262 passed; typecheck, ESLint and build passed. Ruff format/lint passed; collection 4182 is collection only.
- [x] Reproduce on a consistent disposable copy of the host database and verify stopped/resumed state, result hashes, original task identities, foreign keys and integrity. Final copy retained 11 task identities/manager assignments and 5 execution hashes/JSON unchanged.
- [x] Review the complete small diff and record exact results. Fresh review closed the static-factory integration P1 and found no remaining actionable findings. No live deployment, push or merge. Local commit follows the completed applicable checks.

Ruling: the explicitly tracked PR165 calls the static factory from saved-result
review. A first implementation that bound the factory to a repository instance
passed the main-only regressions but broke that integration. A new correction-row
compatibility regression reproduced the TypeError. Keep the original static
factory and add a normalized instance wrapper at the three repository persistence
boundaries; this avoids changing PR165 or its legacy-manager projection. The
bounded-tool caller already copies a validated legacy manager column.
