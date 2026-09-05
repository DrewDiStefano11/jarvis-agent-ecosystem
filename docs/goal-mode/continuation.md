# AI Hub continuation

## Starting point

The continuation branch `astra/ultra-ai-hub-completion` was created and its initial
HEAD verified as `6e73df61837e5a7016863d5499875877c49a791b` on 2026-09-05.
The existing `astra/goal-complete-ai-hub` worktree was not changed. The existing
PR lineage is main → #54 → #55 → #56; #53 supplies the separate Windows supervisor.

The current user directive authorizes commits, pushes, PR updates and merges
after local validation, exact-head CI and applicable review findings are clear.
Earlier handoff instructions prohibiting merges are historical and superseded.
No quality gate, permission boundary or recovery safeguard is waived.

## Verified baseline in Windows

- Backend Ruff formatting/lint and 753 tests passed with Python 3.12.10.
- Frontend typecheck, ESLint, all 47 tests and production build passed.
- The original office asset checksum passed; no generated artwork was committed.
- Local Playwright Chromium was installed. The real API/separate worker browser
  harness passed task creation, explicit provisioning, queueing, persisted result,
  office camera/candidate controls and mobile layout, without browser errors.
  That harness uses explicitly labeled deterministic fixture inference.

## Current correction

PR #56's last review found that the operator could queue as a different identity
from the configured worker, leaving the worker without access to the task.
The status contract now exposes the configured `workerActorId`, including when
storage health is degraded. The system-status payload is declared in OpenAPI.
Planning requires that active configured identity for submission and replay;
other identities remain selectable for authorized history. Selecting the worker
does not grant permissions or change the target agent. Explicit task preparation
continues to preserve denials and grant only the existing scoped permissions.

The correction passed backend Ruff and all 755 backend tests, frontend typecheck,
ESLint, all 50 frontend tests and production build. The isolated database upgraded
from blank to `20260905_06`, downgraded to `20260729_04`, and upgraded back to head.
The browser smoke described above ran with this correction. Remote CI and review
must validate its published exact commit before #56 merges.

## Integration evidence

PR #54 was merged to main as `96e32d2b204b04069b11fd91d0b3eef8e699221e`.
Its exact head `c4f70b432bd5b51e65c7b963837368e008722175` had green backend,
frontend and repository-integrity CI, a clean Codex review of that commit and no
review threads. The continuation's full baseline suite covered its changes.
The resulting main tree matches that reviewed head, and the continuation merged
the updated main without changing source content. PR #55 now targets main;
its recovery corrections will arrive from #56 before #55 is integrated.

## Remaining acceptance work

- Finish review and integration of #54/#55/#56 without landing #55's known
  recovery defects before their fixes from #56.
- Finish the existing #53 supervisor's actionable findings and validate its
  documented Windows launch, stop, restart, diagnostics and backup paths.
- Complete real installed-model acceptance, distinct from fixture transport QA.
- Establish measured office registration, durable worker assignments and useful
  live spatial interaction using the original navigation implementation.
- Verify pending-submission recovery and operator correction/retry behavior.
- Merge completed milestones into main, validate the final target, launch the
  integrated product, and record the final SHA, CI/review and clean-tree evidence.

This is an execution checkpoint, not a completion claim.

## Follow-up runtime safeguards

The next review found that a pending form could bypass the current task's
eligibility. Submission and retry now require the shared, current task to remain
queued or retrying; its original command IDs stay intact while the form is held.
Terminal or review-paused tasks direct the operator to history and form clearing.
The durable command transaction also rejects new autonomous create/queue commands
for unclaimable tasks while preserving replay of already accepted commands.

Independent restart tests also reproduced an older lifecycle defect: API startup
and shutdown flushed cached task and system state over separate-worker updates.
Lifecycle bookkeeping now updates only its own system columns. Task results,
project association, emergency-stop state and the event cursor survive both
boundaries. These changes require fresh local validation, CI and review before
the candidate merges.

## Local database migration disclosure

An additional baseline-check script imported `app.main` before selecting its
temporary database. The module's default application initialization migrated the
existing ignored `apps/api/data/jarvis.db` from `20260724_03` to `20260905_06`.
This was an unintended QA side effect; it is separate from the isolated pytest
and browser databases. No downgrade, restore or deletion was attempted.

Read-only checks found SQLite integrity `ok`, no foreign-key violations, four
demo tasks, five demo agents, and empty identity/runtime/model-execution/workflow
tables. No workflow required recovery, and lifecycle timestamps remained from
July. The applied upgrades add tables/indexes and widen an audit/outbox field;
they contain no domain-data update, deletion or reseeding steps. No verified
pre-import backup was found. Subsequent direct-import checks select an isolated
database before importing the application.
