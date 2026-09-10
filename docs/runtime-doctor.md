# Jarvis runtime doctor

The runtime doctor answers one operator question with evidence: **is Jarvis ready to
run right now, and if not, what exactly is preventing it?**

It complements the existing supervisor `doctor` (which validates prerequisites
*before* a start) by diagnosing the **live runtime**: API, database, schema,
supervisor, worker, local model provider, readiness, workspace tools, emergency
stop, the Office state path, and recent critical failures.

The doctor is **read-mostly diagnostics**. It never:

- runs database migrations,
- creates or deletes databases,
- clears emergency stop,
- starts, stops, or kills processes,
- downloads or launches models, or performs inference,
- grants roles, permissions, or elevation,
- reads workspace file contents (only operator markers),
- mutates authentication or feature settings.

## Usage

From the repository root with the `apps/api` environment installed:

```powershell
# Fast operator checks (seconds, read-only)
python scripts\jarvis_doctor.py

# Same command through the Windows operator entrypoint
.\scripts\jarvis.ps1 runtime-doctor

# Machine-readable JSON on stdout
python scripts\jarvis_doctor.py --json

# Bounded deep checks (API contracts, DB integrity, cross-source state)
python scripts\jarvis_doctor.py --deep

# Write a diagnostic bundle into the repository root (git-ignored)
python scripts\jarvis_doctor.py --report

# Write the bundle somewhere else
python scripts\jarvis_doctor.py --report C:\temp\jarvis-case
```

On Linux/CI the same script works with `python scripts/jarvis_doctor.py` from the
repository root.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | all checks healthy |
| `1` | degraded or unknown conditions found (system may still run) |
| `2` | at least one blocking condition prevents normal operation |
| `3` | the doctor itself failed (never reported as healthy) |

## Status meanings

Every check reports exactly one status:

| Status | Meaning |
| --- | --- |
| `healthy` | the check passed with current evidence |
| `degraded` | the thing works but reliability or visibility is reduced (also used for `unknown` evidence) |
| `blocked` | the condition prevents normal operation; the reason and remediation say which |
| `disabled` | the feature is intentionally off by configuration — **not** a failure |
| `unknown` | no authoritative source could be consulted (for example the API is down) |

The **overall** status is `blocked` if any check is blocked, else `degraded` if any
check is degraded or unknown, else `healthy`. `disabled` never lowers the overall
status: a default-off worker or disabled workspace tools are reported as
`disabled`, not as failures.

A process merely existing is never reported as healthy: the doctor distinguishes
*process reachable* from *system ready* (for example, an API that answers TCP but
returns an invalid health payload is `degraded`, and a port occupied by a foreign
process is `blocked`).

## What is checked

Fast mode (`python scripts/jarvis_doctor.py`):

| Check | Group | What it verifies |
| --- | --- | --- |
| `repository_identity` | identity | Git SHA, worktree cleanliness, app version, environment mode, expected API/web endpoints, diagnostic timestamp |
| `configuration` | configuration | application `Settings` and supervisor configuration validate; feature-flag visibility (worker, model mode, tools, Ollama) |
| `api` | api | `/api/health` answers with the expected service identity and a healthy application status; distinguishes *not running* from *port occupied by another process* |
| `database` | database | configured SQLite file opens read-only, `alembic_version` matches the checkout's migration head, core tables exist |
| `emergency_stop` | emergency_stop | durable `system_state.emergency_stop` (authoritative), with API/supervisor state as fallback sources |
| `frontend` | frontend | the configured web endpoint responds |
| `supervisor` | supervisor | coordination-state ownership (running/stale/not running) and per-process state, health, restart and failure history for required children |
| `model_provider` | provider | enabled providers through the existing provider abstraction: service reachable, response well-formed, configured model present |
| `autonomous_worker` | worker | maps the worker to `idle` / `active` / `absent` / `stale` / `blocked` / `failed` using application health (`autonomousWorker` component), lease authority (`staleWorkerCount`, `expiredLeaseCount`), and supervisor process state |
| `workspace_tools` | workspace | when enabled: `JARVIS_TOOL_WORKSPACES_JSON` parses, directories exist, operator trust markers (`.jarvis-workspace.json`) validate — marker files only, never workspace contents |
| `office_runtime_path` | office | `/api/office` answers the runtime state contract |
| `recent_failures` | failures | recovery-required, exhausted outbox events, expired leases, stale workers, supervisor failure history |
| `planning_readiness` | readiness | why autonomous planning is blocked when it is blocked (configuration, API, database, emergency stop, provider/model, worker process, application reason codes) |

Deep mode (`--deep`) additionally:

- exercises the `/api/system/status` request-path contract;
- runs `PRAGMA quick_check` on the database;
- compares the running API's reported database revision with the configured
  database (a mismatch means the doctor and the API are looking at different
  databases);
- cross-checks the Office snapshot's emergency-stop flag against durable system
  state.

Deep mode is still bounded and safe: short timeouts, read-only queries, no
inference, no uncontrolled autonomous task is ever started.

## Report format

`--report` writes two files (both git-ignored by default):

- `jarvis-diagnostic.json` — the full machine-readable report;
- `jarvis-diagnostic.md` — a human-readable bundle.

Top-level JSON fields:

```json
{
  "schemaVersion": 1,
  "overall": "blocked",
  "mode": "fast",
  "generatedAt": "2026-09-08T12:00:00Z",
  "repository": "C:\\jarvis-agent-ecosystem",
  "gitSha": "…",
  "gitDirty": false,
  "appVersion": "0.1.0",
  "appEnv": "development",
  "apiEndpoint": "http://127.0.0.1:8000",
  "webEndpoint": "http://127.0.0.1:5173",
  "databaseRevision": "20260906_09",
  "readyToRun": false,
  "blockedReasons": ["api: …"],
  "checks": [
    {
      "name": "database",
      "group": "database",
      "status": "blocked",
      "reason": "database schema is behind or foreign: …",
      "remediation": "run the supported migration workflow from apps/api: python -m alembic upgrade head …",
      "identifiers": {"databasePath": "…", "databaseRevision": "…", "expectedRevision": "…"}
    }
  ]
}
```

`--json` prints the same payload to stdout. Attach these files to bug reports.

## Security and redaction rules

- Report fields are **allowlisted by construction**: checks only emit explicit
  scalar facts (revisions, ports, counts, state names, paths of configured
  endpoints/databases).
- On top of that, rendering scrubs (1) any value under a secret-named key
  (`secret`, `token`, `password`, `api_key`, `credential`, `authorization`,
  `cookie`) and (2) the verbatim values of secret-named environment variables,
  replacing them with `[redacted]`.
- The doctor never serializes the process environment, `.env` contents,
  Authorization headers, cookies, or arbitrary file contents.
- Configuration errors are reported with their validation messages only — never
  the offending values.

## Common failure states and remediation

| State | Meaning | Recommended action |
| --- | --- | --- |
| API blocked (not running) | nothing is listening on the configured API port | start the supported runtime: `.\scripts\jarvis.ps1 start` (supervised) or the documented local uvicorn/pnpm dev servers |
| API blocked (port occupied) | a process accepts connections on the port but does not answer the Jarvis health endpoint | stop the unrelated process with its own tooling or change `API_PORT`; the doctor never kills processes |
| DB migration behind | database revision is older than the checkout's migration head | from `apps/api`: `python -m alembic upgrade head` (the doctor never migrates automatically) |
| DB foreign revision | database revision does not exist in this checkout | confirm which checkout/database pairing is intended; see [migrations](migrations.md) |
| Provider unavailable | the local provider service cannot be reached | start/configure the supported local provider (Ollama remains independently managed) |
| Model missing | the provider works but the configured model is absent | install or select the model explicitly with the provider's own tooling, or point `JARVIS_MODEL_OLLAMA_MODEL` at an installed model |
| Provider malformed | the provider answered a response that violates its contract | check the provider version/response; report with `--report` output |
| Emergency stop | autonomous execution is administratively stopped | investigate the cause, then use the supported resume action (System → Resume in the web UI, or `POST /api/system/resume`); health tooling never clears it |
| Worker absent | the worker is enabled but no live worker heartbeat/process exists | start it through the supervisor (`.\scripts\jarvis.ps1 start`) or the documented manual command from `apps/api` |
| Worker stale | lease authority reports stale worker registrations or expired leases | verify the worker process is alive; expired leases recover automatically while the API runs |
| Supervisor stale | recorded supervisor PID no longer matches a live process | `.\scripts\jarvis.ps1 stop` then `.\scripts\jarvis.ps1 start`; stale state never kills a process |
| Workspace tools disabled | feature is off by configuration | expected default; enable via `JARVIS_TOOL_EXECUTION_ENABLED` only when intended |
| Workspace marker missing | a configured workspace lacks the operator trust marker | create `.jarvis-workspace.json` per [workspace tools](goal-mode/workspace-tools.md); the doctor never writes into workspaces |

## Limitations

- The doctor diagnoses the configuration **it can see** (repository `.env` files
  plus its process environment). If the running API was started with different
  environment values, deep mode surfaces the revision mismatch, but the doctor
  cannot enumerate another process's environment.
- Worker "stale" detection relies on existing lease/heartbeat authority and the
  supervisor's recorded state; the doctor never guesses from PIDs alone.
- The doctor supports the repository's SQLite deployment boundary. Other
  database URLs are reported as blocked with that reason.
- Deep mode's provider probe is the repository's existing loopback health check
  (`/api/tags`, `/models`). It performs no inference; there is no opt-in
  inference probe in this phase.
- Browser-level Office rendering is validated by the separate runtime-browser
  acceptance job, not by the doctor.

## Relation to the supervisor doctor

`.\scripts\jarvis.ps1 doctor` remains the **pre-start** prerequisite check (OS,
venv, Node, frontend build, ports, disk, Task Scheduler). `runtime-doctor` (or
`python scripts/jarvis_doctor.py`) is the **live-system** diagnosis described
here. They are complementary; neither replaces the other.
