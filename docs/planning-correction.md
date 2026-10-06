# Opt-in bounded planning correction

Queued planning-review specifications can freeze a `correction_policy` alongside
explicit `verification_criteria`. Without policy, existing review and serialization
behavior is unchanged. The run must freeze a deadline no later than the policy's
maximum elapsed duration after creation.

```json
{
  "policy_version": "planning-correction-1",
  "maximum_corrections": 1,
  "maximum_model_dispatches": 8,
  "maximum_elapsed_seconds": 600
}
```

One persisted failed or needs-correction verdict can select a same-specialist
planning retry. Its durable native review checkpoint binds the original result's
verifier digest and immutable policy digest, preserving criteria and previous
result/verdict records. Feedback contains criterion IDs and outcomes, never model
prose granting authority. Structural review revisions also bind the passing
independent verdict that preceded them. Human-review-required and unverifiable
outcomes remain operator exceptions.

Two planning cycles each permit at most two worker and two critic dispatches.
Existing per-attempt reservations and persisted reviewer dispatches prevent repeat
inference after uncertain acknowledgement. Coordinator children retain their own
native budget; eight bounds this planning correction segment. The frozen deadline
clamps worker and critic timeouts and blocks further access after expiry.

Native task retry/runtime transitions own correction recovery. A task transaction
rechecks current runtime/attempt/target, lease, stop and failure permission before
admitting a retry. Late revocation cannot leave a retrying task attached to an
unauthorized running attempt. Cancellation and emergency stop prevent replacement
dispatch. A second rejected result pauses under native review rather than starting
a third cycle. Local autonomous execution remains disabled by default.

Tests exercise the production worker, SQLite, RBAC, task leases and checkpoints
using deterministic inference fixtures. They do not prove arbitrary model quality.
Opt-in coordinator node verification is being integrated with native runtime
checkpoints and the existing aggregate dispatch budget. Specialist reassignment,
versioned replanning and reuse of verified results across graph versions remain
unimplemented. This
dependent branch is preparation for the adaptive milestone, not its completion.
