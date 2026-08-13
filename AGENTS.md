# Contributor guidance

- `apps/api/app/models` defines HTTP/event contracts; OpenAPI is authoritative.
- Alembic migrations are authoritative; never use `create_all` at application startup as a migration substitute.
- Route handlers use services/repositories and explicit transaction boundaries; they must not contain SQL.
- Audit records are append-only. Domain events must enter the transactional outbox before publication.
- `simulator` owns deterministic decisions, but durable workflow runs and validated checkpoints own recovery position.
- `apps/web/src/state` is the frontend source of truth. Pages and the office must not create separate domain state.
- `agents/manifests` is validated by `app.models.manifest`; update tests with schema changes.
- Run backend Ruff and pytest plus frontend typecheck, ESLint, Vitest, and build before committing.
- Preserve compatible response envelopes, error codes, event schema versions, and sequence semantics.
- Update contracts, tests, and docs together. Never add secrets or commit `.env`, virtual environments, dependencies, or builds.
- Tests must use isolated temporary databases. Runtime databases and SQLite sidecars must never be committed.
- Phase 2C permits only the disabled-by-default, explicitly queued local-only
  `planning_review` worker documented in `docs/autonomous-worker.md`. Remote models,
  provider fallback, agent tools, external side effects, and misleading capability
  placeholders remain prohibited.
- Work on feature branches. Never merge directly into `main`, delete the feature branch, or push without approval.
