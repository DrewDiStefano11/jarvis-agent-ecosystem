# Durable planning/review orchestration

## Purpose

Phase 2C already executed one local planning request per queued runtime run. This
layer adds the missing orchestration step: the durably persisted plan is
**reviewed** by a deterministic local policy, and the workflow advances to a
machine-readable outcome — accepted, one bounded revision, escalation, or a
terminal failure — using only durable repository state.

It is not autonomous improvement discovery, repository editing, shell execution,
Git/GitHub activity, or any new authority. The Phase 2C boundary documented in
`docs/autonomous-worker.md` is unchanged: disabled by default, explicitly queued,
local-only, bounded, auditable.

## Lifecycle

```text
queued run
  -> claimed / starting (fenced lease, prepared execution row)
  -> running (one local planning call, validated result)
  -> plan persisted            (model_executions.result_json, exactly once)
  -> review recorded           (durable runtime checkpoint, exactly once)
  -> accepted  -> task completed -> attempt/run succeeded -> execution completed
  -> revision  -> attempt failed (recovery_required) -> task retrying
                 -> next deterministic attempt (bounded)
  -> escalated -> runtime paused, task under_review (existing human-review lane)
  -> exhausted -> task failed -> attempt/run failed -> execution failed
```

Each poll of `run_once` performs at most one durable stage transition sequence,
so normal progression and crash recovery use the same code path.

## Deterministic review policy

`app.autonomous_worker.plan_review` is a pure function over the already persisted
`PlanningReviewResult`. It never calls a model, never reads generated prose as an
instruction, and never grants capability:

| Condition (structural)           | Outcome              | Reason code                      |
| -------------------------------- | -------------------- | -------------------------------- |
| `requiresHumanReview` is true     | `escalated`          | `model_result_review_required`   |
| no `recommendations`              | `revision_requested` | `plan_missing_recommendations`   |
| otherwise                         | `accepted`           | `plan_review_accepted`           |

`missingInformation` is recorded as the informational finding
`plan_open_information_gaps` and never changes the outcome by itself. Only the
structured outcome governs orchestration; `summary`/`analysis` remain stored
explanation. Generated text is never authorization.

## Durable authority

No new table, state enum, or workflow framework was introduced. The review record
is an existing durable runtime checkpoint:

- checkpoint ID `checkpoint-<execution-id-suffix>-review[-r<cycle>]`,
- `attempt_id` is the exact runtime attempt that produced the plan,
- `state_reference` is `model-execution:<execution-id>:review[-r<cycle>]`,
- `integrity_digest` covers the execution ID, request hash, result hash, outcome,
  reason code, findings, and policy version,
- `metadata` carries the machine-readable decision.

Recording is idempotent by checkpoint ID and content, so a replayed review is a
no-op with no new event, sequence number, audit row, or outbox message. A record
whose metadata or digest does not verify raises `PLAN_REVIEW_RECORD_CORRUPT` and
fails closed instead of being re-derived.

## Attempt identity and bounded retries

- Attempt IDs are deterministic per revision cycle: `attempt-aw-<run digest>` for
  the first cycle and `attempt-aw-<run digest>-r<n>` afterwards. Cycle zero keeps
  its established identifiers, so in-flight executions replay unchanged.
- Exactly one `model_executions` row exists per runtime attempt, so the durable
  row count is the authoritative cycle index. Nothing reads "the latest result"
  to decide authority.
- A revision cycle reads the review record of the **exact** previous attempt and
  echoes only its machine finding codes into the next deterministic request; the
  request hash therefore differs per cycle and is stored per row.
- Retries are bounded twice over by existing primitives: the task retry budget
  (`fail_task` with `tasks.maximum_retries`) and the runtime attempt budget
  (`maximum_permitted_attempts`, enforced by the ledger and the recovery plan).
  Exhaustion of either terminates the workflow deterministically: the task fails,
  the attempt and run fail, and the execution records `review_revision_exhausted`.
- A revision uses the repository's sanctioned recovery lane: fail the attempt
  (`recovery_required` block) → request a recovery plan → unblock → begin the next
  attempt resuming from the checkpoint the plan selects (the prior review record).
  The worker never selects its own resume position.

## Fencing, stop, and eligibility

Every durable orchestration advance is preceded by the existing live-policy
recheck and a fenced repository check that locks the target identity row, and is
committed through a transaction that revalidates emergency stop and exact lease
ownership:

- the review checkpoint commits with `require_execution_enabled` and the runtime
  execution fence (worker ID plus lease token),
- `fail_task` revalidates the exact lease and emergency stop in its own
  transaction,
- the next planning cycle re-validates target lifecycle inside `prepare`.

A stale or replaced worker, an active emergency stop, a cancelled task, or a
suspended/disabled target cannot advance the workflow; the plan stays durable and
recoverable instead.

## Recovery

Recovery is driven from durable repository truth, not memory:

| Interruption point                   | Resumption                                              |
| ------------------------------------ | ------------------------------------------------------- |
| before a plan exists                  | existing prepared/uncommitted recovery                   |
| after plan persistence, before review | review once, then advance (no second provider call)      |
| after review, before the transition   | reuse the durable review record and finish the advance   |
| during a revision transition          | skip already-committed steps, complete the rest          |
| before a terminal transition          | existing finalization recovery                           |

Recovery scans stay bounded and keyset-paginated. The revision scan selects only
blocked autonomous runs that carry a revision marker and have no still-active
execution row, so one corrupt or ineligible candidate cannot starve later valid
work, and no unbounded history is read.

## Operator notes

- Revision cycles require the worker actor to hold the task-scoped
  `runtime.recover` and `runtime.pause` permissions in addition to the previously
  documented runtime permissions; without them the run stays blocked and
  untouched rather than advancing.
- A superseded revision attempt is recorded as a failed execution with
  `review_revision_requested`, so the health `failedExecutionCount` includes
  revised attempts.
- No schema change, no migration, and no HTTP/event contract change accompany this
  layer. `ModelExecutionResult.failureCode` gains the bounded values
  `review_revision_requested` and `review_revision_exhausted`.
