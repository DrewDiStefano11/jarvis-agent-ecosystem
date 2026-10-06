# Adaptive correction preparation

This is implementation preparation, not a completed capability or merge-ready PR.
The updated October 5 goal permits a dependent branch while PR #69 gates run.

- Branch: `codex/adaptive-correction-replanning`.
- PR #69 merged after all exact-head gates and clean review; main is
  `9d5eab1c5e169f7c99f3cf1e9e2ecafa3a725fdf`, merged into this branch.
- Planning correction checkpoint `04d06ae6b69b39505745359da7ba52853da9f115` is pushed.
- PR #73 exists on the planning checkpoint. Preserve current partial
  node-verification implementation; full adaptive capability remains incomplete.
- #68/#63 have merged. Integrate only current main; other sessions stay untouched.

## Current implementation checkpoint

Opt-in immutable planning correction policy permits one same-specialist retry,
at most eight planning/critic dispatches across two cycles, and a frozen deadline
up to one hour. Legacy serialization and behavior remain unchanged without policy.
Durable review checkpoints bind exact verifier and policy digests. Unverifiable
evidence and human-review-required results escalate; exhausted correction pauses.
Provider/critic timeouts are clamped to the run deadline. Native retry transitions
and previous result/verdict checkpoints own recovery, rather than a new ledger.

Real temporary SQLite/worker tests prove successful correction, cancellation/stop,
review acknowledgement loss with result reuse, and eight-dispatch worst case.
A late permission-denial test reproduced a task incorrectly entering retrying;
the native task failure transaction now uses a same-session revision guard and
the reproduction passes. Final affected verifier/worker/review/lease integration
passes 171 tests (363.58 seconds); final policy package passes 12 (18.31 seconds).
Deadline denial, bounded queued contracts, structural/verifier provenance,
protected human review and missing test evidence are covered. API/scripts Ruff,
frontend typecheck/ESLint/104 Vitest tests/build and diff integrity pass.
One stalled local integration process was stopped after verifying its owned pytest
command; the diagnostic rerun with faulthandler passed all cases. No tests weakened.
Checkpoint this coherent slice on the existing branch, without opening a PR
claiming the complete adaptive milestone. Full validation follows its final scope.
This proves no reassignment or result-preserving replan; those remain required for
the full milestone.

## Active node-verification work

An opt-in immutable `coordinator_verification` policy now journals independent
critic dispatches, responses and verdicts in existing child runtime checkpoints.
Source result, exact planned inputs/criteria, upstream result provenance and policy
digests bind the verdict. Native node success and final completion require a
matching passing proof inside their transactions. Critic requests consume the
existing 38-dispatch coordinator budget, with at most two requests per attempt.
The frozen deadline fences new dispatches and clamps physical request timeouts;
already committed results can reconcile without another call.

Eighteen initial tests pass across targeted runs: actual three-node coordination
and task completion, failed/needs-correction/unverifiable outcomes, schema repair,
invented evidence refusal, lease takeover without repeat inference, native success
enforcement against service bypass, deadline/clamping, and resealed foreign task,
criterion or false passing verdict rejection, concurrent critic ownership, unknown
response recovery and late stop/revocation/suspension. Initial recovery testing caught and
fixed datetime serialization changing the sealed verdict digest. Full integration
and concurrency/control-plane adversarial checks remain required before committing
this slice. Coordinator/planning integration passes 97 tests; full backend is
running on the combined node-verification candidate. No reassignment or versioned
replan/reuse has been added yet.

PR #73 frontend failures were cold lazy Office imports exceeding DOM query bounds:
the job's failed DOM contained only `Loading office…`. A temporary 2.5-second
real-module import delay reproduced both exact failures. Awaiting the real module
in test setup made both unchanged interaction assertions pass under the same delay.
The diagnostic delay was removed. Typecheck, ESLint, all 104 Vitest tests and build
pass; no production code, retry allowance, query timeout or assertions changed.

## Evidence from current architecture

`AutonomousWorkerService._resolve_review` escalates every nonpassing independent
verdict. Existing `_advance_revision` already fences task failure, attempt failure,
and retry recovery; `_revision_findings` reloads the previous durable decision.
Reuse these transitions rather than adding another scheduler or retry ledger.

Existing per-attempt worker limits and the critic's two-call allowance do not
provide aggregate run/graph bounds. Recovery must reserve aggregate dispatch
budget before calling a provider; uncertain acknowledgements consume a reservation
and must not dispatch again. A budget check performed only after a call is unsafe.

`DecompositionRepository.save` serializes with the control-plane write fence,
checks task/team input, preserves prior versions and rejects operator protection.
It forbids graph replacement after execution starts. Preserve that behavior until
an explicit transactional replan/version/result-mapping command exists.

Merged main now executes coordinator nodes, retries and synthesis. Independent
node-level verification, specialist reassignment and versioned replan reuse still
need integration; existing node checkpoints alone do not prove those capabilities.

## Required implementation and evidence

1. Freeze correction policy in queued specifications; omit absent policy from
   legacy serialization. Bound per-node corrections, reassignments, replans,
   verifier cycles, aggregate physical model dispatches and elapsed runtime.
2. Derive correction from persisted exact-result verifier checks. Application
   policy selects actions; model reasons remain feedback data, never authority.
   Never repair uncertain provenance, missing evidence or unavailable capability
   by guessing or weakening frozen criteria.
3. Record a fenced decision checkpoint before advancing: trigger, verifier digest,
   previous attempt, selected action, reason, remaining bounds, affected node IDs,
   graph version and reusable verified result IDs. Replay derives recovery position
   from committed decisions and dispatch reservations.
4. Reuse existing worker revision transitions for same-specialist output repair.
   Re-verify the new result; original result/verdict remain durable history.
   Only the new verified result can become authoritative.
5. Reassignment must resolve an existing active, capability-compatible selected
   identity and repeat RBAC/permission checks inside the commit transaction.
   Revocation cancels authority; it must never silently broaden the agent pool.
6. Replanning must keep prior graph versions and explicit lineage. Compare the
   exact graph/input version under the control-plane fence. Preserve unrelated
   verified outputs only when node inputs, criteria, capabilities and upstream
   evidence hashes remain compatible. Operator-protected graphs pause unchanged.
7. Exercise real worker/repository paths for repair success/exhaustion, restart,
   lost acknowledgement, concurrent decision fencing, cancellation/stop/revocation,
   no unnecessary rerun, reassignment and compatible-result graph reuse.
   Deterministic inference fixtures prove runtime execution, not model quality.

Do not open a PR claiming this full milestone until all three recovery categories
are integrated and tested. No placeholders for unavailable execution paths.

## Pipeline and roadmap

Keep #69 repairs in its own worktree. After a validated push, record its exact
head/checks/review and continue this preparation rather than polling CI. Return
on a meaningful preparation boundary. Remote control is an explicit upcoming
substantial milestone: transport authentication, operator identity mapping, RBAC,
audit, remote goal submission/read/control, emergency stop and secure deployment
boundaries must be designed together; a remote actor-ID header is insufficient.
