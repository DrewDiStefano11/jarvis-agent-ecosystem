# Grok frontend campaign

Independent of the Codex self-build campaign. Do not treat this file as a self-build handoff.

## Scope

Pages: Business Lab, Agents, Office. No API, migration, shared state, routing, Runtime, Tasks, Dashboard, or self-build edits.

## PR 1 — Business Lab

- Branch: `grok/business-lab-workspace`
- Title: `feat(web): improve Business Lab workspace experience`
- Status: implementation complete on this branch; open the PR before treating it as reviewable
- Files:
  - `apps/web/src/pages/BusinessLab.tsx`
  - `apps/web/src/pages/business-lab/objectives.ts`
  - `apps/web/src/styles/business-lab.css`
  - `apps/web/tests/business-lab.test.tsx`
  - `apps/web/tests/business-lab-objectives.test.ts`
  - `docs/frontend/business-lab-workspace.md`
- Behavior: search, recorded-status filter, workflow-stage grouping, sort, honest empty/error states, queued-only creation confirmation, runtime vs demonstration participant labels.
- Validation: local `pnpm typecheck`, `pnpm lint`, `pnpm test` (27 files, 205 tests), and `pnpm build` passed in `apps/web` on 2026-10-10.
- Review: not requested until the PR exists. CI on the exact head is not yet observed.
- Depends on: latest `main` (`2c9e0b3`). Not stacked.

## Later milestones

- PR 2 Agents / workforce presentation
- PR 3 Office interaction and layout
- PR 4 Accessibility hardening of the three screens
- PR 5 Focused regression coverage

## Conflicts

Open PRs at campaign start: #94 and #93 (self-build). Neither edits the Business Lab page. No overlap recorded.
