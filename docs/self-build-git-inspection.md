# Native repository metadata inspection

Self-Build's native Git observer verifies the registered primary repository and
operator-attested base using real, fixed Git read commands. It does not create a
worktree, checkout source, modify the primary index, fetch, reconcile, commit, push,
open a PR or merge. Workspace cleanliness remains `unobserved`; committed metadata
is not evidence about uncommitted edits.

The operator configures an absolute external Git executable and its SHA-256 with
`JARVIS_SELF_BUILD_GIT_EXECUTABLE` and `JARVIS_SELF_BUILD_GIT_SHA256`. Defaults are
empty, so inspection is unavailable. The executable must be outside both registered
roots, ordinary (no reparse/symlink), and match the pinned content before every
command. Vendor-installed executable hard links are allowed only at this trusted
external boundary; repository metadata retains the single-link rule.

Read families are hardcoded: repository top level/common directory, HEAD, exact
local base commit/tree, local origin tracking ref, local origin URL and committed
tree inventory. No arbitrary arguments, executable, shell string, aliases, hooks,
filters, model command text or network transport are exposed. The subprocess has
an environment allowlist, global/system config suppression, optional locks disabled,
fsmonitor/hooks disabled, lazy fetch disabled, credential prompts disabled and
protocols denied. Origin is read without local config includes and converted to a
credential-free canonical identity; raw URL/stderr are never returned or persisted.

Primary `.git` must be an ordinary contained directory. Native pinned-directory
primitives protect its directory ancestry during observation; symlinks/junctions,
external alternate object stores, ambiguous tree paths, Git links and symbolic-link
tree entries fail closed. Metadata is bounded to 16,384 entries, tree inventory to
4,096 ordinary files, stdout to one MiB and stderr to 64 KiB. Inactivity is measured
from output progress; no arbitrary overall runtime deadline ends healthy progress.
Volatile refs and directory identity are rechecked before returning coherent evidence.

The API previews one exact `git.repository.inspect` plan bound to workspace/version,
reservation plan, repository/base, inspection implementation/contract policy digest
and opaque executable-path/content identity. Changes to that policy invalidate
previous approvals.
Preview grants no command authority. Inspection requires separate approval on the
existing authenticated HTTPS operator surface:

- `POST /api/self-build/workspaces/{id}/repository-inspections/preview` previews
  metadata through the existing local runtime actor boundary.
- `POST /api/remote/self-build/workspaces/{id}/repository-inspections/preview`
  allows the configured authenticated operator to inspect the same plan.
- `POST /api/remote/self-build/repository-inspections/approve` binds the exact plan
  hash to an expiring (at most one-hour) operator audit/outbox approval.
- `POST /api/self-build/workspaces/{id}/repository-inspections` requires that
  approval ID/hash, original worker ID and current native lease token.
- `GET /api/self-build/workspaces/{id}/repository-inspections/{inspection_id}`
  reads immutable measured evidence under task-read authority, including after
  configuration changes; stored evidence does not grant current execution authority.

The operator and worker need explicit task-scoped `self_build.git.inspect` as well
as native runtime/workspace authority. Approver must be the configured remote
operator with live `remote.control`, different from the implementation/runtime
actor. Credentials stay in transport configuration, outside model payloads. A
reservation approval cannot authorize Git reads. These reads cannot authorize later
filesystem/Git mutation or commands.

Every native read rechecks current policy, permission, runtime/attempt ownership,
original lease, emergency stop, exact plan/tool and approval expiry. A running read
rechecks authority at least every half second and kills only its own process when
revoked. No SQLite write lock spans the process. Before evidence commits, the same
fence runs again; revoked/stale work produces no successful observation. The native
append-only audit and transactional outbox retain plan/approval/worker, measured
start/finish, exact base/tree/HEAD/local tracking SHA, file count/inventory digest and
tool digest together. There is no parallel task/lease/recovery framework.

`origin_evidence_kind=local_tracking_ref` explicitly means the local
`refs/remotes/origin/<base_branch>` value. `base_matches_origin_tracking_ref` is
neither a fresh GitHub observation nor a conflict/mergeability assertion. No fetch
occurs. A stale or differing local tracking ref is evidence for later lifecycle
policy, not an automatic merge conflict.

This is an A/D inspection increment, not the complete development command journal.
Per-command measured artifacts, worktree creation intent/acknowledgement recovery,
source edits, bounded test execution and native publication remain subsequent work.
No production Self-Build dogfood/merge-ready factory claim is made yet.
