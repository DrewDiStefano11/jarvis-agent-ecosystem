# Authorized native commit preparation

This increment builds on merged source-planning PR #103 and explicitly depends
on this campaign's verifier/proposal PRs #107 and #109. It adds native HTTP
contracts, authorization, append-only preparation intent, transactional outbox
events and runtime checkpoint recovery. It introduces no migrations, staging,
Git object/ref writes, commit creation, shell commands, push or PR effects.

Self-Build remains disabled by default. Both builder and configured authenticated
operator require task-scoped `self_build.git.commit.prepare`, existing workspace
and Git-inspection permissions, and native task/runtime authority. The builder
must be the original workspace/runtime actor and retain the original worker and
lease. Running native attempts, active identity, current policy, registered owned
workspace, acknowledged creation checkpoint and emergency stop are checked.

The service resolves a protected `SourceEditingPlanService` record by its native
ID, verifies immutable source/reservation provenance, and checks the original
`self_build.source_edit_plan.persisted` audit event's actor, task, workspace, run
and plan hash. Model descriptions, submitted observations or caller-asserted
checkpoint references cannot substitute for that record.

Two independent approvals avoid reading changed source before operator consent:

1. `POST /api/self-build/workspaces/{id}/commit-preparation/inspection/preview`
   derives a metadata-only `git.commit.inspect` scope from ready ownership,
   creation checkpoint, source record/hash, actor/run/attempt/worker, pinned tool,
   selection and exact commit metadata. It launches no native Git process.
2. `POST /api/remote/self-build/commit-inspections/approve` requires authenticated
   HTTPS operator access, a different identity from the builder, and the exact
   inspection-plan hash. Its native audit/outbox grant expires within one hour.
3. `POST /api/self-build/workspaces/{id}/commit-preparation/preview` requires the
   inspection grant/hash before any Git read. It obtains the complete pinned
   inventory and derives the exact prospective tree, commit and authorization
   plan. Changed source, index, policy, owner or tool is refused.
4. `POST /api/remote/self-build/commit-preparations/approve` repeats the authorized
   measurement and approves the exact resulting plan hash independently.
5. `POST /api/self-build/workspaces/{id}/commit-preparation` requires both grants,
   the exact resulting hash, an original lease token and a bounded command ID.
   It repeats measurement, persists immutable intent before checkpointing, and
   returns `checkpointed` only after the native checkpoint and journal
   acknowledgement are verified.
6. `GET /api/self-build/workspaces/{id}/commit-preparation/{command_id}` reads
   protected original evidence without requiring a live execution lease. A
   missing/unacknowledged checkpoint is never presented as successful recovery.

Every fixed Git command and long-running read rechecks native authority, both
applicable approval scopes, original source-record integrity and the actual
creation checkpoint. No SQLite write transaction spans a subprocess. Source,
Git metadata and ownership pins remain held through intent/checkpoint publication.
Native transaction guards recheck authority in their existing transaction and
call the filesystem-only guard; they do not open a nested SQLite writer. This
private callback choice is not a model/API parameter.

The journal reuses native audit/outbox records rather than adding a duplicate
orchestration table. Preparation is keyed by workspace, original actor and exact
command ID, bounded to 32 intents per workspace. Changed requests cannot reuse a
command. Original approval IDs remain immutable; renewed current grants are
recorded on acknowledgement. Native checkpoint metadata binds operation,
workspace, exact plan and original approvals; its integrity digest binds the
complete immutable intent. Recovery before a checkpoint or after a checkpoint
but before acknowledgement reuses the original operation and checkpoint.

Returned authorization plans are capped at 128 KiB. Raw source bytes, lease
tokens, private ownership nonces, executable paths and command stderr are not
persisted in preparation evidence. Commit messages and author metadata are exact
operator-reviewed proposal data. Original source-edit and creation references
remain inspectable.

The source record is a proposed editing plan. Independently measured files may
match it, but that does not prove a native source mutation ran or recovered.
`source_edit_checkpoint_id` is explicitly absent until a genuine applied-edit
checkpoint can be resolved. Old non-null proposal references remain readable.
`commit_creation_supported` remains false, and there is no commit-creation route.
Later native creation must verify applied-source recovery position and its full
ownership/authorization boundary before writing any Git object or ref.

Local regression evidence covers real owned workspaces and isolated migrated
databases: exact replay, unchanged Git index/ref/source, atomic audit/outbox,
checkpoint crash gaps, missing checkpoint refusal, both approval expiries,
self-approval refusal, permission/lease/stop revocation, changed source,
pre-inspection refusal and authenticated HTTP/OpenAPI contracts. A separate
reviewer has not audited this increment; remaining risk is native effect and
applied-source recovery integration. No Codex review is requested while quota
is exhausted. All PR merges remain human decisions.
