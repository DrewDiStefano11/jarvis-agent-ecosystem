# Local autonomous planning/review worker

## Boundary

Phase 2C adds one real autonomous path: a dedicated local process may consume an explicitly queued Agent Runtime run whose `autonomous_execution.execution_type` is `planning_review`. It acquires the run's exact task through the existing fenced lease service, loads the one explicitly referenced completed Context Assembly, calls one structurally loopback provider, validates a fixed result, stores it once, and completes or pauses the runtime and task.

The worker is disabled by default. It has no tool schema or tool-calling transport. It does not read files, execute code or shell commands, follow URLs, browse, call GitHub, use external connectors, send messages, create agents, alter authorization, or interpret generated text as approval. Remote models are outside this phase.

## Explicit eligibility

A run is executable only when all of these facts remain true:

1. The durable runtime run is explicitly queued.
2. Its execution type is exactly `planning_review`.
3. Its immutable specification names one Context Assembly ID and schema version `1.0`.
4. The assembly belongs to the same task, is `completed`, contains a typed `modelRequest`, and has matching assembly, manifest, and request hashes.
5. The target identity and worker identity are active and enabled.
6. The worker actor is currently authorized for the task-scoped runtime operation.
7. The task is queued or retrying, has no unsatisfied `requires` dependency, and can be leased.
8. Emergency stop is inactive and cancellation has not begun.
9. No committed result exists for the runtime attempt.

Ordinary tasks, non-planning runs, old runs without `autonomous_execution`, review-required assemblies, missing references, and “latest assembly” guesses are never eligible. Runtime command idempotency hashes include the execution specification, so replaying a create command with a different assembly or budget conflicts.

## Fixed result

The only accepted output is the typed `PlanningReviewResult` version `1.0`:

```json
{
  "schemaVersion": "1.0",
  "summary": "Concise summary",
  "analysis": "User-facing detailed analysis",
  "recommendations": [
    {
      "title": "Recommendation",
      "description": "What should be done",
      "priority": "low"
    }
  ],
  "risks": [
    {
      "title": "Risk",
      "description": "Why it matters",
      "severity": "high",
      "mitigation": "Suggested mitigation"
    }
  ],
  "assumptions": ["Explicit assumption"],
  "missingInformation": ["Missing information"],
  "requiresHumanReview": false
}
```

Unknown fields fail validation. Every string, list, nested record, priority, schema version, and response size is bounded. JSON must occupy the full response; markdown fences and surrounding prose are invalid. Text that asks for shell, browser, tool, approval, or permission activity remains inert result data.

## Local-only model gate

`JARVIS_MODEL_EXECUTION_MODE` has two values:

- `disabled` (default): execution and provider network health/model-list traffic fail closed.
- `local_only`: only providers whose parsed hostname is `localhost` (optional terminal dot) or a loopback IP are eligible.

Private-LAN IPs, Docker service names, `host.docker.internal`, deceptive localhost suffixes, public DNS names, and remote HTTPS endpoints are rejected. `JARVIS_MODEL_ALLOW_REMOTE` cannot override the worker's `allow_remote=false` routing requirement. Production HTTP clients disable redirects and proxy-environment inheritance, so a local request cannot be transparently rerouted by `HTTP_PROXY` or a redirect.

Ollama is the supported smoke-test adapter. A loopback OpenAI-compatible adapter may be configured, but remains subject to the same structural locality, secret, health, and request contracts. No endpoint, installed model name, key, or machine-specific `.env` is committed.

## Lifecycle and staged recovery

The normal state flow is:

1. scan queued autonomous runs in stable order, skipping tasks that cannot currently be leased;
2. acquire the exact task lease in `BEGIN IMMEDIATE`;
3. claim the runtime and begin its deterministic attempt, leaving the runtime in `STARTING`;
4. recheck current read/execute authorization, target lifecycle, emergency stop, cancellation, worker configuration, and the exact lease; then durably persist the deterministic `prepared` `model_executions` row **while the runtime is still `STARTING`**;
5. recheck those live fences again, then commit the attempt-start transition to `RUNNING`; the runtime command also checks emergency stop in its ledger transaction;
6. checkpoint the prepared execution, recheck live policy, and only then permit local model access;
7. persist `call_started`, perform one non-streaming local call, and persist `response_received`;
8. validate, or perform one repair call if the initial call returned text and budget remains;
9. recheck all live policy and fencing conditions, including target identity, immediately before durable result persistence;
10. atomically persist the validated result and `result_persisted` event;
11. checkpoint the durable result and mark finalization pending;
12. complete the fenced task with `model-execution:<execution-id>`;
13. complete the runtime attempt/run from that durable task commit;
14. mark the execution completed.

The durable-ordering invariant is: an autonomous `planning_review` runtime never durably enters `RUNNING` unless its deterministic execution row for that same runtime attempt is already committed. `prepared` is therefore sufficient recovery state for both a `STARTING` runtime (crash before attempt start) and a `RUNNING` runtime (crash immediately after attempt start). The worker does not use a broad `RUNNING` scan: recovery is limited to autonomous `planning_review` requests with the validated immutable execution specification, either through their prepared execution row or, for the earlier no-row window, through the narrow `CLAIMED`/`STARTING` transitional scan.

The `model_executions` row is keyed by a deterministic execution ID and has a unique `(runtime_run_id, runtime_attempt_id)` constraint. It stores validated content, provider/model identity, assembly and execution request hashes, result hash, bounded token/latency/request/cost metadata, normalized finish reason, stage, review flag, and timestamps. Reads and recovery recompute the canonical result hash and fail closed on a mismatch. The row never stores a key, authorization header, raw request/response, invalid output, repair prompt, source text, exception object, traceback, path, or hidden reasoning.

A model call cannot be transactional. A crash after `call_started` (which is recorded immediately before a provider call) or `response_received` and before validated-result persistence may cause the local model to be called again after lease recovery. A crash in `prepared`, including one immediately after the `RUNNING` transition but before the first provider call, has the already-committed marker but no prior local call to repeat. These are the only documented pre-result duplicate-call windows; exactly-once inference is not claimed. Exactly-once **durable result persistence** is enforced by the deterministic execution ID, the unique `(runtime_run_id, runtime_attempt_id)` constraint, result hash conflict checks, and fenced result transaction.

Recovery first reconciles known pre-execution safety pauses. It then scans prepared/call-started/response-received execution rows, reauthorizes the exact runtime and reacquires its exact task lease, and resumes the same deterministic runtime attempt. A prepared row with a `STARTING` runtime commits only the pending start transition; a prepared row with a `RUNNING` runtime continues through the established uncommitted-execution path. It never creates a second runtime attempt or conflicting execution row. The narrow no-execution-row scan handles only autonomous `planning_review` runs stranded in `CLAIMED` or `STARTING` before preparation; it rechecks current authorization, target lifecycle, worker lifecycle, emergency stop, cancellation, and exact task ownership before resuming. No provider call is possible in that earlier window.

If another worker has already completed the authoritative task, recovery cancels the superseded autonomous runtime without overwriting the completed task or its result. Authoritative cancellation wins: recovery cancels the runtime before marking the execution terminal, and an authorization denial leaves the execution recoverable. An authoritative failed task closes the active attempt, runtime, and execution instead of retrying forever. Recovery confirms an already-requested human-review pause, preserves a model-requested review, and resumes finalization after task completion only while emergency stop remains inactive. Expected per-run lease loss, cancellation, stop, and task-scoped authorization outcomes are contained by the polling loop; unexpected failures still propagate. Deterministic runtime command IDs, processed-command hashes, unique result constraints, fenced task operations, and deterministic audit/outbox event IDs prevent duplicate durable results and duplicate completion records.

## Fencing, authorization, stop, and review

The task lease is the only execution ownership mechanism. Its worker ID and random token are revalidated before each call, after each call, before result persistence, and before the task completion commit. A background heartbeat renews the lease during inference. Audit/events retain only a SHA-256 fingerprint where a lease reference is needed.

The configured actor is authenticated through `IdentityService`; the established task-scoped runtime permissions are reevaluated by the runtime authorizer throughout execution and before an authorized result read. A task-scoped denial of either the read or the required recovery action makes only that queued or recovery candidate inaccessible for the current poll; stable recovery pagination continues to later authorized work without mutating or disclosing the denied row. Authorization revoked while a provider request is in flight is likewise contained to that run: the generated response is discarded, the durable nonterminal execution remains available for authorized recovery, and polling continues. If task cancellation is already durable but runtime cancellation is no longer authorized, the execution is not marked terminal until runtime cancellation succeeds. Invalid or inactive worker identity, corruption, persistence failures, and unexpected authorization errors still fail the worker. Administrator denial and resource-policy denial retain their existing precedence.

Emergency stop blocks acquisition, model calls, result commit, and terminal completion. Runtime completion rechecks and locks the durable stop state inside the same ledger transaction as each terminal transition, closing the gap between the worker precheck and commit. A response that arrives after stop activation is discarded. Cancellation similarly blocks commit and enters the existing runtime cancellation boundary. Lost ownership abandons the active runtime attempt on a bounded best-effort basis and never stores generated output.

An operating-system shutdown signal interrupts an active `run_once` await instead of waiting for the
model timeout. The process then executes its existing finalizer to stop the durable worker
registration and dispose the database engine within the supervisor's graceful-shutdown window.

A valid result with `requiresHumanReview=true`, or exhausted output repair, pauses the runtime and moves the fenced task to `under_review`. Recovery preserves this branch even if the process exits immediately after result persistence. The model cannot approve itself or clear this state.

A validated result that does not require human review is then evaluated by the deterministic local review policy described in `docs/planning-review-orchestration.md`. The structured outcome — accepted, one bounded revision, or escalation — is durably recorded as a runtime checkpoint for that exact attempt and governs the terminal transition. Generated text remains stored explanation and never authorizes anything.

## Configuration and local command

Required worker settings are:

```text
JARVIS_AUTONOMOUS_WORKER_ENABLED=false
JARVIS_AUTONOMOUS_WORKER_ACTOR_ID=
JARVIS_AUTONOMOUS_WORKER_INSTANCE_ID=
JARVIS_AUTONOMOUS_WORKER_POLL_INTERVAL_MS=1000
JARVIS_AUTONOMOUS_WORKER_MAX_CONCURRENCY=1
JARVIS_AUTONOMOUS_WORKER_LEASE_SECONDS=60
JARVIS_AUTONOMOUS_WORKER_HEARTBEAT_INTERVAL_SECONDS=15
JARVIS_AUTONOMOUS_WORKER_MAX_EXECUTION_SECONDS=300
JARVIS_AUTONOMOUS_WORKER_MAX_REPAIR_CALLS=1
JARVIS_MODEL_EXECUTION_MODE=disabled
```

Concurrency must be exactly one. Enabling the worker without `local_only`, a configured actor/instance, or a safe heartbeat-to-lease ratio fails settings validation.

Run the worker separately from `apps/api`:

```powershell
.\.venv\Scripts\python.exe -m app.autonomous_worker
```

API startup never starts it automatically. It registers the stable instance, heartbeats while idle, executes at most one task, sleeps at the bounded poll interval, and stops cleanly.

## Optional Ollama smoke test

This path is manual and must not run in CI:

1. Start the API and local Ollama separately.
2. Install a model locally without committing its name.
3. In an uncommitted `.env`, enable Ollama with a loopback URL and local model.
4. Set model execution mode to `local_only`.
5. Create/activate the worker identity and grant the required task-scoped runtime permissions.
6. Set the actor and stable worker instance IDs, then enable the worker.
7. Create a task and a completed Context Assembly for it.
8. Create and queue a `planning_review` runtime run referencing that exact assembly.
9. Start `python -m app.autonomous_worker`.
10. Inspect the task lease, runtime ledger/checkpoints, model execution result, audit history, and task completion.
11. Activate emergency stop and confirm the worker stops acquiring work and cannot commit an in-flight response.

## Health, API, and rollback

`/api/health` and `/api/system/status` expose the same bounded autonomous-worker component: enablement, local execution mode, provider configuration readiness, the actual eligible queued runtime count, active/completed/failed/review counts, last worker heartbeat, and last successful execution. Disabled-by-default is healthy. When enabled, at least one active autonomous worker must have a heartbeat within its lease window; a missing, stopped, or stale worker degrades health with `autonomous_worker_unavailable`. An enabled worker with no eligible local provider or other stale infrastructure also degrades health without exposing secrets.

`GET /api/model-executions?taskId=...` and `GET /api/model-executions/{id}` require the established `X-Jarvis-Actor-Id` boundary and reauthorize access to the run's task. There is no arbitrary prompt-completion endpoint.

To roll back code safely, disable the worker first and let active leases settle. Alembic downgrade to `20260729_04` is allowed only when `model_executions` is empty; it refuses to destroy committed results. Back up the local database before any deliberate data removal.
