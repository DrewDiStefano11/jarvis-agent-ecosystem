# Home → bounded native planning vertical slice

Execute inline using the existing brainstorming/planning/TDD workflow. The owner
has supplied the complete campaign and requests continuous authorized execution.
This plan does not activate any production service, inference or account.

## Dependency and current state

Feature branch `codex/jarvis-goal-planning`, based explicitly on unmerged PR165
source e041eab10b4da893612d3029aaa277c38c4f5f2b. Checkout path retains the name
`.local/worktrees/jarvis-retention` because it was created before prioritizing this
gap; do not confuse the folder name with the branch. Original review checkout is
unchanged for its ongoing full system tests. Retention research is saved separately.

At the start of this slice, Home created only a draft linked to one native task. Planning requires
the technical Planning form/task setup. The first ordinary workflow needs a
separately consented, default-disabled path from Home to one bounded native
planning request, then actual saved output and the existing reviewer workflow.

## Proposed bounded authority

- An explicit local operator setup/configuration records a versioned, hashed,
  expiring planning consent with a small maximum number of new goals and the
  immutable direct-v1 one-request/one-attempt/7500-token envelope. This is an
  authorization scope, not a replacement quota ledger. Native provider activation,
  current eligibility/capacity reservations, worker settings, RBAC, emergency stop
  and execution fences remain authoritative.
- Default disabled. Viewing pages, refreshing, reading policy, replaying a draft,
  navigation and idle perform zero inference and never enable a provider/worker.
  Setup must not change production configuration or activate cloud models.
- Home explicitly captures the exact live consent ID/hash in a new submission.
  Legacy submissions without it stay drafts; old pending session submissions keep
  their exact body. No automatic backfill of existing drafts.
- Server picks the explicitly configured native planning worker identity; no
  arbitrary actor/provider supplied by model text or Home input. Provision only
  native task-scoped planning rights, preserving denials/inactive/expiry, never
  tools/workspace/runtime.admin. Local exact Origin/peer/no-forward boundary.
- Persist the exact captured consent binding with the created goal/task, in the
  native task-created transaction. One authority slot per goal; identical retries
  do not consume another. Concurrent last-slot and stale/expired/revoked consent
  must fail closed and create no execution authority beyond the captured scope.
- Recheck consent under the actual native runtime/dispatch write fences. The task
  binding must be authoritative independently of caller-supplied run metadata;
  forged/missing metadata cannot bypass a revoked/expired automatic scope.
  Per-task grants must not outlive the captured consent. Decide and test revocation
  behavior before implementing the setup operation.

## Native orchestration and recovery

Reuse ContextAssembler and direct_providers/validate_direct_assembly, the existing
context-created event transaction, runtime Create/Queue commands and worker lease/
checkpoint/outbox machinery. Extract a shared direct-context preparation/persistence
service from main.py rather than calling HTTP routes or copying organization paths.
Direct paths must continue to perform zero team/decomposition calls.

Use deterministic command IDs and timestamps bound to the saved goal/intent and
the exact original input/profile. Native durable runs/checkpoints define recovery
position. Any small admission record stores immutable authority/scope, not a second
workflow state machine or background scheduler. Handle saved draft → context →
created run → queued run crash windows with exact replay; recheck current authority
before crossing each execution boundary. Never start new workers/schedulers in
development. Use the existing isolated separate fixture worker for acceptance.

Only direct bounded planning is supported initially. Unsupported graph/tool/profile
requests fail before provider dispatch; retain general budget graph requirements
for later work. Missing provider/worker/capacity is visible and does not fabricate
execution or completion.

## Shared UI and output

Extend shared goal state with policy and captured submission scope. Home labels
the explicit action accurately (save draft vs send for bounded planning), shows
actual native task/admission state and actual structured output through shared
results state. Do not retain a static Saved draft label after work progresses.
Reviewer setup remains a separate explicit local authority; no automatic results
permission bypass through a new goal detail response. Link the existing Inbox/
Library and preserve full assumptions/recommendations and original result evidence.
No principal0% label for a generated result awaiting review.

Clarification/follow-up messages and multi-task conversation links need their own
bounded immutable request/revision treatment; do not overwrite the original goal
or reuse an exhausted run. Inspect existing correction/task links before extending
the one-goal/one-task schema. Do not silently claim this is implemented by the
initial direct planning slice.

## Implementation/validation sequence

- [x] Resolve native consent/grant/dispatch revocation boundary from current code;
  write failing isolated default-disabled, explicit consent/hash, expiry/deny,
  last-slot concurrency and forged-authority tests before implementation.
- [x] Strict contracts and additive migration, local setup/read/revoke with no
  model/configuration effects; task-created immutable capture and native fences.
- [x] Shared direct-context service, deterministic native admission/replay/restart,
  full actual wire budget before first call; no second execution/quota engine.
- [x] Shared Home policy/submission/output state and honest action/status labels;
  exact lost-ack/reload recovery preserves old draft contracts.
- [x] Actual API/separate fixture-worker/browser: ordinary Home user sets explicit
  bounded consent, submits paragraph, sees one task/run/call/full output, accepts
  through Inbox and downloads Library artifact without selecting worker IDs or
  manually calling APIs. Test default-disabled/idle/blocked/revoked/oversized paths,
  all six responsive widths, restart and uncertain outcomes.
- [ ] Required Ruff/pytest/frontend gates, migration and independent code review;
  record exact source, retained vs new evidence and stacked draft PR. Never merge.

Useful source anchors already inspected: main.py create_goal around970,
create_context_assembly around1134–1360; EventBroker.emit(created_context,
authorize); SqlAlchemyRepository._persist_created_context and _insert_created_task;
autonomous_worker/provisioning.py configure_task_actor (currently all native
planning permissions, task scoped); setup_router.py already picks the configured
worker for explicit task setup; agent_runtime/sqlalchemy_repository.py commit
authorizer/guards and ModelExecutionRepository._require_task_fence.

## Verified implementation checkpoint

Implemented the bounded slice above, including independent operator review even
when the immutable model result has requiresHumanReview=false. Review caught and
fixed configured-worker drift and the pending consent retry UI. An expanded native
write fence initially regressed the controlled legacy parent-deletion race; the
fence now retains the native autonomous/authorizer boundary, and all eight parent
authorization tests pass. Tests also reject a tampered persisted context budget.
Full-system validation and exact published-head CI remain pending; the last item
is intentionally open until those results and the draft PR are recorded.

## Consent changes during worker execution

Revocation, renewal, expiry or configured-worker changes can reject any native
fenced write, including the first response receipt after a provider call. The
worker polling boundary treats only `GOAL_PLANNING_CONSENT_UNAVAILABLE` as an
expected per-task refusal. It does not persist the refused result, bypass consent,
retry inference or cancel other execution slots. Scope corruption/mismatch and
unlisted failures still propagate. Recovery continues to use the existing native
leases and checkpoints; old captured work never inherits renewed consent.

Regression coverage changes persisted consent during a mocked provider request
for both revocation and renewal, verifies that no result is saved, then processes
a new goal under fresh consent. Separate polling tests preserve an unrelated
execution slot and require integrity errors to remain visible.
