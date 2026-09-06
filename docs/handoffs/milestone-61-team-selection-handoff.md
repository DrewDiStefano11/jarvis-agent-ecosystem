# Milestone #61: Automatic Team Selection Handoff

## Summary
This milestone introduces automatic capability-aware team selection. The system infers required capabilities from a task objective, queries the active agent workforce, securely selects an appropriate manager, and assigns a minimal set of specialists to fulfill the capability requirements. The assigned team is durably persisted and injected into the task's context assembly.

## Exact Branch Information
* **Base Branch:** `main`
* **Final Base SHA:** `692eb6f030ceca035284f00811d5de1101b6d232` (or latest `main` post-rebase)
* **Final Head SHA:** `b23cbec3815c86d0dc7d6fd9874f673ea6b3371e`
* **Branch Name:** `codex/automatic-team-selection`
* **PR URL:** PR #61

## Completed Architecture
* **Capability Inference:** The `TeamSelectionService` makes a deterministic inference call to the model to output a `RequiredCapabilitiesResult` JSON payload, delineating explicitly required and optional capability keys alongside reasoning.
* **Manager Selection:** The selector queries the active workforce and currently selects the existing eligible Jarvis manager/planner identity. It explicitly avoids elevating arbitrary imported catalog specialists to manager roles.
* **Minimal Specialist Selection:** A greedy set-cover algorithm iterates through required capabilities, finding the candidate covering the most uncovered requirements, breaking ties deterministically via a `stable_key` sort, and stopping once requirements are met (capped at 6 specialists).
* **Durability:** The chosen team configuration is persisted in `Task.teamSelection` alongside rationale summaries. Legacy Phase 1 DB fields (`assignedManagerId` / `assignedAgentIds`) are intentionally NOT mutated during this phase to avoid polluting the underlying simulated-agents identity table with real external active identities.
* **Grounded Planning Integration:** Team selection automatically fires during `create_context_assembly()`. The result is safely embedded in the context assembly passed to the planner, explicitly restricting external catalog context to just the active team identifiers and roles.

## Files Changed
* `apps/api/app/team_selection/service.py` (New)
* `apps/api/app/team_selection/router.py` (New)
* `apps/api/app/models/team_selection.py` (New)
* `apps/api/app/models/domain.py`
* `apps/api/app/main.py`
* `apps/api/app/catalog/taxonomy.py`
* `apps/api/tests/test_team_selection.py` (New)
* `apps/api/tests/test_autonomous_worker.py`
* `apps/web/src/components/Details.tsx`
* `apps/web/src/types/contracts.ts`
* `scripts/smoke-local-planning.py`

## Selector Algorithm
The greedy set-cover algorithm is deterministic:
1. Identify all capability gaps.
2. Filter the workforce for active identities covering at least one gap.
3. Select the identity covering the most *remaining* gaps.
4. On a tie, break by highest total relevant capabilities, then fallback to `stable_key` alphabetical order.
5. Repeat until gaps are fully covered, up to a maximum of 6 specialists.
Missing capabilities block selection (persisted as `blocked_missing_capability`).

## Security
Selection strictly acts as an assignment mechanism and **grants zero authority**. No permissions, roles, ranks, workspace paths, or tool executions are intrinsically granted by selection. Active identities remain securely bounded by existing Jarvis RBAC logic.

## Validation
* **Ruff:** Formatted and linted cleanly across the backend.
* **Backend Pytest:** Clean run resolving legacy context assertion bugs related to task mutation.
* **Frontend:** Typecheck, lint, and build succeeded locally.
* **Runtime/Browser:** The capabilities request in the local planning smoke test is intercepted and validated uniquely, distinguishing capability inference requests from core planning requests.
* **Exact-head Actions:** Automatic CI checks have passed successfully.

## Remaining Limitations
* Currently falls back gracefully when capabilities are missing, but does not autonomously pause/alert developers mid-workflow to adjust catalog agents (relies on user inspection of task status).
* Task execution decomposition is deferred.

## Next Milestone
**#62 Automatic Task Decomposition + Assignment**
