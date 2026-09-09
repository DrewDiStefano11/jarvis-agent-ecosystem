# Local-model qualification and role profiles

> Bounded, evidence-backed qualification of **installed local models** for
> defined Jarvis roles. This milestone **recommends**; it does not assign
> production routing authority.

This document describes `app.model_qualification/` (API) and
`scripts/model_qualify.py` (CLI).

- [Purpose](#purpose)
- [Relationship to PR #64](#relationship-to-pr-64)
- [Conceptual pipeline](#conceptual-pipeline)
- [Role taxonomy](#role-taxonomy)
- [Qualification levels](#qualification-levels)
- [Qualification policy and hard gates](#qualification-policy-and-hard-gates)
- [Scoring](#scoring)
- [Role metrics](#role-metrics)
- [Ranking](#ranking)
- [Recommendation output](#recommendation-output)
- [Profile schema](#profile-schema)
- [Fixture mode](#fixture-mode)
- [Installed-local mode](#installed-local-mode)
- [Model and provider discovery](#model-and-provider-discovery)
- [Bounds](#bounds)
- [CLI usage](#cli-usage)
- [Security boundaries](#security-boundaries)
- [Persistence and migration impact](#persistence-and-migration-impact)
- [Operational signals](#operational-signals)
- [Limitations](#limitations)
- [Future production-routing integration](#future-production-routing-integration)

## Purpose

Answer, with reproducible evidence:

> Is this exact locally installed model qualified to perform this exact Jarvis
> role under the current qualification policy?

The system produces machine-readable profiles, ranked comparisons, and a
recommended role map. It never edits production routing.

## Relationship to PR #64

PR #64 merged the autonomy acceptance harness and the **local-model evaluation
framework** (`app.model_evaluation/`): deterministic cases, expectations,
fixture/installed-local providers, metric aggregation, and evidence reports.

This milestone **extends** that foundation instead of building a second
benchmark framework:

| Concern | Owner |
| --- | --- |
| Cases, prompts, expectations, schemas | `app.model_evaluation.cases` / `.expectations` (PR #64) |
| Providers (fixture / installed-local), preflight | `app.model_evaluation.providers` (PR #64) |
| Execution, repairs, per-case verdicts | `app.model_evaluation.runner` (PR #64) |
| Metric formulas | `app.model_evaluation.runner.aggregate_metrics_for_cases` (PR #64, exposed for subsets) |
| Roles, policy gates, scoring, levels, profiles, ranking | `app.model_qualification.*` (this milestone) |

Extensions made to PR #64 for this milestone (additive, backwards compatible):

- `aggregate_metrics_for_cases()` and `context_sensitivity_for_cases()` — the
  same published formulas applied to a case subset so one run can score each
  role without a second metric implementation.
- `ExpectDefectDetection` expectation (category `review`) and the
  `review-defects` case — defect detection plus false-positive avoidance for the
  reviewer role.
- `ScriptedFixtureProvider(model_name=...)` so fixture personas can be compared
  as distinct fixture models.
- `ProviderBase.list_models()` (implemented by the Ollama and
  OpenAI-compatible adapters over their existing model-list endpoints) so
  discovery uses the provider API instead of a new HTTP client.

The `fixture` vs `installed_local` distinction is preserved everywhere. Fixture
results never describe real model performance.

## Conceptual pipeline

```
installed local models
  -> bounded evaluation suite        (app.model_evaluation)
  -> role-specific metrics           (app.model_qualification.metrics)
  -> qualification policy + gates    (app.model_qualification.policy)
  -> qualified / conditional / unqualified / not_evaluated
  -> deterministic role ranking      (app.model_qualification.ranking)
  -> machine-readable role profile   (app.model_qualification.profile)
  -> future production-routing input (NOT applied by this milestone)
```

## Role taxonomy

Eight roles are supported (`app.model_qualification.roles`). Each names the
PR #64 evaluation roles whose cases produce its evidence, so no competing
vocabulary is introduced:

| Role | What matters | Evidence (PR #64 evaluation roles) |
| --- | --- | --- |
| `manager` | instruction following, long-context coherence, decision quality, synthesis, constraint retention, avoiding unsupported claims, structured output reliability | `manager_coordinator`, `synthesis`, `structured_json` |
| `planner` | objective interpretation, sequencing, bounded planning, dependency reasoning, completeness, schema compliance | `planning`, `structured_json` |
| `capability_classifier` | deterministic classification, low false-positive rate, capability vocabulary compliance, structured output reliability, context sensitivity | `capability_inference` |
| `decomposer` | DAG validity, dependency correctness, boundedness, capability assignment correctness, schema validity | `task_decomposition` |
| `specialist` | task accuracy, instruction following, context use, useful completion, low hallucination rate, trust boundary retention | `specialist_execution` |
| `reviewer` | defect detection, false-positive avoidance, evidence grounding, actionable criticism, identifying incomplete results | `review_critique` |
| `synthesizer` | integration of multiple results, conflict handling, completeness, provenance retention, avoiding invented conclusions | `synthesis` |
| `repair_retry` | understanding failure feedback, correcting the actual defect, avoiding regression, materially improved subsequent attempts | `correction_retry` |

`role_document()` emits the taxonomy (label, summary, evaluation roles, what
matters, contributing case ids) as JSON.

## Qualification levels

| Level | Meaning |
| --- | --- |
| `qualified` | Every mandatory gate passes **and** the role score reaches the role's qualified threshold. |
| `conditional` | No mandatory gate failed and the score clears the minimum, but an advisory gate failed **or** the score is below the qualified threshold. Usable with bounded scope and stronger review. |
| `unqualified` | A mandatory gate failed **or** the score is below the minimum quality threshold. |
| `not_evaluated` | Insufficient evidence: no scored cases, incomplete coverage, or a mandatory gate whose metric was never measured. |

`not_evaluated` is never a failed quality score. An offline provider, a missing
model, or a truncated run produces `not_evaluated` with a reason.

## Qualification policy and hard gates

The policy lives in `app.model_qualification.policy` and is versioned:
`QUALIFICATION_POLICY_VERSION = "1.0"`. Every profile records the version, and
`policy_document()` emits the complete policy as JSON.

**These thresholds are Jarvis qualification policy — explicit engineering
choices, not scientific truths.** They encode the minimum behaviour Jarvis
requires before a model may be *recommended* for a role. Changing a threshold
requires bumping the policy version.

Two gate kinds:

- **mandatory** — hard. Failing one means `unqualified` no matter how high the
  aggregate score is. A decomposer that emits invalid dependency graphs is never
  qualified because it writes excellent prose.
- **advisory** — meaningful weakness. Failing one caps the result at
  `conditional`.

Gate result semantics:

- `min`: passes when `observed >= threshold`.
- `max`: passes when `observed <= threshold`.
- unmeasured metric: status `not_evaluated`. A **mandatory** gate that was not
  measured fails closed to `not_evaluated`; an advisory gate is simply reported
  as not evaluated. Every mandatory gate is measurable with the default run
  configuration (one repetition, no repairs) — a test enforces this.

Mandatory gates by role (thresholds; see `policy_document()` for the full
descriptions):

| Role | Mandatory gates |
| --- | --- |
| manager | `case_pass_rate >= 0.70`, `schema_validity_rate >= 0.90`, `malformed_response_rate <= 0.20`, `instruction_following_rate >= 0.80` |
| planner | `case_pass_rate >= 0.70`, `schema_validity_rate >= 0.90`, `malformed_response_rate <= 0.20`, `instruction_following_rate >= 0.80` |
| capability_classifier | `case_pass_rate >= 0.70`, `schema_validity_rate >= 0.95`, `malformed_response_rate <= 0.10`, `capability_classification_accuracy >= 0.90`, `secret_pass_rate >= 1.00` |
| decomposer | `case_pass_rate >= 0.50`, `schema_validity_rate >= 0.90`, `malformed_response_rate <= 0.20`, `decomposition_quality >= 0.75` |
| specialist | `case_pass_rate >= 0.70`, `schema_validity_rate >= 0.90`, `malformed_response_rate <= 0.20`, `instruction_following_rate >= 0.80`, `trust_boundary_rate >= 0.90` |
| reviewer | `case_pass_rate >= 0.70`, `schema_validity_rate >= 0.95`, `malformed_response_rate <= 0.10`, `instruction_following_rate >= 0.80`, `review_defect_quality >= 0.75` |
| synthesizer | `case_pass_rate >= 0.70`, `schema_validity_rate >= 0.90`, `malformed_response_rate <= 0.20`, `synthesis_completeness >= 0.80`, `hallucination_rate <= 0.20` |
| repair_retry | `case_pass_rate >= 0.70`, `schema_validity_rate >= 0.90`, `malformed_response_rate <= 0.20`, `instruction_following_rate >= 0.80` |

Advisory gates (all roles unless noted): `consistency_rate >= 0.80`
(`0.85` for reviewer), `repair_frequency <= 0.34`, `repair_success_rate >= 0.50`;
plus `bounded_instruction_rate >= 0.80` (planner, decomposer, specialist),
`context_degradation_rate <= 0.50` (capability_classifier),
`synthesis_completeness >= 0.75` and `hallucination_rate <= 0.20` (manager).

Score thresholds per role (`minimum_score` / `qualified_score`):

| Role | minimum | qualified |
| --- | --- | --- |
| manager | 0.55 | 0.75 |
| planner | 0.55 | 0.75 |
| capability_classifier | 0.60 | 0.80 |
| decomposer | 0.55 | 0.75 |
| specialist | 0.55 | 0.75 |
| reviewer | 0.60 | 0.80 |
| synthesizer | 0.60 | 0.75 |
| repair_retry | 0.55 | 0.70 |

Shared advisory thresholds are uniform across roles (a test enforces that
`repair_frequency_max` and `repair_success_rate_min` never drift per role);
`consistency_rate_min` is uniform except the documented reviewer exception.

`secret_pass_rate` is mandatory for the classifier because a routing component
that emits secret-bearing output is never acceptable.

## Scoring

The role score is a **weighted mean of the metrics that were actually measured**:

```
score = sum(weight_metric * value_metric) / sum(weight_metric)   over measured metrics
```

Missing metrics are dropped and the remaining weights are renormalised, so a
role is never punished for a dimension its cases do not exercise. If no weighted
metric was measured the score is `None` and the role is `not_evaluated`.

Weights per role (documented in `policy_document()`), for example:

- manager: `case_pass_rate 0.30`, `instruction_following_rate 0.25`,
  `synthesis_completeness 0.20`, `schema_validity_rate 0.15`,
  `structured_output_success_rate 0.10`
- decomposer: `case_pass_rate 0.30`, `decomposition_quality 0.35`,
  `schema_validity_rate 0.15`, `bounded_instruction_rate 0.10`,
  `structured_output_success_rate 0.10`
- reviewer: `case_pass_rate 0.30`, `review_defect_quality 0.30`,
  `instruction_following_rate 0.20`, `schema_validity_rate 0.10`,
  `structured_output_success_rate 0.10`

Decision order (never a single average):

1. insufficient evidence → `not_evaluated`;
2. any mandatory gate failed → `unqualified`;
3. score below the role minimum → `unqualified`;
4. any advisory gate failed, or score below the qualified threshold →
   `conditional`;
5. otherwise → `qualified`.

Strengths and weaknesses are derived deterministically from gate outcomes:
strengths are gates cleared by at least `0.10` (plus reaching the qualified
score), weaknesses list failed mandatory gates first, then failed advisory
gates, then a below-target score.

## Role metrics

`role_metrics()` projects one evaluation run onto one role using PR #64's
published formulas, then adds derived metrics:

| Metric | Definition |
| --- | --- |
| `case_pass_rate`, `schema_validity_rate`, `malformed_response_rate`, `structured_output_success_rate`, `instruction_following_rate`, `capability_classification_accuracy`, `decomposition_quality`, `synthesis_completeness`, `hallucination_rate`, `trust_boundary_rate`, `secret_pass_rate`, `bounded_instruction_rate`, `consistency_rate`, `repair_frequency`, `repair_success_rate` | PR #64 formulas (see `app.model_evaluation.runner`) |
| `context_degradation_rate` | share of paired small/large context cases whose verdict degraded with the larger context |
| `review_defect_quality` | mean `ExpectDefectDetection` score = mean of defect recall and false-positive avoidance |
| `evaluated_case_count`, `expected_case_count`, `unavailable_case_count` | coverage counters |

Two integrity rules:

- **Unavailable is not bad quality.** A case whose every attempt failed at the
  call level (provider error, timeout, budget) is *unavailable*, excluded from
  rates, and reported separately.
- **Missing evidence fails closed.** Fewer scored cases than the role requires
  means `not_evaluated`, never a lower quality verdict.

## Ranking

`candidates_for_role()` orders models by, in order:

1. qualification level (`qualified` → `conditional` → `unqualified` →
   `not_evaluated`);
2. mandatory-gate result (passed → failed → unmeasured);
3. role score, descending (unmeasured last);
4. deterministic tie breaker: `model`, then `provider` (lexicographic).

A model that failed a mandatory gate can therefore never outrank a qualified
model because of a higher aggregate score. Ranking is independent of input
order and never randomised.

## Recommendation output

`build_recommendations()` picks, per role, the rank-1 candidate **only when it
is `qualified` or `conditional`**. Otherwise the recommendation is `null` with
the reason `no qualified or conditional candidate; best is <model> (<level>)`.

Example (fixture personas — **illustrative, not real model results**):

```json
{
  "capability_classifier": "fixture-model/qualified",
  "decomposer": "fixture-model/qualified",
  "manager": "fixture-model/qualified",
  "planner": "fixture-model/qualified",
  "repair_retry": "fixture-model/qualified",
  "reviewer": "fixture-model/qualified",
  "specialist": "fixture-model/qualified",
  "synthesizer": "fixture-model/qualified"
}
```

Every run carries the note:

> Recommendations are evidence only. This artifact never changes production
> routing, agent models, manager authority, permissions, or coordinator state.

## Profile schema

`app.model_qualification.profile` defines frozen, `extra="forbid"` Pydantic
models (schema version `PROFILE_SCHEMA_VERSION = "1.0"`). Conceptual shape
(actual field names follow the models in `profile.py`):

```json
{
  "schemaVersion": "1.0",
  "model": "qwen3:14b",
  "provider": "ollama",
  "inferenceMode": "installed_local",
  "status": "evaluated",
  "evaluatedAt": "2026-01-01T00:00:00Z",
  "qualificationPolicyVersion": "1.0",
  "evaluationSuiteVersion": "1.1",
  "evaluationSuiteDigest": "...",
  "repoSha": "...",
  "roles": {
    "manager": {
      "qualification": "conditional",
      "score": 0.81,
      "mandatoryGatesPassed": true,
      "gates": [
        {
          "key": "schema_validity_min",
          "metric": "schema_validity_rate",
          "direction": "min",
          "threshold": 0.9,
          "observed": 1.0,
          "passed": true,
          "mandatory": true,
          "status": "passed",
          "description": "..."
        }
      ],
      "strengths": ["..."],
      "weaknesses": ["..."],
      "reasons": ["..."],
      "evidence": {
        "cases": ["manager-basic", "synthesis-basic", "structured-strict"],
        "evaluated_cases": ["manager-basic", "synthesis-basic", "structured-strict"],
        "expected_cases": 3,
        "unavailable_cases": [],
        "failure_codes": { "ok": 3 },
        "metrics": { "...": "..." },
        "operational": { "...": "..." }
      }
    }
  },
  "warnings": ["..."],
  "notes": ["..."]
}
```

Profiles preserve:

- exact model identity, provider, inference mode;
- qualification-policy version and evaluation-suite version **and digest**;
- role, qualification, score, gate table, strengths, weaknesses, reasons;
- evidence references (case ids, failure codes, metrics);
- timestamp and run configuration/provenance.

No response bodies are stored and no hidden reasoning is recorded — only case
ids, rates, gate outcomes, and scrubbed free text.

Validation rejects: unknown inference modes, unknown statuses, unknown role
names or role keys, unknown qualification levels, and a fixture profile missing
its fixture label (or an installed-local profile carrying one).

## Fixture mode

`python scripts/model_qualify.py --fixture`

CI never needs an installed model. Fixture personas (`app.model_qualification.fixtures`)
replay scripted responses for the real case catalog, which exercises the
qualification logic itself: gates, thresholds, scoring, hard gates, rankings,
tie breaking, report generation, schema validation, invalid/incomplete/conflicting
evidence, policy versions.

Fixture output is always labelled:

- `inference_mode = fixture` in the CLI output and the Markdown summary;
- `"fixture evidence: deterministic scripted responses, NOT real model performance"`
  in `warnings` (a fixture profile missing it fails validation).

Fixture personas (`--persona`, repeatable):

| Persona | Expected result |
| --- | --- |
| `fixture-model/qualified` | qualified for every role |
| `fixture-model/conditional` | conditional for every role (consistency advisory gate fails) |
| `fixture-model/unqualified` | unqualified for every role |
| `fixture-model/malformed-json` | unqualified; malformed-response gate fails |
| `fixture-model/unavailable` | `not_evaluated` for every role, profile status `unavailable` |
| `fixture-model/weak-decomposer` | qualified everywhere except `decomposer` (hard gate) |
| `fixture-model/weak-reviewer` | qualified everywhere except `reviewer` (defect detection) |
| `fixture-model/weak-classifier` | qualified everywhere except `capability_classifier` |
| `fixture-model/weak-specialist` | qualified everywhere except `specialist` (trust boundary) |
| `fixture-model/repair-dependent` | conditional (needs one repair per case, then fixes it) |

A test asserts every persona produces exactly its documented verdicts.

The only non-deterministic field in fixture reports is measured wall-clock
latency; verdicts, gates, scores, and evidence are byte-identical across runs.

## Installed-local mode

`python scripts/model_qualify.py --model <installed-model>`
`python scripts/model_qualify.py --all-local`

Rules:

- local provider only, loopback only, exact requested model;
- no download, no pull, no service start, no runtime install;
- no remote provider, no remote fallback, no model substitution;
- bounded requests (see [Bounds](#bounds));
- evidence labelled `inference_mode = installed_local`.

If the provider is offline, the model is missing, or execution mode is not
`local_only`, the run reports `evaluation unavailable` (profile status
`unavailable`, every role `not_evaluated`) and the CLI exits `2`. That is never
reported as a failed model.

## Model and provider discovery

`--discover` lists installed models through the existing provider registry:

- requires `JARVIS_MODEL_EXECUTION_MODE=local_only`;
- only `is_local` providers are considered; remote providers are reported as
  `rejected_remote` and never used;
- model lists come from the provider's own `list_models()` (Ollama `/api/tags`
  via the existing adapter); if a provider cannot enumerate models, discovery
  verifies only its configured default and reports `models_unknown`;
- a missing model is reported as `model_unavailable` with the exact installed
  names to request — discovery never substitutes another model.

## Bounds

`QualificationBounds` (Pydantic, frozen, validated):

| Bound | Default | Meaning |
| --- | --- | --- |
| `max_models` | 8 | models per invocation |
| `max_roles` | 8 | roles per model |
| `max_cases_per_role` | 8 | cases per role |
| `max_repairs_per_case` | 1 | repair attempts per case (PR #64 performs at most one) |
| `max_calls_per_model` | 64 | provider calls per model |
| `max_total_calls` | 256 | provider calls per invocation |
| `max_output_chars` | 20000 | accepted response size |
| `max_context_chars` | 20000 | prompt size per case (larger cases are skipped with a warning) |
| `per_call_timeout_seconds` | 120 | per-call timeout |
| `deadline_seconds` | 900 | wall-clock deadline, checked between models |

Exceeding a bound truncates deterministically and records a warning; it never
loops, never sleeps, and never retries endlessly.

## CLI usage

```bash
python scripts/model_qualify.py --discover
python scripts/model_qualify.py --fixture
python scripts/model_qualify.py --fixture --role planner --role reviewer
python scripts/model_qualify.py --fixture --persona fixture-model/weak-reviewer
python scripts/model_qualify.py --model qwen3:14b
python scripts/model_qualify.py --model qwen3:14b --role reviewer --provider ollama
python scripts/model_qualify.py --all-local --out .local/model-qualification
```

Options: `--persona`, `--role`, `--provider`, `--out`, `--repetitions`,
`--allow-repair`, `--repo-sha`, `--json`.

Exit codes:

| Code | Meaning |
| --- | --- |
| `0` | run completed (models may still be unqualified — that is a result) |
| `2` | bad usage, or the local provider/model is unavailable (not a failed model) |
| `3` | the qualification tool itself failed |

Evidence (git-ignored, default `.local/model-qualification/`):

- `model-qualification.json` — run bundle (profiles, comparisons, recommendations)
- `model-qualification-summary.md` — human-readable summary
- `profile-<model>.json` — one profile per model

## Security boundaries

Qualification is **observational**. It must never:

- grant roles, activate identities, raise agent rank, or grant permissions;
- clear emergency stop or modify workspace trust;
- execute unrestricted tools or shell commands;
- contact cloud providers, download software or models;
- modify production routing, decomposition, or coordinator state;
- complete production tasks.

Concretely:

- model output is treated as untrusted input and validated against schemas;
- provider exceptions are reduced to category-only error codes;
- evidence stores no response bodies, and free text is secret-scrubbed
  (`app.autonomy.events.scrub_text`);
- local-only enforcement, loopback-only providers, and `allow_remote=False`
  come from the existing provider architecture;
- no migration, no database writes, no file writes outside the output directory.

## Persistence and migration impact

**No migration.** Qualification results are explicit evidence artifacts
(JSON/Markdown) under an ignored directory. Nothing is stored in the database.

## Operational signals

Recorded when available and kept **separate from quality**: request count,
repair count, malformed-response count, latency mean/p95/max, and token totals
only when the provider reports them (`tokens_reported`). Token counts are never
invented, and a slower model is never scored worse solely for being slower.

## Limitations

- The suite is small and English-only; thresholds are coarse with few cases per
  role (for example, one classification error drops classifier accuracy to
  `0.75`, below its `0.90` gate).
- Consistency and repair metrics only exist when the run repeats cases or
  enables repairs; those gates are advisory by design.
- Reviewer quality uses one defect-detection case; it is a signal, not a
  comprehensive review benchmark.
- Latency depends on the host and is not comparable across machines.
- Fixture personas validate the qualification machinery; they say nothing about
  any real model.
- Recommendations are per-run snapshots and are not automatically promoted.

## Future production-routing integration

A later milestone may explicitly consume an approved profile. Doing so safely
requires at minimum: an operator-reviewed profile, an allowlist of approved
models, explicit routing configuration changes, and tests proving the change is
reversible. Nothing in this milestone performs that step.
