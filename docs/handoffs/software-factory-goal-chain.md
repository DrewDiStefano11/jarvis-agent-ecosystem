# Software Factory continuous-development checkpoint

The human explicitly requires continuous productive development. CI/review waits,
clean commits and handoffs are checkpoints, not stop conditions. Preserve remote
work, keep independent branches isolated, and do not touch PR #63.

## Current priority: independent verification (#69)

- PR: https://github.com/DrewDiStefano11/jarvis-agent-ecosystem/pull/69
- Branch/worktree: `codex/independent-result-verification`, `.worktrees/independent-result-verification`.
- Original base `6626c5c4e267b737d86f8883201450e946aa1831`; merged/reconciled main
  `ee0dd09bf1825ab635e7a61d8fe2ae192f6e0444` includes #68 and the merged coordinator.
- No new migration; single inherited head `20260907_11`. The other session's branch
  remains untouched; this work integrates only the now-merged main.
- Latest pushed safety head before reconciliation: `4247ca012fe7ecf055c0c02fa039d8e1b6799fdc`.
  Preserve verifier and coordinator together, including async finalization recovery
  and task-filtered coordinator dispatch. Final merge SHA/fresh CI/review request
  belong in PR comments.

Frozen criteria, deterministic checks and a separate bounded local critic gate
completion using native result/checkpoint/RBAC/lease/audit/outbox systems. Invalid
artifact criteria are rejected for queued planning; test evidence stays
unverifiable without a command journal. Nonpassing evidence pauses for review.
Reviewer dispatch ownership and persisted responses/verdicts prevent repeated
inference after acknowledgement uncertainty. Recovery handles result/verdict/
review crash windows and chooses permissions only after terminal outcome is known.

Fresh review at 91da926 found a late permission revocation between preauthorization
and task completion. The repair uses the existing lease-fenced `completion_guard`,
passing its database session through native runtime/identity policy evaluation.
Denial or suspension committed immediately before completion prevents the write;
task remains in progress, runtime running, result finalization pending. Native
cancellation/recovery test wrappers forward this session without changing their
existing assertions.

The follow-up reproduction showed cancellation between service precheck and task
transaction could still mark a task completed. The completion guard now reads the
current runtime snapshot in the task session, checks active attempt/task/target
lineage and active target, and applies current-snapshot RBAC there. Cancellation
rolls back task completion before native cancellation reconciliation. A late pause
blocks completion while preserving finalization-pending recovery; actor suspension
continues to report revoked authorization. New races use actual native commands.

## Validation for the pending transaction repair

- Latest review repairs: cancellation precedes completion-only RBAC both in the
  task transaction and finalization recovery; collection criteria reject blank
  entries. All 162 verifier/worker/authorization/review tests pass (182.15 seconds).
  The reconciled full backend checkpoint below remains valid for unchanged paths.
- Remote review repairs now cover resume rollback, no-store body errors and
  concurrent desired-state control. Preserve its separate worktree and PR #70.
- PR #71 exact-head review of `808bc03f15` has no findings. The failed backend
  job never acquired a hosted runner; only that job was rerun. Other PR-event
  frontend, integrity and runtime-browser gates passed. No code workaround.
- CI evidence's full backend passes: 1,460 tests and two existing skips.

- Final reconciled full backend: 1,508 passed/two existing skips in 856.53 seconds.
  Frontend typecheck/ESLint/104 Vitest tests/build and API/script Ruff pass.
  Earlier checkpoints below are history superseded by this full run.
- Current runtime-state completion fence: full affected verifier/worker/native
  authorization/planning-review package passes 145 tests in 128.24 seconds.
  The added regression first failed with a completed task despite cancellation;
  it now proves transaction rollback and exact native cancellation/pause outcomes.
  Frontend typecheck/ESLint/101 Vitest tests/build and Ruff pass after the repair.
- Full backend run: 1,425 passed, two existing skips, five test-wrapper TypeErrors.
  All five are in the affected worker suite; wrappers were updated and the full
  affected verifier/worker/review/native authorization package passes 142 tests.
  This accounts for 1,430 passing backend cases and two skips across the full and
  repaired-package runs. Do not describe it as an uninterrupted green full run.
- Ruff format/lint pass across API and scripts; frontend typecheck/ESLint,
  101 Vitest tests and build pass. Repository generated-artifact/diff checks pass.
- Earlier merged-main real API/worker/frontend office and runtime browser paths
  passed. Actual Ollama HTTP transport used deterministic inference fixtures;
  arbitrary model judgment is unproven. No model downloads or provisioning.
- Full tests use fresh validated short `$env:TEMP` roots to avoid Windows shared
  temp permissions/deep-path limits. No tests skipped or weakened for environment.
- Exact final-head CI, explicit fresh review, zero actionable threads, reconciled
  main and clean integrity are required before the already-authorized #69 merge.

## Independent work and next selection

Remote operation is preserved on `codex/remote-operation-control` in its own
worktree, reconciled to main ff11aba. Dedicated disabled-by-default direct-TLS
API and certificate-verifying CLI reuse native identities, scoped controls,
goals/audit/outbox/leases and system stop. Real TLS proves submit/inspect/cancel,
pause/replay/resume/runtime cancellation, denial of worker confirmation and
system stop/resume. Correction submission now requires source read authority at
request and commit. PR #70's pre-reconciliation head is `50fd6deb1a894a8b04ef0aa77b72f99f37b5ebcf`;
the review's default HTTPS port finding is fixed, its thread resolved and fresh
review requested. Full backend checkpoint: 1,414 passed/two existing skips;
latest HTTP/config/actual TLS package: 32 passed. Required frontend gates pass
with 101 tests. Reconciled-main full backend: 1,494 passed/two existing skips;
frontend 104 tests and required checks pass. Publish reconciliation and request
fresh exact-head gates. No public deployment. See its own handoff.

Adaptive preparation remains isolated at old #69 head 7d3777f with its preserved
handoff. Executable coordinator nodes are needed to prove reassignment and
verified-result graph reuse; never import unmerged #63 contracts.

Next independent Self-Build prerequisite: durable prioritized improvement backlog,
using merged #68's immutable evidence/proposals and native tasks/outbox. Fresh-main
worktree `codex/improvement-backlog` was created at ff11aba. The native selector,
admission provenance, loopback API and CLI are implemented; 20 targeted tests pass,
including concurrency, commit-time revocation/stop, uncertain acknowledgement,
protected tasks and invalid lineage. PR #71 is pushed at `808bc03`, reconciled to
main ee0dd09 with fresh gates requested. Full backend checkpoint: 1,385 passed/two
skips; final guard package 136 passed; merged-main integration 139 passed; frontend104
pass. Independent `codex/ci-evidence` at main ee0dd09 adds bounded native job
observations for diagnosis without claiming root cause or quality. Ninety focused
tests pass; actual exported GitHub JSON validates. Its final checks/publication
remain pending. Preserve frozen
criteria/provenance and existing execution authority; proposal selection must not
auto-approve tools or claim autonomous coding. #68 currently provides diagnosis/
planning/comparison only. Return to #69 when actionable, checkpoint remote after
its coherent gates, and keep selecting material independent work without polling.
