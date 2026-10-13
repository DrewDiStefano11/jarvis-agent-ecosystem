# Durable development admission and dispatch

The execution milestone is stacked on Campaign A PR #133, #130 and #128.
Origin/main 5e11d60 supplies merged source history/recovery, native artifact
inspection, applied-source provenance and Git commit preparation. No private
Campaign B runner or Campaign C publication implementation is imported.

DevelopmentDispatchService is an internal application service. It creates no
scheduler, HTTP route, model tool, provider traffic or filesystem mutation.
The existing native runtime, task lease, RBAC, emergency-stop, independent
operator and workspace-creation boundaries remain authoritative.

## Admission

The operator reviews the exact previewed AdmissionPlan hash. The plan binds the
native durable task objective fingerprint, repository workspace/creation,
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
preserves the known receipt against its original protected request/context.
Current native authority and deadline are rechecked at receipt commit; current
source is independently remeasured when compiling a source plan. It records known provider evidence
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

## Execution, source approval and handoff

DevelopmentExecutor consumes only fresh reservations, monitors/renews the original
native lease, cancels its single provider operation on deadline/stop/cancellation,
and persists sanitized failure categories without refunding reservations.
Unknown outcomes block subsequent calls. Exact stored request bytes are retained;
the outer execution deadline may shorten their timeout, never alter the request.

Known responses compile into native source-edit plans under a deterministic
command identity. Final source-plan commits recheck native source pins, root
budget, original response, configuration and authority in the same transaction.
A crash between source-plan commit and binding reuses the protected native plan.
Reservation, receipt, terminal failure and editing-binding checkpoint gaps recover
without recalling a provider or rereading original source after mutation.

Model output never approves a mutation. `apply_approved` binds the recorded plan,
original worker/lease and explicit native mutation approval. Native ownership,
source state, effect journal/recovery, emergency stop and transaction fences remain
authoritative. Recorded iteration status is distinct from fresh source evidence.

`handoff` composes the merged MutationHistoryReader and MutationInspector. It
requires acknowledged completed mutation history and an actual matching native
artifact measurement, checks exact expected CodeState, and returns a versioned
DevelopmentHandoff with requested validation. Validation passed, publication ready
and execution authorized are all false. Campaign B's real isolated runner remains
a separate requirement. No mocked command output becomes execution evidence.

## Activation and remaining work

Production dispatch is quarantined regardless of configuration. The factory
constructs no router and reads no API key; the production executor refuses even
an enabled injected router. Tests use a test-only subclass with deterministic
MockTransport. Preferred future integration is Campaign D's native subscription-
authenticated Codex app-server, contingent on independent included-only usage,
purchased-credit exclusion and recharge safeguards. No live inference has run.

Current bounded repair supports fresh proposals after known-response source
conflicts within the original task budget. A new command consumes the next slot;
replays and failures never reset that root. Repeated approved source effects still
require a separately reviewed correction baseline/workspace lineage, rather than
weakening the native one-mutation-per-workspace invariant. Operator-facing task
admission/review orchestration and that correction lineage remain Campaign A work.
Real coding acceptance remains separately gated and unperformed.

Local deterministic tests exercise real native multi-file mutation, restart/replay,
artifact handoff, source conflict repair, sanitized failure, cancellation/stop,
disabled production dispatch and protected checkpoint crash recovery. These tests
prove implementation boundaries, not real coding quality or independent validation.
