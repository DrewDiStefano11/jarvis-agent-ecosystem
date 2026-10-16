# Campaign A execution integration checkpoint

## Authoritative foundations

This branch integrates origin/main 5e11d60545006eda9a0f5a77affcb5860e2c3926:
source mutation history and recovery (#114/#118), expected artifacts (#120),
native inspection (#121), and applied-source provenance/Git preparation (#119).
Campaign A's proposal/context/provider/dispatch prerequisites remain reviewable
in PRs #128, #130 and #133. Their inclusion does not claim those PRs are merged.

Development execution composes the native task/runtime/lease/checkpoint/outbox,
workspace ownership, exact source-plan and independent source-mutation approval
services. No model command receives shell, filesystem, validation or publication
authority. Source editing retains native original-byte verification and recovery.

## Campaign D handoff

The operator explicitly requested coordination with Campaign D. D reported draft
PR #131 at 760ddadb63441f4288d5a8b8945c73f04b09ebb3, based on the same main.
It is an unmerged future dependency, not copied into this branch.

Its proposed ModelProvider interface is CodexProvider(grant, capacity, transport),
with asynchronous ModelExecutionRequest to ModelExecutionResponse execution.
The native stdio transport uses the existing subscription-authenticated app-server,
read-only temporary scope, disabled tools and no API key. Deterministic transport
injection is available. Both provider and transport avoid physical retries;
capacity is reserved before dispatch and uncertain/failed calls are not refunded.
A must retain its own no-fallback, one-attempt dispatch and native authority gates.

D reported these unresolved A acceptance gaps: durable native thread/turn/request
identity is absent; native output-schema mapping is absent; token usage may be
missing; actual rerouted model identity is not propagated. A currently requires
complete normalized usage, exact task/correlation/context/model and strictly
validated proposal schema. Integration must refuse incomplete or changed identity
rather than convert missing observations into successful accounting.

D's separate default-off activation requires protected fresh account-specific
included-only allowance evidence and owner authorization. Native account and rate
limit reads must match independently verified identity/plan/shared bucket, reject
unknown or positive credits, and require known nonexhausted bounded windows.
Observations, configuration flags and mocks do not establish billing proof.
No account currently meets independent verification. Live inference remains off
until included-only use, purchased-credit exclusion and recharge safeguards are
independently established. The newly created API key is unused by this campaign.

## Evidence and outstanding implementation

Deterministic provider tests drive real native multi-file planning/mutation,
restart recovery, fresh native artifact handoff and bounded source-conflict repair.
Provider fixtures do not prove real model coding quality. Native measurement is
not independent code execution. Campaign B must retain its real isolated runner;
validation_passed and publication_ready remain false in A's handoff.

Authenticated admission preview/read, independent remote-operator approval, and
protected source-plan review are now exposed through typed HTTP contracts. The
iteration endpoint remains quarantined before reservation. Remaining implementation
includes complete operator-facing task orchestration and independently approved
correction baseline/workspace lineage
for repeated effects, and bounded repair informed by real validation diagnoses.
The existing one-mutation-per-workspace rule is preserved. A fresh source/validation
plan cannot reset the task's immutable root reservation/output/deadline budget.
Campaign A implementation completion and live coding acceptance remain distinct;
this execution checkpoint is not a campaign-completion claim.

Remote CI is intentionally skipped for this publication under the operator's
unverified billing/storage boundary. Local tests and integrity checks are reported
in the PR. No merge is recommended without independently permitted current-head
CI and review. No remote workflow, live inference or API-key validation is invoked.
