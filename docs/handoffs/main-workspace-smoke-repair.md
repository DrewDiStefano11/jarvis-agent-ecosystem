# Main workspace smoke integration repair

Base: `f130e47e61000120d10e62adcf286d12b8140bdb`.

Production team selection adds a capability inference request before workspace
planning. The smoke provider now returns the capability schema for that request
and verifies exactly one capability request and one workspace-plan request across
API restart. The evidence directory is resolved to its canonical Windows path so
the filesystem safety checks see the same path as the file handle.

Validation on 2026-10-02: backend Ruff format/lint pass; complete backend suite
1,290 passed, 2 skipped; frontend typecheck/lint pass, 101 tests pass, build passes;
blank upgrade to `20260906_10`, downgrade to `20260729_04`, re-upgrade pass.
Twelve autonomy fixture scenarios and model evaluation/qualification fixtures pass.
Office/browser, workforce and repaired workspace execution/restart smokes pass.
Windows filesystem tests require unsandboxed execution. Fixture evidence is not
installed-model evidence. No reachable Ollama service was available.

The initial repair changed smoke evidence. The CI compatibility repair below
also constrains the supported dependency line and adds regression coverage.
Do not merge automatically.

## Exact-head CI repair

The original repair head was `e43cb3ab0e04613152fc868e470a1b5b9652ef1a`.
Its Windows Actions backend failed with 3 failures, 1288 passes and 1 skip;
frontend, runtime-browser and repository-integrity passed. Local validation used
SQLAlchemy 2.0.51, while a fresh CI install selected 2.1.2 and Alembic 1.20.0.

SQLAlchemy 2.1 changed database-name URL percent decoding/rendering. An unchanged
`sqlite:///./data/jarvis%20.db` began pointing to `jarvis .db`, and a rendered
Windows drive became `C%3A`, exposing the Office test's missing ConfigParser
percent escaping. Both URL failures reproduced in an isolated CI-version
Python 3.12 environment. The repair bounds SQLAlchemy to its supported 2.0 line
instead of silently changing existing database filenames or introducing a URL
migration in this smoke PR. Alembic's ConfigParser boundary doubles percent signs
once; interpolation returns the original URL once. SQLite `file:` URI decoding
remains distinct and unchanged. A future SQLAlchemy 2.1 adoption needs an explicit
configuration/URL compatibility migration and its own validation.

The historical-execution failure has a separate cause. TestClient starts the
real lifespan lease-recovery loop on another thread. After the test expires a
lease, that consumer can reconcile it before the test's manual recovery call,
which then correctly returns zero. Runtime recovery is not suppressed and no
fingerprint/dispatch logic needed removal. The test now cancels and awaits that
background task to own recovery, retaining the original count-one assertion.
It also explicitly exercises the opposite winner order on the lifespan portal:
one recovery returns one, the next returns zero, exactly one expiry event exists,
then the second runtime attempt uses the historical review checkpoint and makes
exactly one additional inference. Terminal replay makes no extra call. Both
crash boundaries and both owner orders pass in eight independent repeats.

Regression coverage includes application/engine/Alembic downgrade/re-upgrade
roundtrips for actual Windows paths with literal `%20`, `%3A` and `%25` filenames,
and verifies there is one correctly named database. Existing plain-path/query,
SQLite file-URI, in-memory refusal and filesystem safety tests are retained.
There is no runtime, coordinator, permission, schema or Office redesign. PR #63
is unchanged and will need to reconcile this new repair head afterward.

CI repair validation on 2026-10-02: Python 3.12, SQLAlchemy 2.0.54 and Alembic
1.20.0; dependency consistency, Ruff format/lint and full backend suite pass
(1,295 passed, 2 skipped). Affected modules pass (212 tests), and all four
recovery owner/boundary cases pass in eight independent repeats. Frontend
typecheck/lint, 101 tests and build pass. Blank upgrade, downgrade to
20260729_04 and re-upgrade to 20260906_10 pass. Twelve autonomy acceptance
fixtures, model evaluation/qualification fixtures, workspace execution/restart,
workforce and local planning/Office/browser smokes pass. Model calls in this
evidence are deterministic fixtures, not installed-model validation.

Final validation results and exact-head Actions/review gates are recorded in
PR #67's description. No merge is authorized by this task.
