# Superseded PR #8: Worker Supervisor Prototype

**Do not merge:** This pull request must not be merged, rebased, cherry-picked wholesale, or used as the source of truth.

## PR History

- **PR Number:** #8
- **Original Title:** feat: Implement Phase 2B Worker Supervisor Prototype
- **Original Head Branch:** `prototype/phase-2b-worker-supervisor-17647116720871462399`
- **Last Known Head SHA:** `51861b1b086e39ed1398819509195a1a667344a8`

This PR was opened against an obsolete, pre-Phase 2A version of the `main` branch. As a result, GitHub displayed a large set of unrelated Phase 2A files as part of the PR diff, falsely implying that the PR itself modified them. Furthermore, the branch was fundamentally unmergeable and exhibited failing CI checks at its reviewed head.

## Replacement Path

The repair and replacement path for this work is **PR #13**.

*Note: PR #13 itself still requires additional fixes before it can be merged, and must not be described as already approved or production-ready.*

## Architectural Boundary

This prototype highlighted an important architectural boundary that must be preserved:

- **Application Control Plane:** The application control plane strictly owns tasks, workflow checkpoints, durable audits, idempotency, and transactional outbox publication.
- **Supervisor Prototype:** The supervisor prototype strictly owns only OS-process lifecycle behavior.
- **Strict Separation:** A worker supervisor must never become a second task orchestrator.

## Lessons for Future Agent-Generated PRs

1. **Verify changed-file lists:** Never trust a claimed changed-file list without explicitly comparing the branch against the actual target branch.
2. **Process Identity:** Process identity cannot rely on PID existence alone.
3. **Deterministic Testing:** Lifecycle tests must assert one deterministic outcome.
4. **State Authority:** Prototype-local state must not replace or overwrite authoritative application state.
5. **Context Matters:** Green local tests do not compensate for an obsolete base branch.
