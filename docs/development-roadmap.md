# Development roadmap

## Phase 1 — interface and simulator

Contract-first FastAPI/React application, deterministic orchestration, ordered events, approvals, audit, responsive PWA, office projection, manifests, and tests.

## Phase 2 — durable data and control-plane preparation

Introduce a local durable repository implementation, migrations, transactional outbox/idempotency, resumable workflow records, audit retention, encrypted configuration boundaries, and adapter interfaces. Preserve Phase 1 HTTP/event compatibility and keep real tools disabled.

## Later phases

Add scheduling/orchestration, local model adapters, memory/search, and then individually threat-modeled integrations behind approvals. Remote access, voice, and richer pixel animation follow only after identity, security, observability, and recovery foundations are proven.

## Phase 2A status

The durable local control plane is implemented with SQLite, Alembic, typed persistence records, outbox/idempotency, resumable workflow runs, validated checkpoints, audit retention, and safe restart detection. Real tools remain disabled.
