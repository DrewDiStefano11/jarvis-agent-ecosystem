# Software Factory continuous-development checkpoint

The human explicitly requires continuous productive development. CI/review waits,
clean commits and handoffs are checkpoints, not stop conditions. Preserve remote
work, keep independent branches isolated, and do not touch PR #63.

## Current priority: independent verification (#69)

- PR: https://github.com/DrewDiStefano11/jarvis-agent-ecosystem/pull/69
- Branch/worktree: `codex/independent-result-verification`, `.worktrees/independent-result-verification`.
- Original base `6626c5c4e267b737d86f8883201450e946aa1831`; merged/reconciled main
  `ff11aba814b8caf67ca5b8f2af415e827e6ec63b` includes #68.
- No migration; single inherited head `20261002_si`. #63 remains separately owned.
- Latest pushed head before the pending repair: `91da926454a51e0081f08119f468a0ba8d613428`.
  Run `37362645872` has passing runtime-browser/repository checks; exact final
  SHA and fresh review request will be recorded in PR comments after repair push.

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

## Validation for the pending transaction repair

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
request and commit. Broad backend validation is running; required frontend gates
pass with 101 tests. No remote PR yet or public deployment. See its own handoff.

Adaptive preparation remains isolated at old #69 head 7d3777f with its preserved
handoff. Executable coordinator nodes are needed to prove reassignment and
verified-result graph reuse; never import unmerged #63 contracts.

Next independent Self-Build prerequisite: durable prioritized improvement backlog,
using merged #68's immutable evidence/proposals and native tasks/outbox. Fresh-main
worktree `codex/improvement-backlog` was created at ff11aba. Preserve frozen
criteria/provenance and existing execution authority; proposal selection must not
auto-approve tools or claim autonomous coding. #68 currently provides diagnosis/
planning/comparison only. Return to #69 when actionable, checkpoint remote after
its coherent gates, and keep selecting material independent work without polling.
