# Workspace tools integration checkpoint

Status: backend implementation checkpoint for integration and fresh CI/review; this
is not a final release acceptance claim. Root integrates the Business Lab/frontend,
then runs the combined gates and actual local-model/browser smoke.

The existing planning worker can produce `workspace_plan_json_v1`. After explicit
operator review, the authorize endpoint binds the result hash and narrowed workspace
scope to a new task and runtime. The worker executes up to eight fixed list/read/write/
report steps and stores observations, artifacts, runtime checkpoints, audit and outbox
records. Original planning history is preserved.

## Local configuration

Tools are disabled by default. In addition to the existing autonomous local-only
worker configuration, set `JARVIS_TOOL_EXECUTION_ENABLED=true` and
`JARVIS_TOOL_WORKSPACES_JSON` to an object mapping workspace aliases to absolute
operator-selected directories. Example value: `{"lab":"C:\\workspaces\\lab"}`.
Do not use the repository, a home directory or a credentials directory as a workspace.
Create ordinary `inputs` and `reports` directories and an explicit
`.jarvis-workspace.json` marker in that workspace:

```json
{"schemaVersion":"1.0","workspaceId":"lab"}
```

Optional marker `allowedTools`, `readPrefixes` and `writePrefixes` narrow the configured
boundary. Defaults permit the four bounded tools with reads in `inputs` and writes in
`reports`; each operator authorization may narrow this further. The application never
creates the trust marker automatically. Removing/changing it revokes later operations.
File operations reject traversal, absolute paths, hidden/credential paths, reparse
points, symlinks and multiply linked ordinary files. The workspace lock serializes Hub
operations. This is a boundary for trusted operator workspaces, not isolation from a
hostile native process running as the same OS user.

Writes are create-only unless the reviewed step supplies the current content hash.
Atomic replacement and desired-hash replay handle interrupted writes. Files and output
are at most 64 KiB, listings at most 100 entries (and the authorized byte limit), and a
plan at most 8 steps. Artifacts store content snapshots; reading artifacts remains
available after API restart without accessing the workspace again.

## Checkpoint evidence

- Core fixture acceptance: eight cases passed, including source preservation, duplicate
  authorization, all three real filesystem steps, exact API restart readback, changed
  hash/scope rejection, interrupted setup, corrupt record rejection, safe downgrade, and source-only reader denial.
- Write recovery: an injected process interruption after actual atomic replacement but
  before the database commit left a durable started step. Restart adopted the existing
  desired hash (`written=false`), produced one artifact and three checkpoints, and
  completed the task/runtime without executing earlier read/list steps again.
- Registry: Windows focused tests include real junctions and hardlinks, marker revocation,
  scope narrowing, bounded I/O and overwrite/replay semantics. A symlink-race case is
  skipped when the OS account lacks symlink creation privilege.
- Migration08 descends the integrated office revision07. Downgrade refuses to erase
  nonempty tool authorization/artifact history.

Focused checkpoint validation: 212 impacted backend tests passed with one OS-dependent
symlink-privilege skip; five independent recovery tests and the additional source-only
reader regression passed. Ruff lint and formatting pass across all 126 backend files.

Remaining release gates: combined frontend/backend validation, integrated actual local
model → reviewed plan → explicit authorization → artifact browser acceptance, exact-head
CI and fresh review. No cloud execution or model downloads are part of this checkpoint.
