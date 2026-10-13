# Premium Mission Control: shell milestone

This milestone establishes the shared appearance and navigation for the ongoing UI campaign. The primary reference is supplied image 1 (Mission Control); images 2–4 establish the same shell across Workforce, Business Lab and future Software Factory surfaces. Their example metrics and unsupported routes are deliberately excluded.

## User-visible changes

- Group existing destinations into Operations, Workspace and Observe. Keep every supported route and the mobile More menu.
- Introduce midnight surfaces, restrained purple accents, readable type, common spacing, semantic status colors, inputs, buttons, panels and drawers in `apps/web/src/styles/design-system.css`.
- Move search into the toolbar so it cannot cover records. Rename the launcher to Search Jarvis to describe its actual capabilities. Ctrl/Cmd K, search groups, stale-state protection and existing creation/retry behavior remain supported.
- New task on Mission Control opens an accessible native drawer in place. Reuse the existing idempotent creation form, keep its draft after closing, disclose that creation does not execute work, and show the acknowledged task with an Inspect action.
- Add reduced-motion-aware loading skeletons while synchronization is pending.

## Component contract

`ShellNavigation` owns the route inventory and desktop grouping. Navigation uses links; workflow actions use buttons. `ActionDrawer` uses a native modal dialog for inert background, Escape, keyboard containment and focus return. `NewTaskButton` reads AppStore connection/loading state and delegates all writes to the existing TaskCreateForm. It creates no separate domain state or backend authority. Only drawer visibility, drafts and creation feedback are local UI state.

Tokens cover background/surfaces, text/muted text, border, accent, success/warning/danger, radius, spacing and interaction timing. Feature styles retain layout ownership. The Office remains lazy-loaded and its placement/navigation contracts are unchanged. No new dependencies, API contracts, endpoints or permissions are introduced.

## Visual comparison and intentional deviations

| Reference difference | Rendered change / next milestone |
| --- | --- |
| Small brand and flat navigation | Letterspaced wordmark, grouped navigation, wider rail, clear selected state |
| Floating command control covers records | Toolbar search, no fixed overlay or artificial content clearance |
| Pale flat primary buttons | Purple gradient, white labels, consistent hover/focus treatment |
| Dense small labels and gray surfaces | Larger shared type, midnight surfaces, softer borders and semantic statuses |
| Creation link redirects away | In-place drawer with draft retention and persisted acknowledgement |
| Reference is rich with KPIs/charts | Dashboard composition and genuine scoped record charts belong to the next milestone; fabricated reference telemetry is excluded |
| Generated concept implies unconfigured execution is a system failure | Keep verified healthy-system wording; disabled autonomous execution is configuration, not an outage |

## Verification

Run frontend typecheck, ESLint, Vitest and production build. Run backend Ruff check/format and all four existing isolated pytest shards before commit. Real-API acceptance uses disposable databases, providers disabled and loopback ports. `smoke-mission-control.cjs` now covers in-place creation, persisted task inspection, Escape/focus return, emergency-stop confirmation, stale/reconnect behavior, navigation and 2560, 1920, 1440, 1024, 768, 390 and 320px widths. `smoke-command.cjs` verifies lookup, focus containment, lost-ack retry and stale-state gates; toolbar placement replaces its obsolete floating-control clearance assertion.

In-app browser inspection supplements the deterministic Playwright acceptance harness. Both before-and-after captures and the supplied/generated references are inspected visually. QA captures are operator artifacts outside tracked source. Screenshot data comes from seeded demonstration records in an isolated fixture installation; it is not production evidence.

## Remaining campaign work

Shell validation completed: frontend typecheck and ESLint passed, 217 Vitest tests passed, production build passed, both real-API browser harnesses passed, and all nine routes passed the six-width overflow/reduced-motion check after fixing a long Planning configuration label. Backend Ruff check/format passed; the four isolated pytest shards passed 2,406 tests with three platform skips. The later dashboard milestone adds its own chart and workflow tests on top of this baseline.

Dashboard metrics and charts, direct identity selection, embedded approval review, task/agent/planning workflows, Business Lab, Office refinements, Software Factory components against merged contracts, and the final consistency/accessibility audit remain outstanding. This shell is the foundation, not final product acceptance. Hosted exact-head CI requires publication approval; hosted Codex reviews are intentionally not requested.
