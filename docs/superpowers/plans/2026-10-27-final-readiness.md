# Final readiness acceptance implementation plan

> **For agentic workers:** Use superpowers:executing-plans for inline implementation with test-driven changes.

**Goal:** Establish truthful acceptance results and a safe permanent-host acceptance entry point while continuing the six-criterion campaign.
**Architecture:** Reuse production local-provider evaluation, durable autonomy harness, supervisor and diagnostics. Preserve default-off execution and operator authorization. Never install/start models or alter runtime data here.
**Tech Stack:** Python, pytest, existing Pydantic evidence and local provider router; Windows/React checks.
**Spec:** The three operator attachments supplied 2026-10-27; implementation scope and acceptance requirements recorded in docs/handoffs/final-readiness-20261027.md.

## Global constraints

No Ollama installation/start/inference on development host. Additional AI spending $0. No merge or branch deletion. Real host/isolation/model quality cannot pass from fixtures. Root staged changes remain untouched.

## Review focus

Empty evaluations, failed cases, provider unavailability, unsupported host capabilities and stale evidence must never yield production acceptance. Tests use isolated data and controlled providers.

### Task 1: Correct local-evaluation exit status

Files: scripts/autonomy-acceptance.py; apps/api/tests/test_model_evaluation.py; docs/autonomy-acceptance.md.
- [ ] Add a CLI regression using actual runner/report with scripted good and bad model outputs; require failure code 1 and durable failed case evidence for bad output, success code 0 for complete passing cases, unavailable code 2.
- [ ] Run regression RED; correct cmd_evaluate_local to require nonempty complete passing cases, then run evaluation tests GREEN.

### Task 2: Permanent host acceptance runner

Files: scripts/permanent-host-acceptance.py; apps/api/tests/test_host_acceptance.py; scripts/backend_ci_shards.json; docs/permanent-host-acceptance.md.
- [ ] Test default offline preparation performs no subprocess/model calls, retains six explicit acceptance criteria, and cannot claim readiness.
- [ ] Test known command execution is bounded, uses current Python and trusted script paths, preserves failure/unavailable/timeout statuses, and labels fixture controls separately.
- [ ] Add explicit local-model and already-authorized task options reusing current CLI, without setting trusted configuration, credentials or permissions.
- [ ] Verify evidence and instructions; run focused tests, static gates, full backend and frontend checks; publish reviewable feature PR with remaining work recorded.

### Continuing queue

Reconcile runtime recovery/process smoke, missing persistent improvement scheduling and remote dashboard admission after this acceptance milestone. Do not mark the entire campaign complete from this first PR.

### Task 3: Preserve Task Scheduler ownership of the supervisor
Confirmed root cause: scheduled start detaches and exits 0, so scheduler never sees a later daemon crash. RED XML regression plus foreground exit-propagation controls recorded. Change only task command to existing daemon and add three retries at one-minute intervals. No task registration on development host; real Scheduler/logon/crash retry behavior remains permanent-host acceptance. Clean stops retain exit 0. Existing manual start/restart behavior remains covered by current smoke and documented separately.

### Task 4: Bind evaluation to actual response identity
Four RED cases showed a foreign model/provider/task/correlation could receive a perfect evaluation score. Validate normalized response identity before scoring, discard mismatched content and use the compatible provider_error category without repair. Re-run evaluator, qualification and self-improvement suites because evaluator source digests intentionally invalidate historical comparisons.

### Task 5: Keep zero-spending capacity refusal out of defect scores
Existing Campaign D failure code was absent from the improvement control taxonomy. Add lowercase/uppercase RED cases to the full baseline/analyze regression; classify free_capacity_unavailable as control_condition with no expected success threshold, preserving the original observed failure code and genuine provider/budget/unknown-fault attribution. No quotas, retry budgets or permission changes.
