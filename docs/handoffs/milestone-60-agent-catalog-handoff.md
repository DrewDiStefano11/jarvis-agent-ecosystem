# Milestone 60: Agent Catalog Handoff

## Overview
This handoff finalizes Milestone 60, which delivers a secure, durable, and explicit agent and skill catalog for Jarvis. The external content is imported offline or online, normalized predictably, versioned immutably, and integrated manually through a deliberate approval and activation flow.

- **Repository**: DrewDiStefano11/jarvis-agent-ecosystem
- **Branch**: `codex/agent-skill-catalog`
- **PR**: #60

## Completed Work & Architecture
- **Durable Catalog Entities**: Added `CatalogSourceRow`, `CatalogEntryRow`, `CatalogRevisionRow`, and `CatalogActivationRow` via migration `20260906_09` to persist sources, unreviewed data, and activation linkages respectively.
- **Immutable Provenance**: Triggers on `catalog_revisions` and `catalog_sources` enforce strict append-only immutability.
- **Import Pathway**: Scripts to acquire and snapshot GitHub repositories/commits deterministically (`import-agent-catalog.py`). Supports offline paths and dry runs.
- **Explicit Review**: UI/APIs for explicitly reviewing and activating specialists.
- **Strict Role Boundaries**: Activated identities receive normalized `capabilities` but absolutely ZERO roles, team authority, permissions, rank, or execution workspaces automatically.
- **Bounded Planning Context**: `IdentityService.workforce_snapshot` provides the actual runtime with the true identity count and capabilities limit.
- **Curated Workforce**: 22 curated specialists proposed via `scripts/curate-agent-workforce.py` from pinned upstream sources, avoiding blind bulk approvals.

## Pinned Source SHAs
- **wshobson/agents**: `a30778f8c4e6b0a87567941b7cca4f534bf642b6`
- **VoltAgent/awesome-agent-skills**: `e4f7a502a78253550890e8b356d43f50192415ae`

## Validated Acceptances
- **Backend Tests**: Full suite passed (clean run via `pytest -q`).
- **Frontend Tests**: 99 Vitest tests passed cleanly.
- **Runtime Browser & Office**: `smoke-local-planning.py` and office browser tests passed seamlessly.
- **Catalog Browser Workflow**: The UI import, inspect, review, activate, and reload workflow successfully tested via `scripts/smoke-catalog.cjs` UI harness.
- **Security Validation**: Review confirms external data does not upgrade itself, no automatic role/workspace/shell assignments occur during import/activation, and collision/downgrade logic safely preserves isolation and integrity.

## Remaining Limitations & Next Milestone
- **Automatic skill/team selection is NOT in this milestone.**
- **No vector search/embeddings.**
- **Next Step**: Milestone #61 will focus on automatic team selection based on the capability taxonomies mapped here.
