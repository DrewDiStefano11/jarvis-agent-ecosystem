# Tasks and Planning workflow redesign

Tasks now presents a readable work queue with actual snapshot summaries, reported-progress rings, explicit assignment provenance, saved evidence and direct actions. Creation, corrections, approval review, task inspection and Planning open in place. The shared task inspector separates saved task outcomes from native runtime evidence and keeps planning scope locked to its selected task.

## Data and authority

- Open requests and completed outcomes count loaded root task records. Needs attention counts loaded records with failure, review states, blockers or linked pending approval records. Pending decisions counts stored pending approval records, including records that may be expired or prohibited; the existing inbox determines whether an action is allowed.
- Progress comes from `Task.progress`. It is not measured model execution progress. Task counts and saved outcomes may include demonstration work.
- Assignment names resolve against the loaded registered identity projection, then the demonstration registry. Unknown identifiers remain visible as unconfirmed identities. Registration does not prove availability or actual execution.
- Results, retries, identifiers, parent/child relationships, corrected-source links, errors, artifacts and timestamps remain inspectable from actual records. Long result previews are bounded at 4,000 characters; the task inspector exposes the saved result.
- The table initially displays 50 matching records and expands to 200. Filtering resets that bound. No history, utilization, throughput, costs or external integration results are invented.
- AppStore remains the domain source of truth. Local state owns filters, editor identity, draft fields, drawer views and command acknowledgements. A creation draft survives closing its drawer in memory; input text is not newly persisted in browser storage.
- Creating a task saves a queued request. Preparation still calls the existing task-scoped setup endpoint only after an explicit operator action. Queueing retains configured-worker, prepared-task, target, status, readiness and emergency-stop checks, and now also blocks stale/unconfirmed views. An uncertain submission locks its inputs and retries the original immutable IDs.
- Workspace execution still requires existing native exact-plan-hash approval, bounded marked scope, runtime/RBAC/leases/checkpoints/outbox and emergency-stop handling. Model output does not grant authority. No API, schema, migration or backend authorization change is part of this milestone.

## Interaction and accessibility

Task creation query links and Back remain supported. Programmatic drawer closure no longer creates a second history entry. Native dialogs make the background inert, handle Escape and restore focus, including when the global task inspector unmounts. Explicit Tab wrapping covers the first/last control and skips hidden, disabled and nested-dialog controls. Strict Mode cleanup does not spuriously close a mounted dialog. Task inspector view changes restore a useful focus target.

At desktop sizes the work queue remains a semantic table. At 900px and below it becomes semantic stacked rows with readable assignment/evidence text and direct actions. The automatic narrow sidebar also hides its footer so text cannot overlap content. This fixes an observed stylesheet-order regression in the shell foundation.

## Visual verification

Approved concept: `C:/Users/DDistefano/.codex/generated_images/01a130d5-2ee4-7120-ae37-254387e7cc26/exec-935af735-96c1-48d0-8e18-c024f807fccb.png`, derived from the supplied Mission Control reference. Actual artifacts live in `C:/Users/DDistefano/.codex/visualizations/2026/10/12/01a130d5-2ee4-7120-ae37-254387e7cc26/tasks`. The concept is a design specification; no bitmap UI or fabricated concept values enter the app.

| Comparison | Evidence and implementation decision |
| --- | --- |
| Composition | Tasks heading, four summaries, tab/search/filter row and full-width work table follow the concept. The implementation keeps accurate snapshot scope text. |
| Typography and density | Readable request titles lead each row. Identifiers, retry counts and raw error codes move into record details. Compact secondary actions avoid stretched rows; Inspect no longer breaks mid-word. |
| Palette and iconography | Shared midnight surfaces, restrained violet actions, teal completion and amber attention use existing NavIcon/Status primitives. Unsupported profile/notification chrome is omitted. |
| Progress | Circle indicators use actual reported task progress. The concept's illustrative percentage values and weekly comparisons are replaced with loaded records and scope labels. |
| Evidence | Saved results expand as inert text; no invented artifact totals, deployment links or execution confirmations are shown. Runtime evidence remains a separate direct inspection action. |
| Planning | A real task context, three readiness stages, guarded form and concise availability band implement the concept's planning flow. The drawer is wider to preserve existing authorization/recovery controls; narrow views stack the stages. |
| Responsive behavior | Verified at 2560, 1920, 1586, 1440, 1024, 768, 390 and 320px. At the concept-native 1586x992 viewport, compared before/after rendered screenshots and the concept with view_image. |
| Keyboard and states | Real browser checks cover Tab wrapping, Escape/focus return, stale gates, empty collections, 205 long-label fixtures, bounded display, reduced motion, creation uncertainty and linked corrections. |

Above-the-fold copy preserves the concept's Tasks title, subtitle, primary action, summary labels, table/tab/control labels and visual hierarchy. Deliberate differences retain existing navigation/search/emergency-stop labels, accurate scope explanations and contractual planning fields. Fictional names, weekly changes, artifact counts, costs and unsupported plan types are absent. The supplied reference and the rendered application were inspected through IAB and deterministic Playwright captures; the visual system was faithfully verified with these intentional data/function differences.

## Validation

- Frontend typecheck, ESLint, 230 Vitest tests and production build pass.
- `scripts/smoke-task-workflows.cjs` passes against its own real isolated API/database with model execution disabled: direct filters, native focus/Escape, creation with a lost acknowledgement, close/reopen retention, identical-key retry, focused approvals/results, linked corrections, stale gates and eight viewport widths. It verifies there are no inference, planner-permission or tool-execution writes during inspection/creation.
- Existing real-API Overview and search acceptance scripts pass after the shared drawer changes. Search covers its own lost-ack retry, keyboard containment, stale/offline recovery and five widths.
- Nine supported routes captured at seven widths (including reference-native size) without browser exceptions, document overflow, clipped task actions or overlapping tablet sidebar footer. `route-qa.json` records the capture matrix. Empty and 205-record fixtures are explicitly test-only and do not alter the operator preview database.
- Backend Ruff check/format pass, and the isolated runtime shard passes 402 tests. The prior complete 2,406-test backend result remains the baseline for unchanged backend source; no expensive unrelated backend suite is repeated for frontend-only edits.
- Initial JavaScript is approximately 384KB / 113KB gzip. Planning and approval inspectors remain lazy-loaded; the added bytes supply the expanded workflow and accessible task inspector without a new dependency.

## Review boundary

Recovery inside a task inspector only lists submissions for that locked task.
Recovery also rejects a foreign task before restoring it, and queue/retry requires
the immutable pending submission's task to match the current shared selection.
Changing task scope cannot replay another task's original command IDs.

Campaign validation after reconciling current main: frontend typecheck, ESLint,
232 Vitest tests and production build pass; API regressions pass (28 tests).
The normal browser smoke exercises current drawer/button actions, including
creation/correction acknowledgement loss and exact replay after reload. The
separate Office smoke verifies assignments, movement/stop/reload/continue,
actual worker activity, emergency stop and mobile layout. Both pass without
relaxing timeouts or assertions. Final-head hosted checks and review remain gates.

The approved shell and Overview implementations have merged. This feature branch
is reconciled with current main, including the merged source-edit review contracts,
and now targets main. The Tasks and Planning improvements retain their own review
boundary and shared state. Source-edit implementation is inherited from main.
Final-head checks and independent review remain required. Do not auto-merge.
