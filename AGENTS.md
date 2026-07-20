# Contributor guidance

- `apps/api/app/models` defines HTTP/event contracts; OpenAPI is authoritative.
- `apps/api/app/services` owns repositories and broadcasting; `simulator` owns deterministic decisions.
- `apps/web/src/state` is the frontend source of truth. Pages and the office must not create separate domain state.
- `agents/manifests` is validated by `app.models.manifest`; update tests with schema changes.
- Run backend Ruff and pytest plus frontend typecheck, ESLint, Vitest, and build before committing.
- Preserve compatible response envelopes, error codes, event schema versions, and sequence semantics.
- Update contracts, tests, and docs together. Never add secrets or commit `.env`, virtual environments, dependencies, or builds.
- Phase 1 prohibits real models, external integrations, databases, automation, shell tools for agents, and misleading capability placeholders.
- Work on feature branches. Never merge directly into `main`, delete the feature branch, or push without approval.
