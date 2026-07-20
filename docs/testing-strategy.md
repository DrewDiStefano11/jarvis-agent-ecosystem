# Testing strategy

Phase 2A backend tests use isolated temporary SQLite databases. They migrate a blank database, recreate applications against one database, verify durable tasks, approvals, notifications, emergency state, audits, and idempotency, inspect outbox delivery, and interrupt/resume checkpointed workflows. CI upgrades a blank temporary database before Ruff and pytest.

Backend tests use FastAPI `TestClient` with 1 ms deterministic delays. They cover health/status/lists, unknown structured errors, task creation/retry, approval/rejection/duplicates/expiry/black risk, emergency stop, temporary agents, invalid transitions, start/pause/resume/reset, failure events, WebSocket snapshot/sequence, completed workflow/audit, and all manifests.

Frontend Vitest/Testing Library tests mock HTTP and WebSocket boundaries while rendering the real store and router. They cover dashboard seed state, agent/task shared details, hierarchy, approval safety, approval refresh, emergency display, mobile navigation, office shared state, duplicate event suppression, and gap-triggered resynchronization. TypeScript strict checking, ESLint, and production build are separate gates.

Manual smoke testing uses a real API/browser at desktop, 390×844, and 320px widths. Verify initial fetch, WebSocket connected state, task creation, the complete revision workflow, audit history, emergency stop/resume, failure/retry, offline indicator, reconnect, PWA metadata, keyboard focus, and reduced-motion emulation. Tests use no random behavior or external services.
