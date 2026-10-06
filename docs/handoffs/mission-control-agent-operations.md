# Mission Control: registered agent operations

## Publication and ownership

This milestone uses merged `main` at `9d5eab1c5e169f7c99f3cf1e9e2ecafa3a725fdf`.
It does not import the unmerged shell implementation.

- Shell PR: https://github.com/DrewDiStefano11/jarvis-agent-ecosystem/pull/75
- Shell exact reviewed head: `04eb461fdd14cb3508d4d77cb0c9fd212c73afd7`.
- Shell worktree: `C:/Users/DDistefano/Documents/jarvis-agent-ecosystem/.worktrees/mission-control-shell`.
- Shell external gates at 2026-10-06 10:28 America/New_York: review running;
  pull-request CI running. The earlier push CI run was superseded/cancelled by the
  pull-request run for the same head, not treated as a code failure.
- Agent branch: `codex/mission-control-agent-operations`.
- Agent worktree: `C:/Users/DDistefano/Documents/jarvis-agent-ecosystem/.worktrees/mission-control-agent-operations`.
- Agent publication follows this implementation commit. Its branch ref identifies
  the exact head; subsequent handoff records the PR and review head.

Open non-Mission-Control PRs remain backend-focused (#70–#74). No active PR touches
IdentityWorkforce, workforce tests, or the workforce smoke harness. #73 has a small
app test overlap with shell PR #75, but no identity operations overlap.

## Result

Registered runtime identities now have a dense read-only table of name/stable key,
agent type, lifecycle, enablement, and registry operational status. Existing name
and effective-capability filters combine with new lifecycle and enablement filters.
Showing/total counts disclose the filtered registry scope. No model, current task,
manager/team, utilization, or timestamp is invented from registry metadata.

Inspect opens and focuses the existing profile card inside Manage identity profiles.
The original registration, lifecycle/profile mutation controls, uncertain-ack recovery,
retired-identity guards, shared Planning targets and backend authorization remain
intact. Profile explanations sit beside controls. Registration/activation do not
create permissions or queue execution. Native disclosure/keyboard focus supplies
technical details without a separate domain store.

On phones, rows become compact labeled lists with Inspect; agent type remains in
the profile rather than using limited row width. Status text never wraps within a
word. Registry failures retain the existing stale-data warning. Demonstration agents
and the imported workforce catalog remain separate existing sections.

## Validation

- Backend code is identical to the shell milestone's merged-main baseline: full
  pytest passed 1525 tests with 2 skips; Ruff passed.
- Before this commit, backend Ruff and identity/RBAC pytest additionally passed:
  87 tests, one existing Starlette/httpx deprecation warning. Fresh temporary DB
  scope: `pytest-agent-identity-1` in this chat's visualization directory.
- Frontend typecheck, ESLint, 106 Vitest tests, production build passed.
- Two added integration tests cover combined filters without registry replacement
  or writes, and Inspect opening/focusing existing controls without commands.
- Extended `scripts/smoke-workforce.cjs` passed actual API registration, activation,
  profile edits, suspend/reactivate, disable/enable, shared Planning targets,
  lost acknowledgement recovery, no permission grants, and API restart durability.
- The real-API harness additionally exercised lifecycle/enablement filters, profile
  focus, and no horizontal overflow at 1440px, 390px and 320px.
- Evidence: `C:/Users/DDISTE~1/AppData/Local/Temp/jarvis-workforce-SO30f3`.
  `operations-desktop.png`, `operations-mobile.png`, and
  `operations-small-mobile.png` capture the registry section; full page screenshots
  also retain the existing surrounding navigation/catalog/demonstration UI.

The Browser plugin/skill is absent; the repository's Playwright Chromium workflow
is used. It launches isolated temporary API databases/ports, disables inference,
and stops only its own processes. No runtime DB or generated images are committed.

## Visual comparison

Reference:
`C:/Users/DDistefano/.codex/generated_images/01a11181-6f92-75e3-87b8-2a6413df8ab0/exec-03445994-39d0-465a-b05d-a6a11b0a5b22.png`.
The reference and current renders were inspected with view_image: charcoal surfaces,
purple actions, readable sans-serif hierarchy, the six-column dense table, aligned
filter controls, profile disclosure, and stacked mobile records. Explicit accessible
filter names repaired a native-browser label lookup defect. Mobile status text
wrapping and incomplete section screenshot capture were corrected.

Intentional deviations: actual registered names/statuses/counts replace illustrative
reference rows; current backend acknowledgement messages remain visible; optional
side inspector reuses inline existing profile controls rather than duplicating them;
no invented sort affordances, shared-permission badges, copy action, theme control,
or profile claims from the generated reference are implemented. Type is available
in mobile profile detail. This is a registry-section reference at a composite
aspect ratio; browser QA uses 1440x1000 plus actual phone widths. The surrounding
shell remains merged-main's current UI until independently reviewed PR #75 merges.

## Continue

Prioritize P0/P1/P2 feedback on #75 or this milestone. Then deliver a useful native
dependency/execution visualization using only persisted decomposition and coordinator
records. Keep readiness distinct from actual execution, preserve dependency keys,
include an accessible list, inspect full labels/deliverables, and avoid inventing
review/verification stages not exposed by merged contracts. Reconcile main between
major milestones, and never merge/delete branches here.


### Published review repair checkpoint

PR #78: https://github.com/DrewDiStefano11/jarvis-agent-ecosystem/pull/78. Initial exact head `2638851ea696f5e2ec22f9bd622d11220a7aeee1` received two P2 findings. Fixed lifecycle choices use the merged contract's four states, so the selected filter remains represented after its last identity transitions. Successful profile/lifecycle acknowledgements now live in the workforce section, surviving a card leaving search/lifecycle/availability filters. No domain state moved or added.

Regression tests cover reactivation under suspended, disabling under enabled, and renaming out of a name filter. Real API smoke reproduces lifecycle/availability transitions and confirms acknowledgements. Typecheck/lint/108 tests/build pass; screenshot/browser evidence `jarvis-workforce-EddRsT` in local Temp. Initial frontend/integrity/runtime-browser CI are green; backend CI pending. Republish and review the new exact head; no merge attempted.
