# Local development proposals implementation plan

> **For agentic workers:** Use superpowers:executing-plans with test-driven boundaries.

**Goal:** Connect explicitly enabled loopback Ollama text proposals to the existing protected development service without enabling hosted inference, candidate code execution or promotion.
**Architecture:** Reuse the native Ollama adapter in a separate single-provider, one-attempt router. Retain durable admission, exact source review, lease, checkpoint, budgets, audit/outbox and emergency stop. Add an optional local endpoint to DevelopmentLimits, omitted when absent for historical response compatibility, so an admission binds the actual endpoint and cannot survive rerouting. All hosted choices remain quarantined.
**Tech stack:** Python/FastAPI/Pydantic/httpx, native services, existing isolated pytest fixtures.
**Spec:** Operator final integration campaign attachments, B/D requirements for real approved source work, $0 spending and exact persisted plan approval.

## Constraints and review focus

Default remains disabled. Only an explicitly configured native Ollama adapter in local_only mode with code_generation and structured_output capabilities may connect. No API key reads, cloud fallback, tools, shell, code execution, validation execution or publication activation. Model quality and real Ollama host remain unverified. Malformed response/usage/model identity, endpoint drift, stale authority, lost acknowledgments and stop must fail closed.

## Task 1: Trusted local routing and admission binding

Files: app/development_agent/config.py, execution.py, dispatch.py; app/models/development_dispatch.py; tests/test_local_development.py; scripts/backend_ci_shards.json.
- [ ] RED: explicit local settings and native loopback adapter produce a separate one-attempt router without constructing a client; disabled/hosted/remote/unsupported capabilities remain unavailable.
- [ ] RED: local_endpoint validates loopback/no credentials and omits None from historical serialized limits. Endpoint mismatch prevents reservation/provider calls; native policy source covers the full Ollama/router/retry/HTTP boundary.
- [ ] Implement minimal routing guards, clone exact selected local model without changing planning router, remove tool capability. Check current endpoint and activation on every executor boundary.
- [ ] GREEN: real router/adapter wire tests plus existing hosted quarantine controls.

## Task 2: Real approved workflow through fixture Ollama HTTP

- [ ] Use the existing disposable development workspace and independent operator admission. Supply exact context/criteria through the real provider request, persist a proposal and require exact independent source approval before mutation.
- [ ] Exercise replay and source handoff; verify actual source bytes and no repeated model call. Retain validation_passed/publication_ready false because isolated candidate execution remains unavailable.
- [ ] Test revoked/disabled/endpoint-changed behavior before effects; run focused and required regression/static/frontend gates and independent review before publication.

This is independent of codex/final-readiness-20261027 at the same starting main d8c9057. Do not edit or merge that worktree. Shared initial full-backend baseline is still running there; retain exact results separately and run all affected development tests on this branch.
