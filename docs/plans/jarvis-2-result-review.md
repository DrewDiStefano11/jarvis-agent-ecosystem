# Native saved-result review and Inbox implementation plan

Dependency: draft PR164 at60e3f04, explicitly unmerged. Active branch
codex/jarvis-result-review. Extend the existing runtime/task/artifact authority;
no inference, live data, merges, deployment or production permission changes.

## Design decisions

An explicit local operator setup provisions a separate reviewer identity with
results.read/results.review rights, preserving native denials and inactive state.
It grants no runtime dispatch, model configuration, tool or workspace permission.
Require the existing exact local Origin/peer/no-forward boundary, strict contracts,
no-store responses and identity-backed RBAC. The UI need not select worker IDs.

A review command binds execution ID, original result hash, latest checkpoint ID,
expected native run version, task updatedAt, actor, decision and stable command ID.
The server validates the persisted structured result with the existing execution
contract, its context/task fingerprint, paused model_result_review_required run,
active target identity, matching result-persisted checkpoint/digest and released
lease. Other active runs, leases or unfinished descendants block review rather
than granting cancellation authority. Failures without valid outputs and workspace
plans do not receive this planning-result acceptance authority.

Accept emits existing RUN_RESUMED/ATTEMPT_SUCCEEDED/RUN_SUCCEEDED events in one
native transaction. No worker-visible intermediate state exists. Reject/request
changes use the existing native cancellation sequence atomically. Request changes
creates one native correction task from a full revised request, preserving source
and result evidence; it never queues another model call or reuses an old budget.

Add an explicit domain_effects callback to durable native command commit, applied
after native replay/version validation and in the same transaction as runtime
rows/events, task projection, decision, native ArtifactRow/content and audit/outbox.
The in-memory repository rejects SQL domain effects. Do not hide writes in an
authorization callback. Recheck authorization/system/lease/result/checkpoint/task
scope in that fenced transaction. Preserve idempotent command identity and conflicts.

Preserve original result bytes/hash and requiresHumanReview. Record a separate
operator decision. Acceptance marks the execution completed and task completed
without changing model evidence, and creates a real JSON result artifact under
existing native artifact metadata plus a bounded content row. Downloads verify
content hash and enforce task read permission. No fake simulated output paths.
Append-only security audit contains decision references/hashes, not full user text.
Downgrade refuses nonempty review/artifact-content history.

Inbox shows actual five-recommendation content, assumptions, missing information,
provenance and Awaiting your review, with Accept / Request changes / Reject. It
keeps native action approvals separate. Shared state owns operator, pending review,
results, decisions and artifacts; views own form input only. Stable captured
commands survive uncertain acknowledgements/reload. Library displays accepted real
outputs with preview/download. Home/task links lead to the same authoritative data.
No fabricated completion percentages for pending results.

## Tasks

- [x] Write failing disposable native-worker fixture HTTP tests for all3 decisions,
  preserved evidence/artifact bytes, zero further inference, replay/conflicts,
  authority denial, changed hashes/versions/checkpoints, stop/active-work fences,
  rollback and restart. Reuse the real paused-run scenario with five recommendations.
- [x] Add strict review contracts, additive migration, native atomic event-batch
  finalization and explicit domain effects, decision/artifact repository and API.
- [x] Add operator setup/read/discovery with no execution side effects; prove deny,
  expiry/inactive actor, Origin and worker identity cannot bypass review authority.
- [x] Build shared results state, actionable Inbox, output renderer, Library JSON
  preview/download and correct awaiting-review progress projection; test before UI.
- [x] Actual isolated API/separate-worker/browser: save result, read full content,
  operator setup, accept/change/reject, task state/artifact, reload/restart and
  duplicate/lost-acknowledgement safety. Screenshots desktop/tablet/mobile.
- [ ] Required Ruff/pytest, frontend typecheck/ESLint/Vitest/build, migration and
  relevant CI gates; retain exact tested source and independent review. Publish a
  documented stacked draft PR; never merge automatically.
