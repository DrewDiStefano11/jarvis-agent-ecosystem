# Durable source edit planning

`SourceEditingPlanService` persists bounded source proposals using the existing
Self-Build reservation, runtime identity/RBAC, task leases, emergency stop and
transactional audit/outbox. It adds no worker, scheduler, runtime state machine,
HTTP route, model observation importer, source reader or mutation adapter. This
milestone depends explicitly on the trusted source-change foundation in PR #101.
It uses merged reservation contracts; unmerged checkout implementations are not
dependencies. Source execution remains unavailable.

## Internal interface and trust

`persist(actor, request, observation, original_code_state)` accepts a typed
`PersistEditingPlanRequest` plus trusted source and CodeState observations. A
future fenced native caller must obtain those observations from the exact owned
workspace. Model text cannot assert filesystem safety, inventory completeness,
ownership or passing validation. This service neither reads nor writes source.
See [source contracts](source-change-contracts.md) for the observation boundary.

The request names an existing durable workspace, its exact version/plan hash,
an actor-scoped command ID and the immutable source intent. Its original-plan
and authorization references must equal the persisted reservation plan and its
original approval ID. Those references are provenance, not source-edit approval.
Repository identity/base commit must match the native reservation. Runtime/task
lineage, permission, enablement, emergency stop, ownership and current lease are
checked inside the existing fenced database write transaction. Runtime version
and active-attempt identity are recorded as admission evidence, not future grants.

## Persistence, replay and review

Alembic revision `20261012_source_plans` adds `source_edit_plans` after the merged
workspace reservation revision `20261007_13`. Foreign keys retain task, runtime,
workspace and actor ownership. The service only inserts records; there is no update
or deletion operation. Downgrade refuses while source-plan evidence exists.

Actor/command IDs derive deterministic record IDs. Exact command replay returns
the original record without another event. Changed source, observation or original
code state under the same command refuses. The SQLite write fence and unique
actor/command constraint arbitrate concurrent requests. Record, audit and outbox
commit atomically; a failure rolls back all three. Per workspace there are at most
32 records; serialized record size is bounded to 32 MiB in addition to source
contract file/count/aggregate limits.

Protected records retain exact proposed text, before-source observations, original
CodeState, projected target CodeState and publication ValidationPlan. Reads require
both runtime task-read and existing `self_build.workspace` resource permission.
They verify row/payload IDs, timestamps, request/plan hashes, current policy,
complete source-plan reconstruction, validation derivation and native reservation
references. Coherently rehashed foreign repository/base substitutions and impossible
future workspace versions are refused. Historical reads survive stop, abandonment
and process restart while authorized and policy-compatible; they grant no execution.

Source payloads belong only in this protected evidence table. They must not enter
logs, audit events or public diagnostics. SQLAlchemy exceptions can contain bound
source parameters, so storage failures become fixed `SOURCE_EDIT_STORAGE_FAILED`
errors without the native exception chain. Contract serialization also suppresses
malformed-input warnings before revalidation. All other refusals use fixed codes.

## Validation semantics

Projection merges source changes with **all** original workspace deltas. It retains
unrelated edits, checks the current identity of already changed files, removes a
delta when a previous addition is deleted or a previous change is reverted, and
handles restoring a prior deletion. Case aliases, stale original deltas, excessive
state size and unsafe existing output paths refuse. It never discards unrelated
changes to make tests appear applicable.

Every record derives publication validation, preserving backend Ruff/tests,
frontend typecheck/lint/tests/build, migrations and repository integrity; UI paths
also require browser acceptance. No passing validation result is imported or
invented. The persisted state is always `proposed`, evidence is `unmeasured`, and
`mutation_supported` is always false. Projected hashes describe expected future
bytes and are not evidence that those bytes exist or that commands have run.

## Event contract

`self_build.source_edit_plan.persisted` uses the existing schema-version `1.0`
EventEnvelope and task-lease audit/outbox sequence allocation. Payload fields are
`recordId`, `planHash`, `workspaceId`, `runtimeRunId`, `mutationSupported: false`
and `evidenceStatus: "unmeasured"`. It includes no source, paths, lease bearer,
commands or approval tokens. Exact replay emits nothing. Publication occurs only
through the existing committed outbox dispatch.

## Mutation gate

The separate adapter requires merged #93/#99 production checkout interfaces,
fresh exact-plan operator approval, existing workspace ownership, active-attempt
and lease fencing, durable intent before edits, interrupted-edit recovery, exact
before/after verification and native audit/outbox integration. It must never gain
shell/code execution or use model text as authority. This planning milestone does
not implement or substitute any of those side-effect boundaries.
