# Independent result verification

Explicitly queued `planning_review` runs can freeze up to eight
`verification_criteria` in their autonomous execution specification. The runtime
specification is immutable; model output cannot add criteria, change policy,
grant tools, alter routing or authorize execution. Legacy requests omit the new
field and retain their serialized command hashes and structural review behavior.

Each criterion has a unique `id`, a bounded `description`, and one mode:

| Mode | Evidence and decision |
| --- | --- |
| `field_nonempty` | A named result field must contain a deliverable. Whitespace text is empty. |
| `field_contains` | An exact substring must occur in `summary` or `analysis`. |
| `artifact` | A frozen durable tool artifact ID, path and hash must match a completed, authorized execution associated with this task and its write scope. The stored content is hash validated by the existing artifact repository. |
| `test_evidence` | Returns `unverifiable`: main has no authoritative software command/test journal yet. Prose and text artifacts cannot prove tests or builds. |
| `semantic` | A distinct independent critic request evaluates the grounded objective, frozen criteria and persisted result content through the existing local-only model router. |

Example criterion policy for a planning result:

```json
[
  {"id":"recommendations","description":"Provide actionable recommendations","mode":"field_nonempty","field":"recommendations"},
  {"id":"objective","description":"Recommendations address the grounded objective","mode":"semantic"}
]
```

The verifier reloads the authoritative result, checks its task/run/attempt/context
and digest, then records deterministic checks and, when requested, semantic
checks. The reviewer uses separate role instructions, correlation identity and
JSON schema. It receives bounded data, not the worker prompt or a worker success
flag as acceptance policy. Its brief reasons are user-facing findings, never
hidden reasoning. The reviewer can cite only supplied evidence IDs. Missing,
duplicate or rewritten criteria, invented evidence, unsupported passes, oversized
or malformed responses are rejected. The application derives the aggregate
outcome, with precedence `failed`, `unverifiable`, `needs_correction`, `passed`.

Reviewer calls have a hard allowance of two per result (initial plus at most one
schema repair), each bounded by the queued execution timeout and the worker
timeout, capped at 2,048 output tokens. Router retry budgets allow one physical
request per dispatch and no cloud fallback. Context over 40,000 UTF-8 bytes
returns `unverifiable` rather than silently dropping evidence. These are additional
reviewer calls; worker request counts keep their existing meaning and bound.

Existing runtime checkpoints persist input binding, dispatch ownership,
validated responses and one authoritative verdict. Canonical structured records
use bounded string chunks to stay within the existing checkpoint/event metadata
limits. No migration, parallel ledger, raw model transcript or secret is stored.
Checkpoint commits use the existing actor authorization, lease fence, target
lifecycle checks, emergency stop and audit/outbox boundary. A dispatch committed
without a known response is not repeated: recovery under a new lease records
`unverifiable`. A concurrent dispatch under the same lease is refused. A validated
response committed before a crash is reused without another model call.

Only a passing verdict allows an opted-in task to finalize. Other outcomes pause
for operator review in this milestone; adaptive repair/replanning belongs to the
next milestone. Cancelled, stopped, revoked or stale-lease work cannot commit a
late verdict or verified success. Verification itself runs no workspace tools.

`GET /api/model-executions/{executionId}/verification` exposes the verdict using
the same runtime read authorization as the result. It returns `null` before a
verdict exists or for legacy requests. The record contains stable verification,
task/run/attempt/result IDs, criteria/result hashes, outcome, checks, evidence
references, reviewer role/provider/model, reviewer request count, UTC timestamp,
policy version and digest.

## Acceptance boundaries

The deterministic tests exercise the production worker, SQLite persistence,
leases, runtime commands, RBAC, checkpoint recovery and HTTP read endpoint. A real
authorized workspace report is created and checked for artifact provenance.
An actual loopback HTTP server drives the production Ollama adapter and model
router, but its inference response is a deterministic fixture. Tests also use
mock router responses to inject malformed output and safety races.

These prove execution and persistence of the verifier, not model judgment quality
on arbitrary tasks. Real model inference needs a configured running local model;
none is downloaded or provisioned by this milestone. Software test/build evidence
and coordinator node execution are not implemented on current main. This verifier
does not depend on unmerged PR #63 or #68. Their later integration must preserve
the same authoritative result binding and frozen criterion policy.
