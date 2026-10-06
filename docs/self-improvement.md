# Self-improvement foundation

The implemented loop is **evidence → baseline → weaknesses → hypotheses
(optional advisory contract) → proposals → experiment plans → comparison**.
This milestone does not allow Jarvis to autonomously rewrite or deploy itself.
There is no executor, model invocation, source editing, routing change, permission
change, model installation, shell execution, remote provider, or approval API.

An additional [durable improvement backlog](improvement-backlog.md) admits one
persisted opportunity as a native queued task through explicit local identity/RBAC.
It retains frozen evidence and experiment references, suppresses duplicate active
work and supports durable replay. Selection does not approve or execute a change.

## Evidence and baseline

`app.models.self_improvement` defines bounded frozen contracts. The independent
`app.self_improvement` package uses the #64 acceptance/model-evaluation artifacts,
#66 qualification profiles, #65 Runtime Doctor JSON, and existing durable task,
attempt, runtime-run, model-execution and tool-execution rows. It reads these
systems rather than rewriting them. Artifacts remain external records referenced
by a stable operator alias and SHA-256 digest; only numeric measurements,
identifiers, timestamps, stage/role/model/provider identities and provenance are
persisted. Raw responses, final-result text, prompt/context content, doctor
remediation, credentials and provider exception text are discarded. Existing
secret scrubbers protect even identifiers and advisory text.

Use the same source aliases for baseline and candidate runs. Keep the referenced
artifacts in controlled storage: digests detect replacement but cannot recover
a deleted external artifact. Durable row references identify historical records;
mutable runtime row projections retain the bounded observed scalar values and
snapshot digest. Runtime collection requires an explicit UTC-aware time window
and rejects overflow rather than silently sampling successful records. Empty
history is unmeasured, not zero performance.

Baselines are content-addressed and include repository identity, operator-supplied
configuration/safety fingerprints, evaluator version, source digests, suite/policy
versions and digests, evaluation case identities, inference mode and per-metric
measurements. Fingerprints must cover the complete relevant configuration; the
safety fingerprint must include permissions, provider/routing restrictions,
emergency-stop semantics, retry/call/resource limits and validator controls.
These are operator provenance assertions, not measurements inferred from model
confidence. No environment or configuration values are persisted.

Available dimensions include scenario/check outcomes, task completion, retries,
retry outcome, model-call counts, malformed/schema/instruction/hallucination
rates, classifier/decomposition/synthesis metrics, qualification role gates,
provider/validation/tool failures, runtime outcomes/recovery and latency. Rates
from #64/#66 retain their existing formulas; runtime and acceptance counts remain
per-record measurements. No unsupported denominator or overall score is invented.
Absent dimensions are explicitly listed as missing; per-source null metrics
remain unmeasured. Reviewer evidence is available through reviewer role gates,
not a fabricated generic reviewer score.

#64 acceptance and model-evaluation artifacts and #65 diagnostics do not record
complete historical suite/policy definitions. They support diagnosis but cannot
by themselves establish comparable experiments. Their proposals are
`needs_evidence`. This foundation deliberately does not retroactively stamp the
current suite onto old evidence. Native #66 profiles are comparable only when
all expected cases were evaluated and the recorded suite/policy/run definitions
are available. Fixture evidence always remains fixture evidence.

## Findings, hypotheses and plans

Detection is deterministic: measured values are tested against recorded
expectations, grouped by category/stage/role/model/provider/metric, and retain
exact observation IDs. Qualification policy gates are authoritative; raw role
metrics do not create a second stricter qualification policy. Categories are
reliability, model role, planning, execution, system/runtime and efficiency.
Latency and call counts have no invented failure threshold. Priority classes
use hard gates, frequency, affected subjects and evidence completeness; reasons
expose these factors. A recorded hard failure is critical even if other metrics
look good. One failure is distinguished from failures in multiple source/subjects.

The optional strict `ImprovementHypothesis` contract validates supporting and
contradicting IDs against the measured baseline/weakness. Confidence is low,
medium or high. Model explanations cannot change findings, metrics or criteria.
Local-model hypothesis generation is deferred; analysis makes no model calls.

Deterministic proposals describe a bounded candidate category and target, expected
measurement, risk, rollback and priority reasons. They never apply a change.
Where provenance is complete, the immutable experiment embeds every failing
primary metric and all measured baseline regression criteria, plus the exact
evaluation sources and required authorization, emergency-stop, no-remote-provider,
migration and test checks. Operators must refine/implement candidates in a later
sandbox workflow. Approval/status transition workflows are deferred; emitted
statuses are `ready_for_review` or `needs_evidence`, never approved by analysis.

## Comparison and anti-Goodhart protections

Comparison uses the stored proposal's original plan. The CLI accepts no replacement
criteria. It reports individual improved/regressed/unchanged/new/missing metrics.
An improvement requires every primary criterion, every regression criterion and
every required operator safety/test attestation. An aggregate score has no role.
Any measured regression blocks improvement; missing evidence is inconclusive.
Equal unmeasured dimensions stay unmeasured, not disappearing measurements.

The evaluator implementation digest also covers the existing metric formulas,
expectations and qualification scoring/policy modules. Changing their source
invalidates comparisons even without a version bump. Historical artifacts must
be normalized on the checkout that produced their measurements; a later checkout
cannot retroactively prove historical metric producer provenance.

Changed suite/schema/evaluator versions, suite digests, case identities/counts,
policy definitions/thresholds, inference mode, evaluation bounds, source sets,
metric definitions or safety fingerprints invalidate direct comparison. Candidate
model/provider identities must also remain unchanged, even for proposals whose
category permits future reassignment experiments. Explicit reassignment
compatibility is deferred; v1 never attributes a substituted model's results to
the original candidate change. Candidate
repository/configuration must match its operator attestation. Source repository
identities must match each baseline. Deleted tests, relaxed thresholds, suppressed
failures, inflated retry budgets, unsafe fallback and increased permissions must
never create a successful comparison. There is no compatibility override in v1.

Operator attestations are evidence references with a digest and tri-state results,
not independently rerun tests or model assertions. Unmeasured checks fail closed.
The operator must retain the actual referenced validation report. This milestone
does not itself prove that an external operator's provenance is truthful.

## Durable storage and API

Alembic revision `20261002_si` extends main's `20260906_10` with one independent
`self_improvement_records` table. Content-addressed analysis records contain
bounded normalized observations/findings/proposals/plans, not duplicate raw
runtime payloads. Comparisons preserve operator attestations. Explicit units of
work commit atomically; unique IDs make repeated/concurrent analysis converge.
The repository exposes no update/delete operation. Populated downgrade is refused
until the operator exports/removes this history; empty downgrade/re-upgrade is
supported. These advisory records are not published domain events and introduce
no new audit/outbox sequence stream or worker checkpoint semantics.

Read-only loopback API, with the standard typed response envelope/OpenAPI:

- `GET /api/self-improvement/baselines?limit=20` (maximum 100)
- `GET /api/self-improvement/baselines/{id}`
- `GET /api/self-improvement/baselines/{id}/comparisons`

The API cannot analyze, approve or apply changes. UI is deferred to minimize
conflicts with #63. The only main integration is router inclusion/current database
revision. HTTP contracts remain in `app.models`; SQL is confined to repositories
and the read-only runtime evidence adapter.

## Operator CLI

Use an already migrated database and the API Python environment. Input JSON files
are bounded at 2 MB, source count at 64, observations at 4096, findings/proposals
at 256, and total serialized experiment criteria at 65,536. Overflow fails
explicitly; repeated copies of the same artifact under aliases are rejected.
Runtime time windows are normalized to UTC. No untrusted input is echoed in errors.
Known cancellation, superseded-task and operator-control codes are observations
without failure expectations. Provider faults, model-output validation failures
and workflow faults retain separate attribution; unknown codes are workflow
failures, never inferred model-quality failures.
Worker availability/timeout codes are provider reliability evidence; budget
exhaustion is efficiency evidence within the existing limits. Local-only and
eligibility guards are control conditions, and bounded reviewer revision codes
are planning evidence. These mappings never propose weakening policy guards or
increasing the resource budget.
Tool grant/scope/path/plan denials, unsafe or unmarked workspace guards, explicit
resource limits and cancellation are also unscored control observations. They
retain their original failure-code references without creating handler-fix
proposals. Genuine tool execution faults retain failed tool-success measurements.

Each baseline seals its first complete advisory graph. An identical replay retains
the original capture timestamp. Changed hypotheses or proposals under that same
baseline ID fail explicitly; they never silently overwrite or discard content.
Generating later hypothesis revisions remains deferred.

```powershell
python scripts/jarvis_self_improve.py analyze --repo-sha <40-char-sha> `
  --configuration-fingerprint <sha256> --safety-fingerprint <sha256> `
  --evidence model_qualification:planner:profile.json --json
python scripts/jarvis_self_improve.py proposals --baseline <baseline-id> --json
python scripts/jarvis_self_improve.py compare <before-id> <after-id> `
  --proposal <stored-proposal-id> --attestation candidate-validation.json --json
```

`--database-url` is a global option before the subcommand, otherwise current
Jarvis Settings supply the URL. `analyze --runtime-start <ISO-time>` and
`--runtime-end <ISO-time>` include bounded durable history. Supported artifact
kinds: `autonomy_acceptance`, `model_evaluation`, `model_qualification`,
`runtime_doctor`. CLI fingerprints are digests, not raw configuration JSON.
`analyze` writes only analysis records. It never migrates or starts the runtime.

The candidate attestation file has the exact fields:

```json
{
  "candidate_repo_sha": "<40 lowercase hex characters>",
  "candidate_configuration_fingerprint": "<64 lowercase hex characters>",
  "evidence_digest": "<SHA-256 of retained operator validation report>",
  "authorization": true,
  "emergency_stop": true,
  "no_remote_provider": true,
  "migrations": true,
  "tests": true
}
```

Human CLI output labels measured facts, deterministic findings, model hypotheses,
proposed changes and the absence of approved changes separately.

## Integration and future loop

#63 can supply an `EvidenceSource.collect()` adapter returning a provenance
record and normalized observations for specialist execution, synthesis and
coordinator/replan evidence. It need not change this engine or its contracts;
this PR imports no #63 types. When both schema migrations are integrated, rebase
the later migration onto the earlier one to maintain a single Alembic head and
update the current database revision assertions. This PR is independently
migratable on today's exact main; do not merge two sibling heads unchanged.

The backend dependency stays on SQLAlchemy 2.0 (`>=2.0,<2.1`). SQLAlchemy 2.1
changes SQLite URL rendering and percent decoding, which breaks existing Windows
migration and literal-percent-path contracts. Supporting 2.1 requires separate
validation of those contracts.

Future, unimplemented evolution:

**observe → diagnose → propose → sandbox candidate → run validation → compare
baseline → human/policy approval → promote → monitor → rollback if needed**.

Sandbox execution, local-model hypotheses, approval transitions, promotion,
monitoring/rollback and autonomous source changes are not implemented here.
