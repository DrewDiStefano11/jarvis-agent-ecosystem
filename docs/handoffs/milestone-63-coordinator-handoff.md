# Milestone #63 — Production Coordinator / Synthesis / Retry

## Reconciliation

- Branch: `codex/coordinator-synthesis-retry`.
- Original PR head: `b48b90dc38047fc1ca8d188c552d740dd98d3bc2`.
- Authoritative main used for reconciliation: `f130e47e61000120d10e62adcf286d12b8140bdb`.
- Main was merged into the feature branch semantically. There were no textual conflicts.
- PR #62 remains authoritative for decomposition, assignment, persistence, reset behavior,
  planned-work state, and UI. This milestone consumes its active `DecompositionRecord`; it does
  not parse the objective into another graph or create competing child task records.
- PR #66 model evaluation and qualification code is unchanged.

## Coordinator architecture

`CoordinatorService` is attached to the existing autonomous worker loop after planning-result
recovery. An accepted planning run with an active ready decomposition is handed to a single
durable `CoordinationRecord` instead of completing the parent task. The parent task lease and
`RuntimeExecutionFence` remain authoritative for every runtime and coordinator write.

Each planned node has one deterministic runtime run per durable attempt. Runtime creation,
queueing, claim, attempt start, checkpoint, attempt completion, and run completion use the
existing authorized agent-runtime command ledger. The coordinator never grants permissions,
changes team membership, changes provider policy, or creates executable child `TaskRow` records.

The coordinator API exposes the read-only projection at
`GET /api/tasks/{task_id}/coordination`. Execution remains worker-owned.

## Dependency and result semantics

- Root nodes are ready immediately.
- A dependent node becomes claimable only after every named dependency is `succeeded` and has a
  durable result digest, evidence list, and runtime checkpoint.
- Independent work may continue while another independent node is waiting for retry eligibility.
- Permanent upstream failure marks transitive dependants `blocked`; it never unlocks them.
- Ready order and synthesis inputs use the deterministic topological order from PR #62.
- In-place graph changes, including operator protection changes, invalidate the stored graph hash
  and fail closed before another transition.

Specialist output is untrusted data. It must validate against `SpecialistResult`, explicitly cover
every PR #62 completion criterion, stay within checkpoint bounds, and be persisted with a SHA-256
digest and evidence before dependencies unlock.

## Retry and recovery

- Maximum specialist attempts: 3 (one initial attempt plus at most 2 retries).
- Maximum synthesis attempts: 2 (one initial attempt plus at most 1 retry).
- Provider, timeout, malformed-response, and deterministic output-validation failures are
  retryable within those bounds.
- Budget exhaustion, disabled/invalid provider configuration, authorization/control-plane
  refusal, cancellation, pause, emergency stop, lease loss, and stale inputs do not autonomously
  retry.
- Retry eligibility is durable and uses bounded exponential delay metadata; the worker never
  sleeps inside a coordinator transaction.
- Replaying an already recorded failure or success does not increment the attempt count or add an
  event.
- A restart reconstructs work from the coordination row plus runtime ledger. A validated runtime
  checkpoint repairs a lost coordinator acknowledgement without repeating inference. A running
  runtime with no checkpoint has an ambiguous provider outcome and blocks for operator review;
  it is never automatically dispatched again. A reservation with no started runtime can retry
  within the existing bounds. Same-lease concurrent callers cannot steal in-flight work. Losing
  the parent lease prevents the stale process from checkpointing or committing.

## Synthesis and completion

Synthesis starts only when every required node is `succeeded`. Its prompt contains only the
durable, validated summaries/digests/evidence in topological order. The returned contributor list
must exactly match those durable inputs. Synthesis is stored as data with an input digest, result
digest, and runtime checkpoint.

Parent completion uses `TaskLeaseRepository.complete_task` with a transaction-local completion
guard that revalidates every node, evidence/checkpoint, synthesis state, and final result reference.
The parent runtime is completed under the same fence. A crash after task completion is reconciled
from the durable task result. Repeated worker iterations after completion do not execute nodes,
synthesize, create artifacts, or emit another completion event.

## Migration

Revision `20260907_11` extends main's `20261002_si` self-improvement revision and stores one coordination JSON aggregate per active
decomposition/runtime. It adds no competing decomposition table. Populated downgrade refuses data
loss and instructs the operator to export coordination history. The Alembic graph has one head.

## Acceptance boundary

PR #64's twelve fixture scenarios remain explicitly labelled CI controls using
`FIXTURE_STAGE_PROVENANCE`. Production ports use #61 team selection, #62 decomposition,
the existing autonomous worker/coordinator for specialist dispatch, and distinct manager
synthesis. `AutonomyHarness.run_production` drives an already queued, authorized task and reads
the same durable task/runtime/coordinator records; it creates no competing runtime ledger.
The CLI `production` mode requires an existing migrated database, enabled authorized worker,
prepared context/graph, and a compatible installed local provider/model. It never provisions
identities, permissions, models, or tool approvals. Retry waiting returns pending evidence;
rerunning resumes the same durable task. Evidence verifies checkpoint provider/model identity.
Model-call counts report persisted dispatch intents, including ambiguous interrupted requests.

No installed Ollama endpoint was reachable during validation. Tests exercise production
services with explicitly labelled scripted transport responses, not real model inference.

The repository browser smoke continues to validate the real API, worker process, local planning,
decomposition compatibility, persisted runtime/Office behavior, lost-ack replay, and duplicate
prevention. Coordinator dependency, retry, synthesis, completion, fencing, and restart behavior is
covered deterministically in `tests/test_coordination.py`; the smoke fixture does not pretend to be
real model inference.

## Security

All execution paths recheck emergency stop, task lifecycle, task lease, runtime fence, actor RBAC,
selected team, identity lifecycle, capability assignment, context assembly, and decomposition
fingerprint at the write boundary. Provider routing is local-only with no fallback. Model output
cannot grant authority or alter lifecycle. Raw provider exceptions and unrestricted model content
are not persisted as control-plane truth.

PR #63 must not be merged by the implementation agent.

## Operator visibility and deferred work

The existing frontend AppStore owns a read-only coordination projection shared by Runtime
and Task details. It displays node states, attempts, retry eligibility, failure reasons,
provider/model identity, synthesis and final result. Backend events trigger refreshes.
Blocked graphs preserve successful nodes and require operator reconciliation rather than
automatic reselection or recursive replanning. In-flight runtime records may require operator
recovery after authority is revoked; the coordinator does not invent cleanup authority.

Deterministic validation checks schema, bounded results, exact node/criteria/evidence and
checkpoint integrity. It does not independently establish factual quality. Independent Result Verification/Critic work is outside PR #63. Persistent semantic memory, broad
browser/GitHub/email/cloud tools, qualification-driven production routing and full-floor Office
navigation remain deferred. Local autonomous execution remains disabled by default.

## Prior validation record (before October 5 reconciliation)

- Exact starting main: `f130e47e61000120d10e62adcf286d12b8140bdb`.
- Exact starting PR #63: `b48b90dc38047fc1ca8d188c552d740dd98d3bc2`.
- Clean main: Ruff; 1290 backend tests passed, 2 skipped; frontend typecheck,
  ESLint, 101 tests and production build; blank/supported migration roundtrip;
  autonomy/evaluation/qualification fixture controls; local planning/browser/Office
  and workforce smokes. Workspace smoke fixture mismatch was repaired separately
  in draft PR #67 and cherry-picked here; no runtime behavior was changed by it.
- Integrated branch: focused coordinator/migration tests (44 passed); frontend
  typecheck, ESLint, 104 tests and build; API/worker/browser/Office, workforce and
  authorized workspace-tool smokes passed. Extended decomposition browser smoke
  proves three assigned specialist calls, distinct synthesis, durable completion,
  dependency inputs, restart and UI reload using labelled fixture transport.
- Alembic: one head `20260907_11`, blank upgrade, downgrade to `20260729_04`,
  re-upgrade, populated main task preservation, populated coordination downgrade
  refusal, API revision alignment. Complete backend results, final head and
  exact-head CI/review gates are maintained in PR #63's description.
- The old `smoke-team-selection.cjs` assumes a running localhost:5173 app and
  root-installed Playwright; it is not a standalone isolated smoke. Selection
  is covered by backend tests and the extended decomposition process/browser smoke.
- No real installed-local model acceptance was performed; loopback Ollama was
  unavailable. No merge has been attempted.
- Final complete backend tree: Ruff format/lint pass; **1334 passed, 2 skipped**
  (`pytest -q --basetemp=../../validation-pr63/final-backend -p no:cacheprovider`).
  One upstream Starlette/httpx deprecation warning; no failing tests.


## October 5 main reconciliation and recovery invariant

PR #63 was reconciled with main `ff11aba814b8caf67ca5b8f2af415e827e6ec63b`
without changing PR #69 or the remote-operation/adaptive-replanning branches.
The self-improvement foundation, API router, dependency compatibility repairs,
and existing runtime contracts remain present. The single migration head is
`20260907_11`, now following unchanged main revision `20261002_si`.
Populated self-improvement history survives upgrade and supported rollback;
coordination and self-improvement independently guard populated downgrade.
Earlier disposable feature-branch databases must not be reused as main schemas.

The P1 review finding “Recover checkpoints before suppressing same-lease dispatch”
is addressed in both specialist and synthesis recovery. Durable checkpoint
validation/reconciliation precedes the lease fingerprint check. Only a dispatch
without a durable result can be suppressed as possibly still in flight. A new
service can recover using the original unexpired persisted lease immediately.
Malformed checkpoint envelopes block with `COORDINATION_CHECKPOINT_INVALID`.

Normal acknowledgement and checkpoint recovery share runtime completion.
Optimistic command conflicts are accepted only when durable state confirms the
same checkpoint already advanced; the coordinator success transaction still
revalidates checkpoint contents, live authority and the task fence. Tests cover
four crash boundaries per stage, genuinely concurrent inference, concurrent
checkpoint reconcilers, original acknowledgement racing recovery, cancellation,
emergency stop, permission revocation and corrupted checkpoint envelopes.

Current local validation and exact-head GitHub Actions/review results are
recorded in PR #63's description after the candidate is pushed. The prior test
counts above describe the older candidate only. No PR merge is authorized.

The fresh P2 review finding "Bound the full specialist checkpoint payload" is
addressed by measuring the complete serialized chunk payload, including criteria,
contributor IDs and JSON escaping, before accepting new specialist or synthesis
results. Admission reserves space for bounded model identity and runtime event
metadata. Oversized output follows the existing bounded validation retry path.
The wire fields and schema versions remain compatible. Previously persisted
checkpoints retain their original schema validation and actual runtime bounds;
recovery does not apply the new conservative admission budget retroactively.
Regression tests cover oversized criteria/escaping, maximum identity overhead,
large valid outputs through durable final completion, and recovery of prior
checkpoints without another inference.
