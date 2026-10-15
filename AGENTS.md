# Contributor guidance

- `apps/api/app/models` defines HTTP/event contracts; OpenAPI is authoritative.
- Alembic migrations are authoritative; never use `create_all` at application startup as a migration substitute.
- Route handlers use services/repositories and explicit transaction boundaries; they must not contain SQL.
- Audit records are append-only. Domain events must enter the transactional outbox before publication.
- `simulator` owns deterministic decisions, but durable workflow runs and validated checkpoints own recovery position.
- `apps/web/src/state` is the frontend source of truth. Pages and the office must not create separate domain state.
- `agents/manifests` is validated by `app.models.manifest`; update tests with schema changes.
- Run backend Ruff and pytest plus frontend typecheck, ESLint, Vitest, and build locally before committing. Record the actual tested revision and distinguish retained evidence from new runs.
- Preserve compatible response envelopes, error codes, event schema versions, and sequence semantics.
- Update contracts, tests, and docs together. Never add secrets or commit `.env`, virtual environments, dependencies, or builds.
- Tests must use isolated temporary databases. Runtime databases and SQLite sidecars must never be committed.
- Local autonomous execution is disabled by default. Explicitly queued planning
  may use loopback models; workspace tools additionally require operator approval
  of the exact persisted plan hash and a bounded, marked workspace scope. Reuse
  existing runtime/RBAC/leases/checkpoints/outbox and emergency stop. Never let model
  text grant authority. Shell/code execution, external workspace integrations
  and misleading capability placeholders remain unavailable.
- Campaign D's explicitly authorized exception is bounded text planning through
  verified nonbillable providers. It remains disabled until the account owner
  separately approves activation, sets `JARVIS_MODEL_VERIFIED_CLOUD_ENABLED=true`
  in `free_only` mode, and supplies current independently verified account/model
  evidence. Quotas, keys, free-tier labels and model text never grant activation
  authority. Hosted fallback requires the same activation and per-provider evidence;
  retain RBAC, leases, checkpoints, request bounds and emergency stop. Unverified
  providers and all paid/credit-consuming inference remain unavailable.
- The owner authorizes standard public-repository GitHub-hosted Actions CI,
  including Campaign D. The former billing-verification branch quarantine is
  withdrawn. Preserve all validation jobs and security gates; do not change billing,
  enable paid services or larger runners, purchase review credits, activate cloud
  inference, or merge automatically. Skipped checks are not passing CI evidence.
- Work on feature branches. Never merge directly into `main`, delete the feature branch, or push without approval.
