# Groq bootstrap and independent rate limits

The one-request acceptance flow is implemented and tested with intercepted HTTP.
No live inference is approved or performed. Saving/preparing/approving this test
does not enable ongoing inference, workers, tools, remote access or startup.

The exact native plan binds the disabled profile revision, credential fingerprint,
Personal Free account/model evidence, complete application source seal, fixed body,
one request and zero paid ceiling. Approval grants only models.accept_once for that
persisted plan. A durable claim precedes HTTP; duplicate/concurrent execution and
unknown outcomes cannot replay it. No quota remaining/reset values are invented.

Successful response headers initialize provider RPD/TPM observations. Native
rolling windows independently enforce RPM, RPD, TPM and TPD; aliases share the
account/model ledger. Pending reservations, stale responses, missing headers,
uncertain outcomes, 429 deadlines and reset observations fail conservatively.
Normal Groq activation additionally requires the existing nonbillable allowance
and explicit cloud activation; acceptance alone grants neither.

Validation on 2026-11-02:

- Complete collection: 4081 unique tests; 4077 passed, 4 skipped, 0 failures/errors.
- Eight isolated temporary-database batches, 300-second per-test timeout, no retry.
- Exact coverage and unchanged source verified; frozen source digest:
  56ecc522fd7aa0803b6c750d5886e566fdebb9a4c04030d5060dc23674d45750.
- Evidence retained locally in `.local/full-groq-20261102` (not committed).
- Backend Ruff and migration upgrade/downgrade/re-upgrade passed.
- Frontend typecheck, ESLint, 253 Vitest tests and production build passed.
- Focused security review completed; original baseline 4045-test suite also passed
  separately at 8586920. This evidence does not cover later workflow changes.

The four skips require explicit local-model evaluation, a live Ollama tool model,
Windows file-symlink privilege, or an operator-enrolled Hyper-V execution VM.
Real junction tests run. Do not silently enable models or OS features to remove skips.

Browser QA of the isolated synthetic acceptance page remains pending: automatic
approval review rejected opening it because the approval service hit a usage limit.
The browser action was not executed; no alternate UI automation was attempted.
The isolated fixture contains no real credentials and intercepts every model request.

The operator's saved personal Groq profile remains disabled in its original runtime.
Fresh account/model evidence is needed before preparing an expiring real plan, then
the owner must separately approve that exact request. Do not renew observed evidence
timestamps without new observations. No ongoing activation, permanent startup, push
or merge is authorized. Full live lifecycle proof and local/cloud/hybrid workflow
integration remain outstanding; do not describe intercepted tests as live inference.
