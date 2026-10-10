# Workspace publication CI repair — October 7, 2026

PR #89's original authority repair was independently reviewed clean at
`1e45b8fa511a20fccee5d4e8c0048d3ad06affda`. Its exact-head CI run
`37529237604` passed all non-autonomy gates; autonomy was passing at 97% when
the accumulated-runtime watchdog stopped it. No assertion failure or CI waiver.

The two independently reviewed #92 commits are now applied as one infrastructure
integration delta. All five staged infrastructure files exactly match reviewed head
`e3311b59b450162cc0a2fac469a5560a787e6826`. The latest #92 review found no
major issues; its own CI remains pending. Workspace/application contracts and
migration are unchanged by this integration.

Combined focused CI/workspace package: 74 passed. Ruff format/lint pass from the
repository root with the proper script/backend configurations. Frontend typecheck,
ESLint, 194 Vitest tests and build pass. Native blank migration/current/history/
downgrade/re-upgrade and 1,895-case collection coverage pass. The older full backend
and final fixture correction remain composite evidence; the new exact-head full
hosted CI must pass before #89 is called merge-ready.

No tests/assertions removed, no new skips, no arbitrary timeout increase or merge
attempt. Operator approvals, original lease/attempt ownership, stop and native
append-only audit/outbox behavior remain intact. #91 is stacked on #89 and should
integrate this same reviewed infrastructure delta with a feature-branch merge so
its PR diff remains scoped to native Git inspection. Never merge into main.


Additional review repairs: `4207141494` adds individual checkout/setup/install/
upload limits without imposing an accumulated-runtime deadline on supervised
pytest. A workflow regression verifies every unguarded backend matrix step is
bounded. `4207354287` changes reservation lookup to the authoritative uniqueness
key (runtime run, repository alias). Fresh approval after an alias identity change
now returns SELF_BUILD_WORKSPACE_CONFLICT instead of an unhandled IntegrityError,
preserving the original single reservation and audit event. A native isolated
control-plane regression covers that exact trigger. New-head checks/review required.

Final review-repair validation: 76 workspace/CI cases pass, Ruff lint/format and
1,897-case collection coverage pass. Frontend and migration inputs are unchanged
from passing local gates. New exact-head hosted CI/re-review remain required.


Human merges #90 and #92 advanced main to de18e2aafd56eda98cd794b3883098cadd0be4c5.
The integrated cherry-picked CI history caused actual conflicts in workflow,
CI helper tests and backend CI documentation. Retained the feature's identical
reviewed CI content plus its step-budget repair, preserving all human-merged
validation planner files and combined shard assignments. Main/workspace/CI
integration: 168 tests pass, Ruff lint/format and 1,989-case collection coverage
pass. No merge into main was performed; new-head hosted checks required.

Additional findings 4207573276 and 4211289931: generated namespace ownership is
checked independently of alias uniqueness; canonical-identity-preserving alias
changes fail with SELF_BUILD_WORKSPACE_CONFLICT instead of a primary-key error.
Abandonment now requires separate exact operator approval and the original live
lease/runtime owner, with current RBAC, expiry, stop and version checks. Existing
reservation approval and version-only requests cannot authorize abandonment.
Public preview/approval contracts and docs advance together. New exact-head CI
and independent re-review remain required; no merge-ready claim is made.
