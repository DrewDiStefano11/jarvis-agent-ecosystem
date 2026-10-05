# Authenticated remote operation

Remote control is disabled by default. Explicit remote mode exposes `/api/remote`
using native identities, task/runtime RBAC, leases, checkpoints, audit and outbox.
It does not enable autonomous execution or grant authority to model text.

## Local configuration

Before enabling remote mode, create an active, enabled operator through existing
local identity administration. Define and assign these permissions with resource
`administrative_function` / `remote_control`:

| Permission | Operations |
| --- | --- |
| `remote.submit` | Idempotent goal submission |
| `remote.read` | Goals, planned graphs, audit, runtime runs and results |
| `remote.control` | Pause/resume/cancel and system stop/resume |
| `remote.runtime` | Global status and active identity registry |

Reads also require task-scoped native `runtime.read`. A correction
submission requires `remote.read` and native `runtime.read` for its source,
checked again at commit before inheriting its project and lineage. Cancellation
requires `runtime.cancel`; pause/resume requires `runtime.pause`. Existing native
runtime administration retains its semantics. System stop/resume additionally
requires native `system.control` scoped to `administrative_function` /
`system_control`. Permission definitions supply the authoritative actions.

Set `JARVIS_REMOTE_CONTROL_ENABLED=true`, `JARVIS_REMOTE_OPERATOR_ID` to the
existing identity ID, `JARVIS_REMOTE_OPERATOR_TOKEN` to a generated base64url
secret with at least 32 random bytes (43–128 unpadded characters), and
`JARVIS_REMOTE_ORIGIN` to the exact credential-free HTTPS origin, for example
`https://jarvis.example:8443`. Credentials belong in the deployment environment,
never source control, URLs or CLI arguments. Length checks do not prove entropy.
`JARVIS_REMOTE_REQUESTS_PER_MINUTE` defaults to 120 and permits 1–600.
Use native database configuration/migrations. This initial mode supports SQLite
and one server process; other databases are rejected rather than claiming
unproven transaction fencing. Identity administration remains local only.

## Direct TLS server and verified client

From `apps/api`, launch using an existing certificate and private key:

```powershell
python -m app.remote_control --host 127.0.0.1 --port 8443 --cert C:\operator\server.pem --key C:\operator\server-key.pem
```

The default binding is loopback. A network binding is an explicit deployment
choice. The certificate must cover the configured host. The server requires real
HTTPS and exact Host matching; it rejects Forwarded/X-Forwarded headers and has
no plaintext fallback. Reverse proxy TLS assertions are unsupported. Remote mode
blocks legacy routes including health/docs/OpenAPI/identity administration,
WebSockets and browser Origin requests. Its outer gateway checks CORS preflight
before CORS middleware. Inspect authoritative OpenAPI locally, not on this listener.

Set `JARVIS_REMOTE_ORIGIN` and `JARVIS_REMOTE_OPERATOR_TOKEN` on the operator
client. Use the system trust store or an explicit trusted CA:

```powershell
python -m app.remote_control.client --ca C:\operator\trusted-ca.pem goals
python -m app.remote_control.client --ca C:\operator\trusted-ca.pem submit --file C:\operator\goal.json --idempotency-key operator-goal-001
python -m app.remote_control.client --ca C:\operator\trusted-ca.pem goal task-id
python -m app.remote_control.client --ca C:\operator\trusted-ca.pem cancel-goal task-id
python -m app.remote_control.client --ca C:\operator\trusted-ca.pem emergency-stop
python -m app.remote_control.client --ca C:\operator\trusted-ca.pem system-resume
```

Goal JSON uses native `CreateTaskRequest`:

```json
{"title":"Investigate a regression","description":"Identify failing behavior and supporting evidence"}
```

Other operations: `agents`, `runs`, `run`, `graph`, `audit`, `result`, `status`,
`command --file`. Command files use native immutable command ID, run ID, timestamp
and expected run version. Only `request_pause`, `resume` and
`request_cancellation` are accepted. Cancellation requester must match the
operator. Worker confirmations/completion are unavailable remotely. Reuse the
same saved request after acknowledgement uncertainty, including command ID.
The client verifies certificates, ignores proxy environment inheritance, rejects
redirects, bounds input files and avoids echoing credential-bearing errors.
Successful responses print authorized domain data.

## Durable behavior and limits

Submission creates a native queued task with authenticated creator attribution;
it does not automatically assemble a team or admit a model run. Graph/result
inspection returns existing records, possibly null, and does not imply executable
coordinator nodes. Every write rechecks remote and native permission in its fenced
transaction. Suspension/revocation applies without restart. A suspended operator
does not prevent native worker composition on restart. Registry identity audit
attribution stores no credential. Events enter the native transactional outbox;
schema versions and sequence semantics remain unchanged.

Emergency stop commits its native durable flag, audit and event without flushing
stale API task cache over worker state, and fences completion. Clearing stop does
not automatically resume simulation or forge worker acknowledgement. Duplicate
desired-state system controls return current status. A committed stop survives
lost acknowledgement, and retries do not duplicate its event. Runtime controls
retain native version/CAS and idempotent command replay.

The process rate budget includes failed authentication/denied requests. Actual
streamed bodies are limited to 64 KiB within five seconds. The launcher limits
concurrency to 16 and idle keepalive to five seconds. Goal/agent/audit page limits
are 1–100 and offsets 0–100000; goal filtering scans at most five database pages
per request and exposes no unauthorized count. Responses use `Cache-Control: no-store`.

## Acceptance boundaries

Tests exercise native SQLite/RBAC/leases/runtime/outbox and application HTTP.
A real loopback Uvicorn TLS listener with isolated temporary certificates proves
submission, inspection, cancellation, system stop/resume, transport/identity
spoof rejection, legacy isolation and the certificate-verifying CLI. Native
runtime pause/confirmation/resume/cancel tests use deterministic local worker
confirmation. No public deployment, production credential, downloaded model or
arbitrary-model reasoning quality is claimed by these fixtures.
