# Local model acceptance evidence

On 2026-09-05, the continuation starting from `6e73df6` exercised an installed
Ollama `qwen3.5:0.8b` model through the actual frontend submission function,
FastAPI subprocess, separately running autonomous worker, and an isolated temporary SQLite
database. No model downloads, cloud credentials, user database changes, or committed environment
files were needed. This is behavior evidence for the output-format change; final integrated
commit validation must be recorded separately.

The successful home-office planning task used one actual inference request, 524 input tokens
and 443 output tokens, with about 32.2 seconds of provider latency on CPU. Canonical structured
result validation and deterministic review accepted it. The task reached `completed` with its
`model-execution:` result reference. Replaying the same frontend submission produced one runtime
run. After restarting the actual API against the same database, the complete task and execution
record were identical to their pre-restart values.

The result contained concrete workspace recommendations, risks, mitigations and assumptions.
This demonstrates bounded planning execution, not general tool use or an independent judgment
that every recommendation is correct. The small model's response included some assumptions
about shared work that the operator had not supplied.

The preceding baseline checks exposed genuine model compatibility failures: installed
`qwen2.5-coder:0.5b` exhausted its initial and repair output attempts; `qwen3.5:0.8b` exhausted
its 2,048-token allowance without an accepted answer. Both entered visible human-review state.
Sending the complete result schema also exposed Ollama's grammar repetition limit. Explicit
schema generation with reasoning disabled resolved the Qwen 3.5 path; omitting only generation
maximums kept the canonical validator intact. A failed execution/task also remained identical
after API restart. No failed run was reported as successful.

The validation harness was derived from `scripts/smoke-local-planning.py`, changing only its
provider endpoint/model, task text, waiting allowance and post-run API restart/readback checks.
It retained the actual submission and worker paths, and used only loopback services. API and
worker subprocesses and their temporary databases were cleaned up after each run. Generated
JSON evidence and diagnostic logs remained outside the repository in a temporary QA directory.
