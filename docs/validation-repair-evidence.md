# Validation-driven repair evidence

This milestone explicitly depends on the trusted validation evidence PR and uses
the existing self-improvement EvidenceSource protocol. It introduces no source
mutation path, command execution, provider call, integration, route or migration.
It adds an additive `validation_execution` source kind and preserves CI import
behavior while requiring validation source HEAD to match the requested baseline.

Authenticated measurements are assessed against the original exact plan before
any diagnosis projection. Every current gate has a disposition. Missing, stale,
interrupted, cancelled and unmeasured outcomes remain unavailable measurements,
never passing tests or invented product defects. Source-state changes require
revalidation, including changes with unchanged HEAD. Expired receipts remain
available as historical reproduction references and cannot authorize a retry.

Reproduction references retain repository, HEAD, base, complete code-state hash,
execution/validation plan hashes, suite and policy identities, task/run/attempt/
command references, authenticated receipt digest and captured output digests.
No logs, environment values, source text or generated explanation is imported.
The deterministic classifier retains explicit measured failure categories without
claiming to know a root cause. Permanent test/product/lint/migration failures need
diagnosis. Only recorded transient infrastructure/dependency/watchdog codes can
request another validation attempt, with at most two retries. Replayed receipts
are rejected before counting; prior failures consume the bounded budget. Tied
conflicting results are conservative and retain supporting receipt digests.

Recommendations are advisory requests. Both execution and source-mutation authority
are literal false in the output contract. Existing runtime leases, exact-plan
operator approval and durable journal remain necessary for any later execution;
source editing must use the separately approved mutation subsystem. No report,
model assertion or retry decision grants either authority.

The existing improvement engine receives measured passed/failed gate observations
and unavailable unknown/stale observations. It can identify weaknesses while
retaining provenance. The adapter explicitly reports incomplete provenance because
actual tool, dependency, environment and isolation configuration is not measured
by this milestone. It does not invent a configuration digest or elevate proposals
to autonomous repair readiness.

Confirmed transient infrastructure, CI environment, dependency and watchdog failures project
to reliability observations and retry-policy diagnoses, preserving the bounded
retry decision without generating product handler/test-coverage repair proposals.
Classification follows the bounded-retry recommendation, requiring the recorded
qualifying transient diagnostic code. A transient-looking kind without such
evidence retains failure diagnosis.
Repair disposition selects the highest authenticated sequence within each run and
attempt before timestamp comparison, consistent with validation assessment even
when the collector clock regresses. Historical failures still count toward the
retry budget; sequence selection does not erase prior attempts.

Use isolated fixture receipts/databases for tests. Hosted independent review is
unavailable; document author-performed local review and applicable repository gates
before publication. Runtime untrusted-source execution remains disabled.


Retry recommendations count the supplied authenticated execution history and are
bounded advisory requests. They do not assert that a caller supplied every prior
attempt. Any future dispatcher must use the complete durable journal and existing
runtime attempt budgets before allocating a retry. This adapter cannot grant or
reset that authority. Collector key rotation does not create a new measurement
artifact for improvement-frequency counting.


## Local validation and review

Repair/improvement regressions: 86 passed. Backend Ruff and frontend typecheck,
ESLint, Vitest (214 tests) and build passed. The full backend dependency baseline
passed 2,350 tests with 2 skips; focused tests cover this delta without repeating
that unchanged full baseline. CI collection covers the added file exactly.
Author-performed local review checked receipt authentication, source-state/age
invalidation, conservative tied failures, retry counts, canonical report hashing,
collector-rotation deduplication, preserved CI error behavior and incomplete
provenance. This is not hosted independent review. Exact final-head CI is required.

Repair construction and `ValidationExecutionSource` require the separately trusted
host/operator-approved execution-plan hash. Receipts, model text and candidate
plan fields cannot supply that approval reference. Narrowing browser requirements,
test mappings or validation boundaries requires a new independent approval.
The source provenance pairs `jarvis-development-validation-v1` with the actual
development-validation policy digest; collector command policy fingerprints remain
in each authenticated measurement rather than naming a different policy digest.

Simultaneous latest lineages select outcomes conservatively and break remaining
ties using measurement content, independent of collector key IDs or signatures.
Key rotation may change authenticated envelope references but cannot change the
selected measured failure or exported facts under an unchanged measurement digest.

Authenticated passing commands cannot override blocked repository paths. Repair
construction rejects policy-blocked assessments before exporting gate findings or
improvement observations. Source provenance hashes the same revalidated canonical
current code state used by repair assessment; reordering a model-copy change tuple
cannot turn the same measured artifact into multiple evidence sources.

Source receipt identities hash canonical authenticated measurements returned by
the verifier, including when equivalent raw model-copy values require revalidation.
Retry consumption counts latest failed run/attempt/gate lineages; multiple sequence
updates for one attempt do not spend multiple retry slots. Distinct failed attempts
still exhaust the operator-bounded budget.

Each finding binds the canonical latest receipt from every contributing attempt
lineage in its supporting evidence, including older attempts that consume retries.
Latest-result diagnosis remains timestamp/sequence based, while changing prior
attempt evidence changes the report digest even if the latest failure is identical.
