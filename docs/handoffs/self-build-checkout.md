# Durable workspace preparation — October 7, 2026

This increment implements internal durable preparation, not a completed checkout capability. No creation route, Git mutation, source editing, development command execution or ready workspace is exposed.

## State and dependencies

Branch: `codex/self-build-checkout`. Worktree: `C:/Users/DDistefano/.codex/worktrees/self-build-checkout/jarvis-agent-ecosystem`. Parent: #91 at `9f2f2ea`, stacked on #89. Latest fetched main is `de18e2aafd56eda98cd794b3883098cadd0be4c5`; the human merged #90 validation planning and #92 inactivity supervision. No agent merged a PR. The later #92 branch step-budget delta is already in #89/#91; no replacement PR is needed.

#89 abandonment approval/owner and alias collision repairs are pushed at `8a69b41`. #91 bounded image-read, alternate object-store coherence and Windows direct-executable fixture repairs are pushed at `9f2f2ea`. Those parent changes are integrated here at `560f219`; 118 combined creation preparation/workspace/inspection tests pass. Exact-head CI and independent re-review remain pending. Parent head CI/review evidence does not automatically cover this new increment.

## Implemented behavior

Migration `20261007_13` adds nullable private `creation_json` to the existing workspace projection. Absent intent is SQL NULL. Populated intent blocks destructive downgrade. Database revision contracts and existing head expectations advance together.

Immutable creation plans bind the exact generated reservation, measured inspection/tree/inventory, pinned tool and implementation policy, fixed file/total-byte limits and a read-only initial scope. Preparation requires separate authenticated operator approval, the original live lease, running owned attempt, current task RBAC and emergency-stop checks. Model text grants no authority.

Preparation persists a private nonce/operation and append-only audit/outbox evidence, records a native runtime checkpoint with the existing execution fence and a domain commit guard, then verifies its acknowledgement before storing the checkpoint reference. Crash gaps before checkpoint and before projection acknowledgement recover the same operation/checkpoint. A successor lease cannot silently adopt the operation. Public audit and checkpoint metadata exclude the private nonce and lease token. Historical reads work with mutation disabled and validate persisted plan/ownership/checkpoint integrity.

Internal namespace ownership uses pinned native directory/file handles and existing per-workspace locking. A private marker outside the future checkout proves ownership but grants no execution authority. Foreign or unmarked artifacts require explicit recovery; nothing deletes another workspace.

Internal native process cleanup reuses WindowsJob kill-on-close. Windows starts a native executable suspended, assigns the job, repeats authority, then resumes its unique initial thread. Failure kills the suspended process. POSIX uses an owned session/process group. Cleanup is not a sandbox for model-authored development code. The required Linux native-Git job also exercises these primitives.

## Validation

Frozen final source: full backend **2,080 passed, 2 existing environment skips**, 1,346.86 seconds; log `.local/creation-stable-full.log`. Ruff lint/format: 318 files. Authoritative migration roundtrip passes; collection/shard coverage: 2,082 cases. Frontend typecheck, ESLint, 194 Vitest cases and build pass on unchanged frontend sources. Focused foundation/planner: 117 passed. Creation preparation and migration guard: 15 passed.

An earlier mixed-revision full run failed four assertions after source/migrations changed during collection/execution. Those assertions pass in a fresh interpreter; the frozen final full run above supersedes it. No assertions were removed or new skips introduced. Independent hosted review and exact-head CI remain required before merge-ready handoff.

## Next production work

1. Implement structured `git worktree add --no-checkout` for only the approved generated branch, owned target and exact base. Revalidate pinned executable, live authority and repository metadata; never use force or model-selected paths.
2. Verify worktree/common-directory pointers, HEAD and branch on acknowledgement/replay. Foreign or uncertain artifacts stop for recovery, without duplicate checkout or lease adoption.
3. Materialize bounded verified raw blobs through native filesystem handles, preserving allowed modes. Do not execute checkout filters/hooks or lazy network fetch. Enforce per-file/total/count limits and protected path policy.
4. Initialize only the dedicated worktree index with fixed structured Git operations. Verify files/modes/index/HEAD before persisting a validated native ready checkpoint and publishing ready state.
5. Integrate repository editing with the existing approved tool journal/fences/provenance. Real development-code execution requires configured OS/container confinement; fixed command arguments alone are insufficient. This host has no installed WSL distribution or available Docker CLI.
6. Continue validation execution, Git publication, independent review/CI repair and Mission Control using native durable state. Final acceptance still requires a useful real Jarvis improvement driven through the production Self-Build path, followed by an improvement audit. The campaign is not complete.

Hosted finding 4211888591: creation intent/checkpoint preparation previously left
an earlier abandonment approval valid. Abandonment preview now binds a digest of
the complete private creation projection (hash only; no nonce/token disclosure).
Both intent persistence and checkpoint acknowledgement change that digest, so a
prior reservation-only approval fails SELF_BUILD_PLAN_CHANGED. Fresh exact
operator abandonment remains possible and preserves all creation recovery history.
Tests cover both the pre-checkpoint crash gap and acknowledged preparation.

#91 adds another repair at 4b7dcea for per-command Linux metadata namespace
replacement. Integrate it and validate the combined head before merge-ready claim.
The next driver's draft is in self-build-native-checkout, with 31 native
registration/preparation/process cases passing. It remains unexposed/uncommitted;
Linux mutation pathname containment and durable registration checkpoint integration
still require completion before any production entry point can be added.


## October 9 recovery review repair

Current #93 is stacked on reconciliation #94. P2 4235754777 identified rejection
of a renewed exact-plan approval after the original preparation approval expires
inside a checkpoint crash gap. Preparation now accepts a fresh current configured
operator approval validated by the existing full fence, while preserving the
original record approval, operation, private nonce, integrity digest and checkpoint
identity. The checkpoint acknowledgement audit event records the approval used
for current authorization. Neither a successor lease/attempt nor a changed plan
can be adopted. Native regressions exercise both intent-before-checkpoint and
checkpoint-before-acknowledgement with expired original and fresh exact approval.
Final #94 namespace-notification repair must also be integrated before re-review;
no automatic merge or completed checkout/factory claim.
