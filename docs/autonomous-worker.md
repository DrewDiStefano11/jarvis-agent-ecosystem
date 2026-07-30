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
2. acquire exact task lease in `BEGIN IMMEDIATE`;
3. claim the runtime and begin/start its deterministic attempt;
4. persist `prepared` and a pre-call runtime checkpoint;
5. recheck lease, target identity, authorization, emergency stop, and cancellation before local model access;
6. persist `call_started`, perform one non-streaming local call, and persist `response_received`;
7. validate, or perform one repair call if the initial call returned text and budget remains;
8. recheck all live policy and fencing conditions, including target identity, immediately before durable result persistence;
9. atomically persist the validated result and `result_persisted` event;
10. checkpoint the durable result and mark finalization pending;
11. complete the fenced task with `model-execution:<execution-id>`;
12. complete the runtime attempt/run from that durable task commit;
13. mark the execution completed.

The `model_executions` row is keyed by a deterministic execution ID and has a unique `(runtime_run_id, runtime_attempt_id)` constraint. It stores validated content, provider/model identity, assembly and execution request hashes, result hash, bounded token/latency/request/cost metadata, normalized finish reason, stage, review flag, and timestamps. Reads and recovery recompute the canonical result hash and fail closed on a mismatch. The row never stores a key, authorization header, raw request/response, invalid output, repair prompt, source text, exception object, traceback, path, or hidden reasoning.

A model call cannot be transactional. A crash in `prepared`, `call_started`, or `response_received` may cause the local model to be called again after lease recovery. This is the documented duplicate-call window; exactly-once inference is not claimed. Once the validated result is durable, restart recovery completes the review or finalization path without calling the model again. A known pre-execution safety pause is itself a durable recovery marker: restart scans only autonomous `planning_review` runs paused with one of those bounded reason codes and no model-execution row, then requires current authorization and a fresh exact task lease before completing the task review transition. If another worker has already completed that task, recovery cancels the superseded autonomous runtime instead of retrying an impossible acquisition; expected lease loss remains a contained poll outcome. Recovery confirms an already-requested review pause, and an authoritative task failure closes the active attempt, runtime, and model-execution record instead of retrying forever. Task completion is the normal-result commit point: cancellation that revokes the lease first wins, while a crash after task completion safely resumes runtime completion only while emergency stop remains inactive. Deterministic runtime command IDs, processed-command hashes, unique result constraints, fenced task operations, and deterministic audit/outbox event IDs prevent duplicate durable results and duplicate completion records.

## Fencing, authorization, stop, and review

The task lease is the only execution ownership mechanism. Its worker ID and random token are revalidated before each call, after each call, before result persistence, and before the task completion commit. A background heartbeat renews the lease during inference. Audit/events retain only a SHA-256 fingerprint where a lease reference is needed.

The configured actor is authenticated through `IdentityService`; the established task-scoped runtime permissions are reevaluated by the runtime authorizer throughout execution and before an authorized result read. A task-scoped denial of either the read or the required recovery action makes only that queued or recovery candidate inaccessible for the current poll; stable recovery pagination continues to later authorized work without mutating or disclosing the denied row. Invalid or inactive worker identity, corruption, persistence failures, and unexpected authorization errors still fail the worker. Administrator denial and resource-policy denial retain their existing precedence.

Emergency stop blocks acquisition, model calls, result commit, and terminal completion. A response that arrives after stop activation is discarded. Cancellation similarly blocks commit and enters the existing runtime cancellation boundary. Lost ownership abandons the active runtime attempt on a bounded best-effort basis and never stores generated output.

A valid result with `requiresHumanReview=true`, or exhausted output repair, pauses the runtime and moves the fenced task to `under_review`. Recovery preserves this branch even if the process exits immediately after result persistence. The model cannot approve itself or clear this state.

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
