# Trusted validation evidence

The deterministic planner is a policy engine. Its legacy `ValidationEvidence`
objects are internal projections, not authenticated proof. Use
`development_validation.evidence.assess_receipts` at any authority boundary.
There is no HTTP importer, collector key, signing endpoint, command runner or
new autonomous capability in this milestone.

Receipt assessment requires `expected_execution_plan_hash` from a separately
trusted host/operator-approved reference. Obtain it outside candidate receipts,
model text and submitted plan fields; echoing a candidate hash is not approval.
It binds the original boundary, browser requirement and affected-test mapping.
`verify_plan` reconstructs packaged policy for structural inspection only; it
does not approve embedded derivation inputs. No default approval reference or
HTTP endpoint that lets a caller choose one is provided. Durable journal callers
must retrieve the original append-only operator approval and match its exact plan.

`build_execution_plan` binds the exact repository, HEAD, base, content/mode
changes, deterministic validation plan, gate targets, suite identity, adapter
contract identity and packaged budget policy into an immutable hash. Rehashing
relaxed budgets cannot satisfy `verify_plan`. The caller must obtain `CodeState`
from the existing trusted Git observer, including staged and relevant untracked
changes; a model-provided CodeState is not authoritative.

Receipts use domain-specific HMAC-SHA256 authentication over the entire envelope,
including collector/key identity and measured execution lineage. An operator must
provision collector keys outside model/provider inputs and candidate workspaces.
The trusted collector must independently measure execution and observe source
state, and must never sign assertions from the model or candidate process. No
production collector is implemented or configured yet. Authentication proves
collector provenance, not that a collector is correct or securely sandboxed.
Revoking a configured key rejects its receipts. Receipt JSON alone grants nothing.

Measurements retain task, runtime run, attempt, command run and sequence, exact
plan/code/command references, aware timestamps, bounded elapsed/resource fields,
exit classification, output digests and output truncation status. Unavailable
CPU/memory measurements remain null. Diagnostics contain only enumerated codes;
raw output, paths, environment variables and generated explanations do not enter
this contract. Output digests cover collector-captured output; retained output is
bounded by the immutable command policy. Command adapters are identities only,
not advertised executable capabilities.

Collector sequence conflicts and latest-result selection are scoped to the
runtime run, attempt and gate. Independent gates may each start at sequence one;
command-run identities remain unique across gates within a run and attempt.
Durable journals additionally require their own globally ordered command stream;
that stricter journal reducer constraint does not apply to standalone collectors.

Assessment verifies the packaged policy and receipt authentication, rejects
replays/conflicting identities, future timestamps, incoherent exits and mismatched
plans, and treats expired, interrupted, cancelled and unmeasured results as
nonpassing. Publication requires every exact full-suite gate. Even iteration
receipts must match the complete exact execution plan; this bridge intentionally
requires revalidation after any source-state change. Existing planner-only
component-reuse behavior remains compatible for existing internal callers.

No migration, runtime route or CI manifest is changed. Durable journal ownership,
leases, approved plans, checkpoints and transactional audit/outbox records belong
to the next milestone. Executing tests can run arbitrary repository code. Process
limits, allowlists and a separate worktree do not provide a sandbox. Autonomous
execution remains unavailable until an actual isolation boundary is implemented
and verified. Operator-approved local developer test gates are separate from
Jarvis runtime execution authority.

## Local review

Hosted independent review is unavailable for this campaign. Local review checks
canonical authentication coverage, policy reconstruction rather than rehash-only
validation, model_copy revalidation, replay rejection, exact-state invalidation,
clock freshness, explicit unknown measurements and no reachable execution path.
This is author-performed local review, not hosted independent review.


## Validation record

Local full backend baseline: 2,350 passed, 2 skipped in 1,854.85 seconds.
The measured full-suite duration informed a bounded one-hour backend-test budget;
the final policy calibration is covered by the focused evidence/planner suite.
Other gates retain a 15-minute bound. This is policy calibration, not execution
permission or proof of confinement. Ruff format/lint, all frontend gates (214
Vitest tests), migration round trip and exact CI collection coverage passed.
Subprocess imports were explicitly bound to this isolated checkout via PYTHONPATH.
Exact final-head CI remains required after PR publication.

Final follow-up validation: 243 focused regressions passed with no-cache Ruff.

Measurement bounds retain signed POSIX and unsigned 32-bit Windows termination
status. Failed executions may record budget overrun durations and large measured
peaks; successful exits still must meet immutable command budgets.
An explicitly classified failed observation may retain exit zero when its measured
timeout or memory bound was exceeded; a bounded zero exit cannot claim failure.
Repository identity is canonical and bounded. See [GetExitCodeProcess](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getexitcodeprocess)
and [DWORD data type](https://learn.microsoft.com/en-us/windows/win32/winprog/windows-data-types).
