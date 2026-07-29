# Context assembler

The Context Assembler builds a provider-neutral model request and manifest. It never calls a model itself, executes a tool, grants a permission, or treats context as approval. Phase 2C may consume one explicitly referenced `completed` assembly through the separate autonomous-worker service.

## Integration boundary

`app/models/context.py` is the authoritative HTTP and stored-payload contract. `app/context/assembler.py` is a pure deterministic service: it receives a validated task, policy, and bounded source set and returns a complete or review-required assembly. Route handlers resolve the durable task and delegate assembly; the SQLAlchemy repository owns persistence. No context module opens a database or publishes an event directly.

One successful create command commits these records in the existing unit of work:

- one `context_assemblies` row containing the redacted assembled request, manifest, and report;
- one append-only `context.assembly.created` audit record;
- one schema-versioned transactional-outbox envelope;
- the terminal idempotency response, when an `Idempotency-Key` is supplied.

WebSocket publication happens only after that commit. The frontend treats the event as an ordered invalidation and refreshes the System status metrics through its existing store.

## Assembly pipeline

The pipeline is deterministic and ordered:

1. Validate request shape, task/project binding, configured source/character/token limits, unique source IDs, and cross-project policy.
2. Verify each source's SHA-256 content hash before transforming it.
3. Enforce the source-type/trust-level compatibility map and explicit source approval.
4. Redact recognized API keys, bearer tokens, passwords, and private keys.
5. Detect prompt-injection indicators without retaining matching excerpts.
6. Exclude critical findings and ordinary high-severity findings; high findings may be included only for an explicit `security_analysis` result type.
7. Sort by trust, exact-preservation requirement, inclusion priority, and source ID.
8. Deduplicate by verified original content hash, keeping the deterministic highest-trust source.
9. Apply the complete request budget, including trusted messages, context delimiters, source wrappers, and reserved output.
10. Detect explicit conflicts with prohibited tools and gate uncertain output for review.
11. Hash the canonical request and produce stable assembly, manifest, and request IDs from the canonical input.

All supplied sources remain structurally isolated inside the final untrusted user-context message. A source's trust label affects validation and ordering only; it cannot create a system or developer message. Closing delimiters and the outer end marker are escaped inside source content.

## Status and review behavior

`completed` means the sanitized provider-neutral request is available in `modelRequest`. `review_required` means critical injection, a policy conflict, or unmet minimum context requires operator review; `modelRequest` is withheld while the manifest and counts remain durable for inspection.

Exclusion alone does not always require review. For example, an unapproved, wrong-project, duplicate, invalid-hash, or ordinary high-injection source can be excluded while the remaining request completes safely. An exact-preservation source that cannot fit is rejected with `CONTEXT_REQUIRED_SOURCE_OVER_BUDGET` rather than silently truncated.

## API

- `POST /api/context/assemblies` validates and durably creates an assembly.
- `GET /api/context/assemblies` lists assemblies; optional `taskId` filters by task.
- `GET /api/context/assemblies/{assembly_id}` returns one durable assembly.

Creation returns 201. Replaying the same completed idempotency key returns its stored 201 response. Submitting identical canonical input under a different key returns the existing assembly with 200 and does not append another audit or event. Unknown tasks and assemblies return the existing structured 404 envelope. Policy/project conflicts use 409 or 422 with stable error codes.

OpenAPI exposes `CreateContextAssemblyRequest`, `ContextAssemblyResponse`, `ContextAssembly`, `ContextManifest`, and `ModelRequest`. Successful responses retain the compatible `{ "data": ..., "meta": { "schemaVersion": "1.0" } }` envelope.

## Persistence and recovery

Assembly is synchronous and has no externally visible in-progress state. A crash or exception before commit rolls back the assembly, audit, outbox event, and idempotency claim together. There is therefore no partial assembly to resume and no workflow checkpoint to invent.

After commit, the assembly and terminal idempotency response are authoritative even if HTTP response delivery or immediate WebSocket publication is interrupted. Startup reloads the assembly, dispatches its pending outbox row, and replays the same response. Recognized credentials are redacted before the assembled payload is stored; raw request content is used only for validation and canonical hashing and is not stored as a separate source record.

Simulator reset preserves assemblies for stable seeded and user tasks. Assemblies tied to reset-only generated demo child tasks are deleted before those child tasks, preserving foreign-key integrity. Context state does not alter simulator recovery position.

## Health, metrics, and logging

`GET /api/health` reports `contextAssemblerReady` and `contextAssemblyCount`. Readiness is false when the database is unreachable or the Alembic schema is stale. `GET /api/system/status` exposes totals for completed/review-required assemblies, included/excluded sources, redactions, injection findings, and the last assembly timestamp. Values are derived from authoritative durable records rather than increment-only counters.

The service emits one content-free structured log line per assembly containing IDs, status, and counts. Audit and event payloads contain hashes, IDs, status, and counts only; they never contain source text or injection excerpts.

## Configuration

- `JARVIS_CONTEXT_MAXIMUM_SOURCES` defaults to 32.
- `JARVIS_CONTEXT_MAXIMUM_TOKENS` defaults to 8192.
- `JARVIS_CONTEXT_MAXIMUM_TOTAL_CHARACTERS` defaults to 500000.
- `JARVIS_CONTEXT_CROSS_PROJECT_ALLOWED` defaults to false.

Request policy can be stricter than these settings but cannot raise a server cap. Cross-project context requires both the request policy and server setting; it remains disabled by default.
When `maximumContextTokens` is omitted, the prototype-compatible `estimatedTokenBudget` is the effective request budget; an explicit maximum takes precedence.

## Known limitations and extension points

- Injection detection and credential redaction are conservative heuristics, not proofs of safety.
- Token counts use a deterministic character estimate of approximately 3.5 characters per token.
- Context is not semantically summarized, and external-source truth is not verified.
- The local API currently has no authentication boundary, so deployment remains local-only.
- The assembler does not read repository files itself; callers provide bounded content and a matching provenance hash.
- Only the explicitly queued local `planning_review` worker consumes completed assemblies. It verifies task binding, status, typed payloads, and all three stored request hashes; it never performs a "latest assembly" lookup or reads original sources.
- There is no tool executor or approval generator.
- SQLite and the in-process outbox dispatcher target one local API process.

The Phase 2C worker maps the provider-neutral request through a deterministic adapter, preserves context as user data, appends one fixed output-schema system instruction, and records a separate execution-request hash. A future tokenizer can replace the estimator behind the budget interface without changing API or stored manifest fields. Filesystem, model, and tool access remain outside the assembler.
