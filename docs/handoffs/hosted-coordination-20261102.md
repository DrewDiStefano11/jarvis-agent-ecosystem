# Hosted coordination and Ollama preservation

Parent: `8a8c90d9ccbc9f7a582e58adb3e9d107fb698106`.

Local-only Ollama routing remains supported. Team selection, decomposition,
specialists, synthesis and independent review share validated local/cloud/hybrid
routing requirements. Explicit provider/model selection and local preference are
preserved; no automatic cross-provider fallback or startup activation was added.
Local development uses a local-only cloned router even with a mixed registry.

Cloud-capable planning records a typed native dispatch intent in the audit/outbox
before inference. Concurrent or interrupted copies cannot replay that intent.
Current task inputs, actor, stop, leases and activation are rechecked after awaited
selection. Native workflow checkpoints and authority remain authoritative.

Groq account-only allowance evidence may omit the complete legacy quota-window
trio. Partial windows remain invalid; other integrations still require complete
windows. This grants no capacity: native one-request acceptance and response-header
tracking are still required. Decomposition retains its local output default and
adds an explicitly configurable smaller completion reservation.

## Validation status

The first exact 4129-test run completed with 4123 passes, 4 skips and 2 failures.
One failure exposed a local development clone accepting a later mode mutation;
its validator now requires `local_only`. The other was a crash-recovery fixture
racing the API's background recovery task; that manual-recovery test now disables
background recovery using the existing fixture option. No assertion or timeout
was removed. Focused verification passed all 72 tests in the three affected files.

A fresh exact full run was attempted under `.local/full-hosted-fixed-20261102`,
with frozen source digest
`5a44eae34c2630adfffa1ed0fbb134137255e85141bb91779eda18ad2dea6abd`.
All eight batches ended with inactivity timeout code 124 before complete JUnit
results existed; the ad hoc aggregate then failed on missing JUnit. This run is
incomplete, not passing. Its process log reports roughly 11130 seconds elapsed.
Do not infer application correctness or a specific hang cause from this outcome.
Rerun the committed CI shard commands on the next machine rather than copying
temporary databases or silently raising test timeouts.
Ruff, migration roundtrip, frontend typecheck, ESLint, all 253 Vitest tests and
production build passed. Intercepted regressions cover both execution paths.

Late result-commit fencing, local HTTP/WebSocket browser boundaries and bounded
worker concurrency are being validated in the descendant hardening worktree.
This intermediate source does not claim those descendant fixes or live proof.

No live model request was made. The original saved Groq profile remains disabled.
Fresh private account evidence and separate exact-plan approval remain required.
No push, merge, permanent startup or generated-code host execution is authorized.
