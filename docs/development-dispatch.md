# Durable development admission and dispatch

This milestone is explicitly stacked on Campaign A PR #130, which is stacked on
#128. The repair integrates the shared source-history test correction from #141
and retains the already merged main source-history/provenance dependencies.
Campaign B validation runners and Campaign C publication code remain separate.

DevelopmentDispatchService is an internal application service. It creates no
scheduler, HTTP route, model tool, provider traffic or filesystem mutation.
The existing native runtime, task lease, RBAC, emergency-stop, independent
operator and workspace-creation boundaries remain authoritative.

## Admission

The operator reviews the exact previewed AdmissionPlan hash. The plan binds the
packaged request/context/proposal contracts, provider output schema, Context
Assembler redaction/budget implementation and source-observation dependencies.
A deployment changing those semantics invalidates earlier admissions; collector
or model text cannot renew their approval. Current OpenAPI and runtime database
revisions must also match the single packaged Alembic head.

The plan also binds the native durable task objective fingerprint, repository workspace/creation,
original run/attempt/worker/lease, explicit coding provider/model, selected source
paths, operator completion criteria, deletion policy and execution limits.
The implementation actor cannot approve itself. Admission requires the current
configured authenticated operator and native materialization/inspection authority.

A unique development_admissions row per task freezes one root budget. Replaying
the same admission reuses its original audit event and timestamp. Changing an
objective, budget, source scope, provider, workspace or owner refuses; changing a
source context or creating a new source/validation proposal cannot reset the root.
This initial implementation binds the original attempt and workspace; extending
to another owned workspace/attempt requires a separately reviewed lineage
extension that preserves this root. The native one-mutation-per-workspace policy
is not weakened.

Limits bound physical reservations (1-8), cumulative requested output tokens
(never refunded), output per request (at most 16,384), and elapsed admission time
(at most one hour). Actual reported input/output/total tokens remain in each
protected normalized response. This is not a pre-dispatch monetary cap or a
tokenizer-independent bound on input tokens. Provider pricing and an actual
financial budget require separate current configuration and proof.

## Context and persistence

Each request is built from native task data and a fresh protected source read;
callers cannot inject their own model messages. The existing Context Assembler
labels repository material untrusted, retains provenance and applies its context
limits. Sources with redactions, exclusions, missing required context or
truncation refuse before reservation. Base task/criteria messages, which the
assembler does not redact, are screened separately. Request schema, model,
task/correlation identity, context hash, token limit and deadline are application
owned. No tools or provider fallback are granted.

The Alembic migration adds protected development_admissions and
development_dispatches projections. Context, exact request, manifest and
normalized response bytes stay in these private projections. Integrity hashes
and linked audit events validate projection identity; source text, model output,
operator criteria, machine paths and lease credentials are absent from audit,
outbox and checkpoint payloads. There is no public payload importer.

The reader's ContextCommitGuard rechecks native ownership/source/metadata pins
using the caller's transaction. It prevents nesting a separate SQLite write
transaction while making the evidence commit. The ordinary callable guard
continues to support existing proposal persistence.

## Recovery and accounting

A reservation and its transactional audit/outbox event commit before inference.
A native RecordCheckpointCommand then acknowledges that exact protected record,
using the original execution fence and a commit guard. Only the first successful
reservation returns fresh=True. An existing reservation, including recovery after
a commit/checkpoint crash gap, returns fresh=False. A reserved call without a
durable response blocks any new dispatch in the root; it must not be recalled
automatically. Reservation counts and output allowances are never refunded.

The response path preserves normalized provider identity and complete usage,
requires matching task/request/context/model and no model tool requests, and
remeasures original source before commit. It records known provider evidence
before source-plan interpretation. A schema-valid proposal with an incorrect
before digest can therefore be repaired within the same root allowance; it does
not become an uncertain physical call and does not gain source authority.
Native proposal compilation/persistence and exact mutation approval remain
subsequent gates.

Reserved and received phases each have a native checkpoint with exact protected
digest, cursor, attempt, audit references and identity metadata. A persisted
response with a missing acknowledgement is recovered by acknowledging that same
response, never by asking the model again. Response replay cannot replace bytes.
Late response commits recheck current native permission, operator, lease, runtime,
stop and elapsed budget. A restart must restore the same trusted operator
configuration and pinned Git policy; those are not model-authored evidence.

## Remaining Campaign A work

A production dispatcher must consume fresh reservations exactly once, wire the
separate opt-in provider/router, renew/cancel the native lease while waiting,
persist sanitized terminal failure evidence and refuse uncertain recovery.
The development workflow must then persist native source plans from durable
responses, expose operator admission/mutation review, create validated task
iterations and hand off completed mutation checkpoints and exact source state.
Repeated source mutation requires the authoritative source-history prerequisite
or explicitly authorized fresh workspace lineage. Real configured-model coding
acceptance has not run. Fixture responses prove recovery boundaries only.

Tests cover native admission independence, budget reuse across changed contexts,
exact response/restart replay, commit/checkpoint crash gaps, uncertain dispatch
refusal, revoked permission/emergency stop, original lease/deadline fences,
projection tampering, context security and empty/populated migration behavior.
Local verification: the development/context/navigation/provider package passes
66 cases; the migration/persistence/context-security package passes 61 cases.
A final nine-case run verifies the deadline at both reservation and response
commit, missing admission checkpoint refusal and the context/migration package
after the last hardening changes. Ruff lint/format, whitespace checks, frontend
typecheck/ESLint/234 Vitest tests/build and exact CI assignment of all 2,818
collected tests pass. Native Git fixture paths use a short isolated temp root;
a longer ignored-venv path caused checkout refusal before any development ledger
operation. The tests restore trusted native Git/operator configuration during
application restart. No model inference has run.

The service is instantiated in app.state alongside the existing context/proposal
services. Construction opens no provider connection and grants no execution
capability; public admission/review and production dispatch are still pending.
