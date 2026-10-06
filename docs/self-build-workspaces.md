# Development workspace reservations

Self-Build currently supplies a durable **reservation** boundary. It does not yet
create Git branches/worktrees, inspect repository HEAD, edit source, run tests,
push, open PRs, or grant a worker those powers. `reserved` and `unobserved` are
literal state; they must not be displayed as an active verified checkout.

The operator configures `JARVIS_SELF_BUILD_ENABLED=true` and
`JARVIS_SELF_BUILD_REPOSITORIES_JSON` locally. Defaults are false and `{}`. The
registry maps a stable repository alias to an object with `repository_identity`
(canonical lowercase `github.com/owner/repository`), `primary_root`, `worktree_root`
and `base_branch`. Both roots must already exist, be absolute ordinary directories,
have no symlink/junction ancestor, and be disjoint. Duplicate canonical identities
and overlapping roots across registrations fail closed. Keep worktree roots dedicated
to Jarvis missions. No machine paths or credentials belong in committed configuration.
This is trusted local operator configuration, not repository/model-supplied policy.
A subsequent Git adapter must verify the claimed repository identity and base SHA.

The typed loopback HTTP API reuses `X-Jarvis-Actor-Id` and the existing envelope:

- `POST /api/self-build/workspaces/preview`: repository alias, runtime run ID and
  operator-attested exact base SHA produce a bounded plan. The plan exposes a
  generated branch/key and policy digest, never absolute paths.
- `POST /api/self-build/workspaces/reserve`: the same intent plus
  `expected_plan_hash`, owning `worker_id` and the live `lease_token` reserve one
  workspace. A read-only runtime grant cannot authorize this mutation; explicit
  task-scoped `self_build.workspace` and `runtime.execute` permissions are also required. The hash confirms
  the exact preview, including repository identity, task/run, base and root policy.
- `GET /api/self-build/workspaces/{id}`: authorized durable ownership and recovery
  state, even if configuration later changes or disables Self-Build.
- `POST /api/self-build/workspaces/{id}/abandon`: explicit workspace permission and
  expected version record abandonment. This never deletes files or a branch,
  revokes a lease, resumes a run or approves subsequent tools.

Mutation authorization, emergency stop, current runtime/task lineage and native
lease fencing are evaluated under the existing SQLite `BEGIN IMMEDIATE` boundary.
One transaction stores the reservation, append-only audit and transactional outbox
using the existing event-session sequence. Exact repeat/concurrent reservation
converges to one record/event. Conflicting base/policy or abandoned ownership cannot
silently create another workspace. Lease expiry/successor ownership, cancellation,
pause and changed policy surface recovery requirements; recovery never adopts a
successor or touches files automatically. Private persisted policy retains the
original roots after configuration changes. Lease tokens are never persisted in
reservation payloads/audits/events; only fingerprints are stored.

Alembic `20261006_12` follows `20260907_11`. Empty downgrade/re-upgrade is supported;
populated downgrade refuses to destroy workspace ownership history, even abandoned
records. OpenAPI and `app.models.self_build` are authoritative. Existing planning,
workspace-tool and runtime schemas/permissions remain compatible.

Next increment: a reviewed native Git worktree adapter with durable creation intent,
verified repository/base identity, root marker/containment, uncertain-acknowledgement
recovery, current authority fencing and safe cleanup. Filesystem mutation remains
unavailable until that path and its Windows regressions are implemented.

