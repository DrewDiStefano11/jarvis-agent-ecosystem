# Bounded direct planning and stage-budget foundation

Dependency: PR #163 at 11b7dd2, not assumed merged. Branch codex/jarvis-stage-budgets
in the managed jarvis-stage-budgets checkout. Existing intake verification remains
in the previous unchanged checkout.

The first supported execution graph has exactly one planning request, zero team
selection, decomposition, independent review, synthesis, model validation, retry,
repair or correction calls, and one native attempt. This is a complete bounded
path; it is not general multi-agent admission. Unsupported graphs must be rejected
before context assembly can dispatch any model. Preserve legacy context requests
that do not opt into an envelope.

Add strict frozen ExecutionBudget and StageBudget contracts. Every stage carries
request count and conservative input/output caps; aggregate requests and
sum(requests*(input+output)) must fit explicit maxima. Unknown/duplicate stages,
booleans in numeric limits and inconsistent zero caps are invalid. Budget metadata
is part of the context assembly's immutable input hash and persisted payload.
Budget values are limits, never authority to activate a provider or tools.

With an envelope, context intake must not call the legacy team/decomposition
models, including replay paths. Assemble minimal native task/operator context with
the unchanged source-trust and injection rules; omit unrelated workforce/recovery
snapshots for this direct path. The runtime specification must remain within the
persisted envelope (one attempt/request, no repair or independent model review).
The worker rechecks it and a conservative UTF-8/schema/framing/completion bound
before physical dispatch. Existing Groq/native capacity reservation and current
account/model/RPM/RPD/TPM/TPD checks remain the sole quota authority. Since the
supported graph has one physical request, its native reservation covers the entire
admitted graph; no parallel quota ledger is introduced. Unsupported multi-request
graphs stay blocked until aggregate native reservation integration exists.

Frontend Planning defaults to an explicit direct one-request envelope instead of
silently dispatching selection/decomposition and two-call repair. Preserve explicit
workspace-plan flow compatibility; tool execution remains separately authorized.
Persist limits with the existing submission identity. Show the request/token cap
before queueing. No activation or live inference is authorized by this change.

Acceptance: oversized or unsupported graphs produce zero provider calls; direct
assembly/replay makes no organizational model calls; durable envelope survives
restart; a wider runtime request/attempt or independent-review policy is rejected;
UTF-8/schema/framing/output bound fails before transport when over cap; one actual
API/fixture-worker journey makes exactly one physical fixture call and survives
restart. Unknown/stale native provider capacity still blocks. Update tests, docs
and ledger; no readiness claim until integration/browser checks pass.

## Implementation plan

- [ ] Add failing contract/HTTP/replay/runtime-envelope tests.
- [ ] Implement contracts and immutable context persistence with legacy serialization.
- [ ] Gate organizational planning and enforce one-request graph before any dispatch.
- [ ] Enforce persisted runtime/request/token caps in the native worker.
- [ ] Update browser submission defaults/limit disclosure and focused tests.
- [ ] Actual isolated API + fixture worker + browser acceptance; no live model calls.
- [ ] Relevant backend suites/migrations/Ruff, frontend checks/build, independent review.
- [ ] Publish a separately documented stacked PR, never merge.
