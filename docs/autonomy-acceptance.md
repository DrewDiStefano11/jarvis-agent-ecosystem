# Autonomy acceptance, observability, and local-model evaluation

## What "autonomy acceptance" means

Autonomy acceptance is the objective, repeatable answer to one question:

> Can Jarvis take a high-level objective, choose an appropriate team,
> decompose the work, execute specialists, recover from failures, synthesize
> results, and complete the objective reliably?

This document describes the reusable framework that validates that loop. It
does **not** claim full Jarvis autonomy. The completion claim is limited to:

> A reusable autonomy acceptance, observability, and local-model evaluation
> framework that is ready to validate the production decomposition/coordinator
> implementations once PR #62 and PR #63 are merged.

## Production vs. fixture-backed functionality

Each autonomy stage declares explicit provenance
(`app.autonomy.ports.STAGE_PROVENANCE`), recorded in every evidence file:

| Stage | Implementation | Notes |
|---|---|---|
| `ground_context` | production | Real `ContextAssembler` over explicit bounded sources. |
| `select_team` | fixture | Deterministic greedy cover over the production capability taxonomy; capability inference is fixture-supplied. |
| `decompose` | fixture | Explicit scripted work graphs (stand-in for PR #62). |
| `assign` | production | Deterministic capability-satisfaction assignment. |
| `execute_specialist` | fixture content / production ledger | Specialist output text is scripted; attempts, checkpoints, recovery, and lineage flow through the real `AgentRuntimeService`. |
| `evaluate_output` | production | Strict JSON/schema validation owned by the harness. |
| `recover_retry` | production | Real recovery plans, bounded attempts, checkpoint lineage. |
| `unlock_dependencies` | production | Deterministic readiness over the validated work graph. |
| `synthesize` | fixture | Deterministic join over completed outputs (stand-in for PR #63). |
| `complete` | production | Deterministic terminal-state mapping. |

Fixtures are deterministic, explicitly labeled (`is_fixture = True` plus
fixture provenance in evidence), and never described as real inference,
planning, or orchestration.

## Running fixture acceptance

```bash
# All twelve scenarios; evidence lands in ignored .local/ (never committed).
python scripts/autonomy-acceptance.py acceptance

# A subset:
python scripts/autonomy-acceptance.py acceptance --scenarios golden_path retry_succeeds
```

Outputs (default `--out .local/autonomy-acceptance`):

- `autonomy-acceptance.json` — aggregate machine evidence.
- `autonomy-acceptance-summary.md` — human summary.
- `<scenario>.json` / `<scenario>.md` — per-scenario evidence.

Exit code is `0` only when every scenario verdict is `pass`.

The same scenarios run as unit tests (no script needed):

```bash
cd apps/api
python -m pytest tests/test_autonomy_harness.py tests/test_autonomy_recovery.py -q
```

## The twelve scenarios

1. `golden_path` — team selected, bounded graph, dependencies respected,
   specialists complete, synthesis consumes all results, terminal success.
2. `missing_capability` — unknown capability blocks safely with
   `blocked_missing_capability`; nothing invented, nothing granted, no runs.
3. `specialist_failure` — one failure with bound 1: dependents never start,
   independent completed work stays durably succeeded.
4. `retry_succeeds` — attempt 1 fails, attempt 2 succeeds; exact lineage,
   one durable terminal result, correct synthesis input.
5. `retry_exhaustion` — three bounded attempts then terminal `FAILED`; no
   fourth attempt, prior success intact, no synthesis without inputs.
6. `cancellation` — operator cancel mid-flow: nothing new starts afterwards,
   terminal states cannot later become success.
7. `emergency_stop` — stop before a durable boundary: no further execution,
   committed state inspectable, event counts frozen.
8. `authorization_revoked` (durable DB) — mid-flow `deny` on
   `runtime.execute` fails closed; prior authorized results intact; no
   self-granted permission (audited).
9. `restart_recovery` (durable DB) — terminate after a checkpoint, rebuild
   all in-memory state, resume from durable truth: no repeated work,
   consistent command/checkpoint ids, no duplicate terminal results.
10. `invalid_model_output` — malformed output rejected (`output_not_json`),
    one bounded repair, malformed text never becomes authoritative state
    (only `sha256:` digests and validated summaries persist).
11. `untrusted_context` — operator instructions stay distinguishable;
    injected untrusted content is excluded with findings recorded and cannot
    alter authorization decisions (decision inputs are logged and canary-free).
12. `dependency_correctness` — diamond `A -> B/C -> D`: `D` starts only after
    both branches complete, durably ordered.

## Running installed-local-model evaluation

```bash
# Fixture positive control (CI-safe; asserts the scoring itself).
python scripts/autonomy-acceptance.py evaluate

# Real installed-local-model evaluation (explicit opt-in only).
JARVIS_MODEL_EXECUTION_MODE=local_only JARVIS_MODEL_OLLAMA_ENABLED=true \
  python scripts/autonomy-acceptance.py evaluate-local --model <installed-model>
```

Preflight fails clearly unless execution mode is `local_only`, a healthy
loopback provider exists, and the requested model reports available. The
harness never downloads models, never starts model services, never allows
remote providers, and never adds cloud dependencies.

The same evaluation runs as unit tests; the real-model test skips unless
`JARVIS_EVAL_LOCAL_MODEL` is set:

```bash
cd apps/api
python -m pytest tests/test_model_evaluation.py -q
```

## How results are scored

### Acceptance

A scenario passes when every declared check passes **and** the terminal
state/reason match the expected values. Checks compare explicit expected vs.
actual conditions (terminal states, event ordering, durable ledger contents,
command ids, checkpoint digests). There are no subjective scores.

### Model evaluation

Cases pin an AI Hub role, exact prompts, an optional output schema, and
deterministic expectations (`app.model_evaluation.expectations`). Metric
formulas are exact and documented in `app.model_evaluation.runner`:

- `case_pass_rate` = passed / total (all expectations, all repetitions).
- `structured_output_success_rate`, `schema_validity_rate`,
  `instruction_following_rate`, `capability_classification_accuracy`
  (exact normalized set match).
- `decomposition_quality` = mean graph-subcheck score (schema, node bound,
  depth bound, closure, acyclicity, coverage — 6 subchecks).
- `synthesis_completeness` = mean covered-id recall.
- `consistency_rate` (repetition-identical cases), `hallucination_rate`
  (id-like tokens outside the known set), `malformed_response_rate`,
  `repair_frequency` / `repair_success_rate`.
- `bounded_instruction_rate`, `trust_boundary_rate`, `secret_pass_rate`.
- `context_sensitivity` per small/large pair: `small_pass - large_pass`.
- Failure-code histogram, latency mean/p95/max, token totals where reported.

Every case ships a known-good `reference_output` (positive control) and
known-bad `adversarial_outputs` (negative controls); CI asserts all
references pass and every adversarial fails.

## Observability

Each run records a bounded, secret-scrubbed timeline
(`app.autonomy.events`): stage, event, node, attempt, dependency readiness,
timestamps, retry/failure reasons, synthesis readiness, outcomes, checkpoint
ids, recovery occurrences, and authorization-relevant transitions. Evidence
carries repo SHA, scenario, inference identity, counts, all relevant ids,
expected-vs-actual checks, the timeline, and the final result. Free text is
scrubbed with the production redaction helpers; raw provider exception
internals are mapped to failure codes and never persisted.

## Runaway protection

`app.autonomy.bounds.HarnessBounds` caps tasks, dependency depth, attempts
per node, repairs, model calls, output size, timeline events, and wall-clock
deadline. Violations end the run deterministically (`blocked/bounds_exceeded`
or `failed/...`). No infinite loops, no unbounded sleeps, no retries beyond
explicit bounds.

## Safety boundaries

- No new permissions are granted automatically; no `runtime.admin`; no
  elevated agents; no shell/code execution; no remote fallback; no public
  exposure; loopback restrictions unchanged.
- Generated model/fixture text is data, never authorization.
- Task leases, emergency stop, review gates, and tool authorization are
  bypassed nowhere; the harness adds its own operator gate checked before
  every durable execution boundary.
- Checkpoints persist only validated summaries and `sha256:` digests —
  never raw unvalidated output.
- Schemas were not loosened; no tests were disabled; no regression coverage
  was removed.

## Replacing fixtures after PR #62 / #63 merge

1. Implement `DecompositionPort` with the PR #62 `DecompositionService`
   (scripted graphs drop out; graph validation stays).
2. Implement `SynthesisPort` (and recovery/claim surfaces) with the PR #63
   coordinator (deterministic join drops out; completeness assertions stay).
3. Swap capability inference from fixture-supplied lists to the qualified
   local model; keep the greedy-cover parity tests.
4. Re-run `acceptance` unchanged: identical scenarios, identical evidence
   shape, with provenance flipping from `fixture` to `production`.

No harness, scenario, or evidence rewrite is needed; ports are the only seam.

## Known limitations

- Team selection and synthesis are deterministic fixtures, not model-backed.
- Specialist output content is scripted; only the execution ledger is real.
- Ready nodes execute sequentially in `node_id` order; the `ready_set`
  timeline event exposes the parallelizable surface without concurrent
  execution.
- No HTTP routes, UI, migrations, or durable schema changes were added.
- Local-model quality results depend on installed models and are not part
  of CI assertions (CI asserts the fixture controls only).
