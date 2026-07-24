# Superseded PR #9: Context Assembler Prototype

**Do not merge:** This pull request must not be merged or cherry-picked wholesale.

## PR History

- **PR Number:** #9
- **Original Title:** feat: Add phase-2b-context-assembler prototype
- **Original Head Branch:** `prototype/phase-2b-context-assembler-2091645386854833883`
- **Last Known Head SHA:** `716536787ed3360b1a8442fff81d9286a63ddc5d`

This PR was opened against an obsolete, pre-Phase 2A version of the `main` branch. Consequently, it appeared to change many unrelated control-plane files when compared with the current `main` branch. The branch was unmergeable and had failing CI at its reviewed head.

## Replacement Path

This prototype was successfully replaced by **PR #10**, which provided a current-`main` integration of the context assembler.

## Retained Prototype Ideas

PR #10 retained several key ideas from the PR #9 prototype:

- Trust ordering
- Provenance hashes
- Explicit source approval
- Context isolation
- Credential redaction
- Heuristic injection detection
- Deterministic deduplication
- Token budgeting
- Truncation reporting
- Durable manifests
- Review gating

## Added Production Integration Requirements

When porting the concept in PR #10, several production integration requirements were added that were missing from the prototype:

- Typed API contracts
- Proper Alembic migration ordering
- Durable persistence
- Idempotency
- Transactional audit and outbox publication
- Restart recovery
- Health metrics
- Frontend contract support

## Lessons for Future Agent-Generated PRs

A useful prototype should be systematically ported onto the current architecture rather than attempting to merge it with obsolete repository history. Combining prototype exploration directly with stale history leads to large, unreviewable diffs and broken integrations.
