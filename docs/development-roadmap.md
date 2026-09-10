# Development roadmap

## Phase 1 — interface and simulator

Contract-first FastAPI/React application, deterministic orchestration, ordered events, approvals, audit, responsive PWA, office projection, manifests, and tests.

## Phase 2 — durable data and control-plane preparation

Introduce a local durable repository implementation, migrations, transactional outbox/idempotency, resumable workflow records, audit retention, encrypted configuration boundaries, and adapter interfaces. Preserve Phase 1 HTTP/event compatibility and keep real tools disabled.

## Later phases

Add scheduling/orchestration, local model adapters, memory/search, and then individually threat-modeled integrations behind approvals. Remote access, voice, and richer pixel animation follow only after identity, security, observability, and recovery foundations are proven.

## Phase 2A status

The durable local control plane is implemented with SQLite, Alembic, typed persistence records, outbox/idempotency, resumable workflow runs, validated checkpoints, audit retention, and safe restart detection. Real tools remain disabled.

## Phase 2B context-assembler status

The PR #9 prototype findings are integrated behind typed API contracts and Phase 2A durability. Provenance, structural isolation, redaction, injection signals, deterministic ordering/deduplication, bounded truncation, durable manifests, review gating, health metrics, and recovery-safe event publication are implemented. No model provider, autonomous worker, repository reader, or tool executor is part of this increment.

## Phase 2C local execution status

Provider-neutral contracts, disabled-by-default loopback model adapters, durable
Agent Runtime persistence, task leases, identity/RBAC authorization, and one narrow
`planning_review` autonomous worker are implemented. The worker has no tool,
filesystem, shell, browser, or external-integration execution path. Remote access,
general-purpose autonomous orchestration, and production user authentication remain
deferred.

## Autonomy acceptance status

A reusable autonomy acceptance, observability, and local-model evaluation
framework exists independently of the unfinished PR #62 (decomposition) and
PR #63 (coordinator) implementations. Twelve deterministic acceptance
scenarios validate the conceptual autonomy loop with production stages
(context grounding, runtime ledger, recovery, dependency unlocking) and
explicit fixtures for unfinished stages. Local-model evaluation covers nine
AI Hub roles with deterministic scoring in fixture mode plus opt-in
installed-local-model runs. See [autonomy acceptance](autonomy-acceptance.md).
Production decomposition/coordinator validation plugs in through the same
ports after #62/#63 merge; no autonomy completion is claimed before then.
