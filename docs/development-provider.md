# Proposal-only coding provider

This milestone is stacked explicitly on Campaign A PR #128 and includes its
durable proposal-replay correction.
Its implementation is in app.development_agent.provider and config; it is not
registered with the ordinary local planning worker. Native source mutation still
uses separate exact-plan operator approval.

The OpenAI adapter implements the existing ModelProvider protocol and returns
ModelExecutionResponse with actual provider/model/request identity, complete
reported usage, bounded latency and sanitized errors. It posts only to the fixed
HTTPS Responses endpoint with Structured Outputs. Schema guidance requires every
object field, including nullable before/text fields; canonical Pydantic proposal
validation remains authoritative. The response's context hash must equal the
application request's hash.

The transport offers no tools, stores no provider response, disables redirects
and environment proxies and streams decompressed responses under the existing
262,144-byte cap. Refusal, incomplete output, unsolicited tool output, malformed
or duplicate JSON, wrong source context, unknown/incoherent usage and credential
echo refuse. Raw provider bodies, HTTP requests and validation exceptions are
not retained in normalized failure chains. Source/code never enters routing logs.

Input is at most 16 system/user messages and 131,072 UTF-8 bytes. Output is at most
16,384 requested tokens. A wall-clock deadline covers the entire request and
reading; cancellation propagates and closes owned clients. Complete usage is
accounted through the existing ModelRouter/TaskBudget. These per-request bounds
do not yet implement persistent campaign accounting or pre-dispatch cost proof.

Independent configuration is disabled by default:
JARVIS_DEVELOPMENT_ENABLED=false; JARVIS_DEVELOPMENT_PROVIDER=local or openai;
JARVIS_DEVELOPMENT_MODEL must be explicitly selected when enabled;
JARVIS_DEVELOPMENT_LOCAL_PROVIDER defaults to ollama;
JARVIS_DEVELOPMENT_TIMEOUT_SECONDS is 1-300 (default 120).
The OpenAI key comes from JARVIS_DEVELOPMENT_OPENAI_API_KEY or OPENAI_API_KEY as
SecretStr. No real credentials or machine paths belong in configuration examples.
No env file is implicitly loaded by this separate settings class; a trusted
launcher may use an explicitly approved env-file destination.

Even with development configuration enabled and an injected HTTP client, the
remote adapter must pass the central provider execution policy before creating
a client or sending source. Its runtime execution mode remains `disabled` until
the separate authorized cloud execution boundary exists. An enable flag, API key
or configuration-only health result grants no source-upload authority. Protocol
tests use explicit MockTransport-only policy fixtures; they do not enable live
execution or prove provider/account acceptance.

The side-effect-free factory uses a separate registry and one physical attempt
per dispatch. Local selection retains the original adapter's local-only phase
gate. OpenAI selection does not alter that gate or offer remote fallback.
Configuration health does not prove API availability, account quota or coding
qualification.

## Durable execution integration still required

The production worker must freeze an admitted task objective and operator
criteria, source scope, provider/model and one root repair/inference budget.
Changing a validation plan or source state must not reset that budget. The
original runtime/attempt/worker/lease must own each iteration.

Before inference, persist the exact bounded context, request digest, reserved
physical request allowance and immutable dispatch identity in protected storage,
with native checkpoint and transactional audit/outbox acknowledgement.
A dispatched record without a persisted response is acknowledgement-uncertain;
recovery must not automatically recall the model. Cancellation/stop/current RBAC
must prevent late response or source commits.

Persist the normalized response and provider accounting before source planning.
Recovery reuses that exact response and converges on the native source-plan
command identity. Mutation remains a subsequent independent approval/effect.
Source completion and fresh validation handoff must bind the completed native
mutation checkpoint, exact source state and artifact/history evidence.
Retained originals and the one-mutation-per-workspace constraint cannot be
silently bypassed for another iteration.

All current provider tests use deterministic HTTP transports and dummy keys.
They prove protocol, bounds, identity/accounting and refusal behavior. No live
model inference has run; this does not complete Campaign A.

Official references:
[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs),
[Codex SDK](https://learn.chatgpt.com/docs/codex-sdk).
The proposal-only API adapter avoids granting a Codex executor's native command
tools. A future executor integration requires its own reviewed execution boundary.


## Local provider checkpoint

The new adapter/configuration package passes 27 mocked cases. Existing provider
contracts/adapters and structured tool protocol plus this package pass 227 cases
with the intentional opt-in live-model skip. The existing routing/budget package
plus the new adapter passes 52 cases. Ruff lint/format and whitespace checks pass.
A corrected valid-JSON Unicode credential-echo regression reproduced a real
nested-decoding leak before repair; the adapter now checks decoded proposal data
as well as the outer response and the complete normalized request.

A bounded authenticated GET /v1/models using the explicitly approved ignored key
returned HTTP 200, with public coding model identifiers. This verifies the key's
metadata access only; no repository content was uploaded, no inference ran and
no quota, model quality or end-to-end coding success is claimed.

The final combined provider/contracts/adapters/tool protocol/routing package
passes 252 cases with one intentional opt-in live-model skip. The first rerun
hit an inaccessible shared pytest temporary directory; a fresh isolated ignored
temporary directory resolves that setup failure. Frontend typecheck, ESLint,
234 Vitest tests and the production build pass. No frontend source changed.

The existing autonomous worker already uses native checkpoints for physical
planning-dispatch reservations and refuses uncertain replay. Development dispatch
must compose that pattern and existing runtime command/outbox transactions;
it must not introduce a separate recovery state machine. Its admission and
protected source/proposal storage still require their own bounded contracts.

The next required work is the protected durable admission/iteration/dispatch
store and native checkpoint/audit/outbox composition. Source-state changes must
retain the same root repair budget, including failed or uncertain dispatches.
PR #128 remains the explicit unmerged source-context prerequisite for this
stacked branch. Neither private Campaign B diagnoses nor private Campaign C
commit receipts are adopted as available authority.
