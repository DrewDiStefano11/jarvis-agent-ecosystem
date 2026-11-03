# Final targeted repairs and propagation — 2026-10-16

Both requested workstreams are published. Eight heads changed. No force push,
merge, close, branch deletion, main change, or Campaign B/provider change occurred.
The unrelated primary checkout index was preserved. Work used the existing clean
`.campaign-work` worktree and new `codex/final-*-20261016` feature branches.

Campaign A adds `source_edit_planning/projection.py` to the admission policy source
digest and its existing dependency-upgrade regression. The regression proves a
projection implementation change rejects an earlier persisted admission with
`DEVELOPMENT_ADMISSION_CONFLICT`. The correction was merged in order through
#145 → #148 → #150, retaining parent history and child baseline/lineage behavior.

The published #144 commit `1554822c8fd4904763546a87dc30fb92c51ceb63` and regression
were verified intact; its completed tests were not rerun on the unchanged branch.
It was merged through #146 → #147 → #149 → #151 → #153. The sole conflict, in
#147 deployment configuration, was resolved by preserving `ConfiguredNativeConsumer`
and its unconditional assignment of either a wrapper or None. The inherited API
regression now checks wrapper delegates, valid replacement, explicit/omitted
disable, HTTP refusal before intent, and rejection of captured stale wrappers.
All later configuration/profile revocation guards remain intact.

The single consolidated GitHub snapshot was captured at approximately 09:23
America/New_York on October 16. All 11 active PRs have incomplete CI; no new
completed active-head CI failure or unresolved actionable review thread was found.
The two repaired threads on #145 and #144 were resolved after verification.

| PR | Exact head SHA | CI status / run ID | Latest Codex review | Unresolved actionable findings | Conflicts | Readiness |
|---|---|---|---|---|---|---|
| [#154](https://github.com/DrewDiStefano11/jarvis-agent-ecosystem/pull/154) | `9983a14eeb7f7907bb77e74143f405ee694d9623` | [In progress, 38901242344](https://github.com/DrewDiStefano11/jarvis-agent-ecosystem/actions/runs/38901242344) | Clean, exact head | None observed | No | **CI RUNNING** |
| [#139](https://github.com/DrewDiStefano11/jarvis-agent-ecosystem/pull/139) | `89d00630c9166ab61ee41fbfd42e34676c48fb3a` | [In progress, 38898768211](https://github.com/DrewDiStefano11/jarvis-agent-ecosystem/actions/runs/38898768211) | Clean, exact head | None observed | No | **CI RUNNING** |
| [#145](https://github.com/DrewDiStefano11/jarvis-agent-ecosystem/pull/145) | `0de82d6c23bf9e64198bf5cb6a25a15083498e83` | [Queued, 38906611515](https://github.com/DrewDiStefano11/jarvis-agent-ecosystem/actions/runs/38906611515) | Requested; quota blocked | None observed | No | **DEPENDENCY BLOCKED** |
| [#148](https://github.com/DrewDiStefano11/jarvis-agent-ecosystem/pull/148) | `b2831f65912a3d56ee0a7064991fefe19138ef14` | [Queued, 38906762113](https://github.com/DrewDiStefano11/jarvis-agent-ecosystem/actions/runs/38906762113) | Requested; quota blocked | None observed | No | **DEPENDENCY BLOCKED** |
| [#150](https://github.com/DrewDiStefano11/jarvis-agent-ecosystem/pull/150) | `5908b2f3de87576196981bf822045fde11eb15e6` | [Queued, 38906902864](https://github.com/DrewDiStefano11/jarvis-agent-ecosystem/actions/runs/38906902864) | Requested; quota blocked | None observed | No | **DEPENDENCY BLOCKED** |
| [#144](https://github.com/DrewDiStefano11/jarvis-agent-ecosystem/pull/144) | `1554822c8fd4904763546a87dc30fb92c51ceb63` | [Queued, 38904126536](https://github.com/DrewDiStefano11/jarvis-agent-ecosystem/actions/runs/38904126536) | Existing request failed: unknown service error | None observed | No | **CI RUNNING** |
| [#146](https://github.com/DrewDiStefano11/jarvis-agent-ecosystem/pull/146) | `8f666c9860bd1b68e72bef1c0e7a2512a76dae08` | [Queued, 38906973410](https://github.com/DrewDiStefano11/jarvis-agent-ecosystem/actions/runs/38906973410) | Requested; quota blocked | None observed | No | **DEPENDENCY BLOCKED** |
| [#147](https://github.com/DrewDiStefano11/jarvis-agent-ecosystem/pull/147) | `2405f97718db150df29a0638f63e89374c56a83b` | [Pending, 38907116913](https://github.com/DrewDiStefano11/jarvis-agent-ecosystem/actions/runs/38907116913) | Requested; quota blocked | None observed | No | **DEPENDENCY BLOCKED** |
| [#149](https://github.com/DrewDiStefano11/jarvis-agent-ecosystem/pull/149) | `2b7994bcb4ab60ada054766390b4240ee3f5271d` | [Queued, 38907179259](https://github.com/DrewDiStefano11/jarvis-agent-ecosystem/actions/runs/38907179259) | Requested; quota blocked | None observed | No | **DEPENDENCY BLOCKED** |
| [#151](https://github.com/DrewDiStefano11/jarvis-agent-ecosystem/pull/151) | `c9e89067af3859d572f825e23041e04491917c5a` | [Pending, 38907277106](https://github.com/DrewDiStefano11/jarvis-agent-ecosystem/actions/runs/38907277106) | Requested; quota blocked | None observed | No | **DEPENDENCY BLOCKED** |
| [#153](https://github.com/DrewDiStefano11/jarvis-agent-ecosystem/pull/153) | `cf08191d528eb9ac6d72908fc5ff609d1c59fe7f` | [Pending, 38907413438](https://github.com/DrewDiStefano11/jarvis-agent-ecosystem/actions/runs/38907413438) | Requested; quota blocked | None observed | No | **DEPENDENCY BLOCKED** |
| [#143](https://github.com/DrewDiStefano11/jarvis-agent-ecosystem/pull/143) | `d4984a92c66e59a184d7181db4653314f4a738bb` | [Historical failure, 38766327095](https://github.com/DrewDiStefano11/jarvis-agent-ecosystem/actions/runs/38766327095) | Historical clean review on older commit | Historical backend system failure; superseded | Yes | **SUPERSEDED** |

CI statuses describe the workflow as reported by GitHub; queued workflows can have
individual jobs already running. Canceled duplicate push workflows have replacement
pull-request workflows and are not counted as failures. Standard `ci.yml` was
automatically triggered for every changed head, so no extra dispatch was needed.

Exactly one fresh review was requested on each of the eight changed heads. All
eight failed because the review quota was exhausted. #153's failed review summary
was diagnosed with one follow-up read, confirming the same quota notice. No review
was re-requested. #144 retained its existing exact-head request, which failed with
an unknown service error. Failed review summaries contain generic findings flags;
the review-thread snapshot contains no unresolved actionable threads. Those flags
are not evidence of new code findings. Clean receipts from older commits are not
carried forward to changed heads. Fresh reviews remain a merge gate.

#143's known failed system run and conflicts were checked once using the retained
failure evidence. Its head is an ancestor of #144. No separate #143 repair or merge
is required. No new repair loop, review polling, CI waiting, or automation started.

Validation:

- #145, #148, #150: admission dependency invalidation regression passed once on each
  final composition; full backend Ruff check and format check passed on each.
- #146: 38 native API and correction-triage tests passed; Ruff/format passed.
- #147: 45 native API, profile-revocation, and triage tests passed; Ruff/format passed.
- #149: 71 native API, profile-revocation, readiness, and triage tests passed;
  Ruff/format passed.
- #151: 81 composed tests plus 5 source-base tests passed; Ruff/format passed.
- #153: 86 composed tests plus 12 correction-link/review-guard tests passed;
  Ruff/format passed.
- Frontend typecheck, ESLint, Vitest (33 files, 234 tests), and production build
  passed. Every campaign head retains frontend tree
  `1ea2ab1f0f342d22b68b23601e8e41ebbce3ddd5`, so this evidence applies throughout.
- Initial sandboxed backend fixtures were refused by filesystem boundary checks;
  the targeted checks passed with native filesystem access. A broader native
  dispatch/baseline run was stopped to keep validation scoped; no full backend
  suite pass is claimed. All test databases were temporary and excluded from commits.

Evidence remains in `.campaign-evidence/final-*-focused.xml`,
`final-151-source.xml`, `final-153-links.xml`, `final-launch-*.json`, and
`final-targeted-{prs,threads,runs-*}.json`. This report is a local handoff and was
not staged into the unrelated primary checkout.

Manual merge order after CI/review gates complete:
**#154 → #139 → #145 → #148 → #150 → #144 → #146 → #147 → #149 → #151 → #153**.
Do not merge #143 separately. No PR is currently classified MERGE NOW.
