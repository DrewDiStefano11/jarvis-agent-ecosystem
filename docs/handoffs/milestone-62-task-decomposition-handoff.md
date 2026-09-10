# Milestone 62 - Automatic Task Decomposition + Assignment (Completed)

## Implementation Summary

Milestone #62 implements the capability to safely decompose a complex objective into bounded, specialized subtasks and automatically assign those subtasks to appropriate agents within a selected team. 

This completes the WHAT (Decomposition) of the autonomous capability framework, building directly on Milestone #61 (WHO - Team Selection). 

### Key Accomplishments
1.  **Decomposition API & Schema**: Added DecompositionService which uses model_router.execute to deterministically create DecompositionProposal objects with 1-12 bounded subtasks. 
2.  **Context-Grounded Planning**: Bound decomposition to the new ContextAssembly system, guaranteeing the LLM grounds its plan entirely on authoritative rules, permissions, and tool catalogs, rather than arbitrary system prompts. 
3.  **Dependency Graph & Validation**: Enforced strict DAG validation and capability coverage. Subtasks may only depend on upstream artifacts, avoiding circular recursion. 
4.  **Error Handling & Edge Cases**: 
    - Re-planning requests on stale inputs or context changes are automatically detected via fingerprinting.
    - Operator overrides and task status checks prevent unsafe parallel execution. 
    - Graceful degradation: The system handles UnknownProviderError or capability inference failures gracefully without throwing HTTP 500s on the /api/context/assemblies endpoint. 

## Final CI Validation & Fixes Applied

In the final hardening phase, several failing CI checks were resolved:
- **E2E Browser Race Condition**: Playwright UI tests were timing out with element detached from DOM during context assembly navigation. This was due to React's concurrent mode re-rendering <option> tags rapidly while Playwright was trying to selectOption(). Fixed by enforcing .waitFor({ state: 'attached' }).
- **Capability Inference Masking**: The ssign_team function was masking model routing failures by returning an empty team instead of raising the error. This caused downstream test failures (e.g. 	est_catalog.py) because active capabilities like software.python weren't included in the system-workforce-snapshot. Removed the inner 	ry/except to allow UnknownProviderError to bubble up safely. 
- **Graceful Context Assembly**: Wrapped the background ssign_team call in pps/api/app/main.py's POST /api/context/assemblies handler so that if team assignment throws (e.g. due to missing LLM provider), the assembly itself still completes successfully. 
- **DecompositionService Scoping Bug**: Fixed an UnboundLocalError in the create_context_assembly endpoint caused by a conditional import masking an outer import. Added 	est_context_assembly_replay_decomposition_scoping_regression to prevent regressions.
- **Autonomous Worker Test Coverage**: Updated the backend FakeRouter test fixture to properly generate mock DecompositionProposal objects instead of failing when the schema was requested.

## Security Guarantee
This milestone adds ZERO authority escalation.
- permissions = 0
- oles = 0
- anks = 0
- workspace grants = 0
- 	ool grants = 0
- system-agent elevation = 0

Assignment remains responsibility mapping only. Jarvis remains authoritative. Imported specialists do not become system managers.

## Known Limitations & Next Steps
- Milestone #63 is EXPLICITLY DEFERRED. The execution/coordinator loop was not implemented.
- Missing capability handling correctly sets 
eeds_team_reselection but does not automatically attempt to re-select a wider team or perform dormant catalog activation.

## Final Note on Source of Truth
**Do NOT create an endless self-referential SHA loop. GitHub PR metadata is authoritative for the final exact head SHA, base SHA, and Actions run ID. Please check the PR body on GitHub for the exact final CI validation states.**
