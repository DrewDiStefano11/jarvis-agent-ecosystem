# Structured model tool requests

## Audit and scope

The baseline audit at `4758891` found a production `ModelRouter` with static
capability filtering before health traffic, stable registration order, bounded
retry/fallback and per-execution request/token/cost accounting. The adapters
already map non-streaming text and schema-guided JSON output. Before this change,
`tool_calling` existed only in the capability vocabulary: configuration rejected
it, neither adapter sent tool definitions, and both required nonempty text.
There was no native call parser, validated call identity or result contract.
The isolated campaign was subsequently moved onto merged `main` at `c393985`;
the audited provider/router implementation is unchanged at that base. The newly
merged development-workspace APIs are not used by this protocol.

Ollama documents `tools` and assistant `tool_calls` with object arguments and an
optional function index. Chat Completions documents function tools with string
JSON arguments and provider call IDs. These are different native protocols, not
JSON text extracted from a prompt. References:
[Ollama tool calling](https://docs.ollama.com/capabilities/tool-calling),
[Ollama chat](https://docs.ollama.com/api/chat), and
[OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling).
Wire compatibility does not establish capability for every compatible server or
installed model. Explicit `tool_calling` configuration is required, default
capabilities remain `chat,text_generation`, and unsupported native requests fail
as normalized errors. There is no capability claim for streaming, vision,
legacy `function_call`, custom tools, or result-continuation messages.

The existing `ToolExecutionService` authorizes only completed durable workspace
plans after exact plan-hash review, configured operator identity and runtime
RBAC checks. It uses bounded marked workspaces, leased tasks, runtime fences,
checkpoints and transactionally committed audit/outbox records. Recovery reads
durable step records and validated checkpoint digests. Runtime recovery verifies
the execution ledger, attempts, checkpoint lineage, versions and sequences.
This foundation changes none of those services, migrations, routes or authority
checks, and does not depend on unmerged Self-Build APIs.

## Protocol

Contracts live in `app.models.model_tools`. `ModelExecutionRequest.tools`
contains application-supplied definitions; `tool_request_id` is mandatory when
tools are supplied and must be assigned by the trusted caller before inference.
That ID must identify one durable logical inference operation, not a model's
chosen ID. Tool definitions are requests the model may propose, not execution
capabilities. They do not register a handler or grant a workspace scope.

The strict supported schema subset is explicit closed objects, arrays, strings,
integers, numbers, booleans, nulls and enums. Every object property is required;
strings require `maxLength`, arrays require `items` and `maxItems`. Unknown
schema keywords, references, regexes and combinators fail explicitly. No schema
fetching, coercion, inferred defaults or credential-bearing properties occur.
Tool names use the common function-name alphabet (`A-Z`, `a-z`, digits,
underscore and hyphen); use an explicit future trusted mapping for names such
as `workspace_read`, never infer an executable command from the string.

Limits are enforced regardless of whether the provider follows the schema:

| Item | Hard limit |
| --- | --- |
| Offered definitions / returned calls | 8 / 8; default returned-call cap 1 |
| Definition JSON | 16,384 UTF-8 bytes each |
| Arguments | 16,384 UTF-8 bytes per call |
| Payload nesting | 8 levels |
| Object properties / array items | 32 / 64 |
| Native tool response on wire | 262,144 decompressed bytes, read in bounded chunks |
| Normalized tool response / result output | 65,536 UTF-8 JSON bytes each |

Adapters reject unknown tools, unoffered calls, extra/missing arguments, wrong
types, invalid JSON, duplicate JSON keys, nonfinite values, truncated tool
responses and duplicate compatible call IDs. Ollama must finish the assistant
response; compatible calls require `finish_reason=tool_calls`. Empty text is
accepted only with validated calls; a model can also return bounded text without
making a call. Output-schema guidance and tools cannot be combined in this phase.

`ModelToolRequest` has a local `mtc_` identity, ordinal, name, validated arguments,
optional provider call ID and fixed `trust=model_request`. Its SHA-256 identity
binds application request ID, provider, returned model, ordinal, name and canonical
arguments. Identical normalized acknowledgement produces identical IDs; changed
arguments, lineage, provider or model do not alias. Ollama has no required native
call ID, so its ordinal is assigned from the validated returned list. Compatible
IDs remain untrusted provenance, never authorization/idempotency commands.
The response carries provider/model/request provenance and the normal budget and
routing metadata. The router revalidates calls against the original definitions,
count and derived identities before returning them.

`ModelToolResult` carries a call ID, trusted execution record reference, status
(`completed`, `failed`, `uncertain`), bounded output and fixed
`trust=tool_observation`. Output remains untrusted even when a trusted service
records it. It cannot become a system message or permission decision. The result
contract is prepared for later integration; adapters do not yet send it back to
models as continuation history.

Credentials remain transport configuration. Definitions, arguments, results and
tool-bearing content reject secret keys/patterns. Compatible tool responses also
reject the exact configured credential in raw and decoded fields, including
provider model/response IDs and request headers, before it can enter routing logs.
Malformed response errors contain fixed descriptions and no raw response,
argument-bearing Pydantic errors or credential-bearing exception chains.
Callers must continue to redact source data before sending any model request.

## Trusted integration and recovery boundary

`app.models.model_tool_handoff.ModelToolHandoff` is a review-only contract for a
future service to attach existing task/run/attempt/checkpoint references and an
exact persisted response digest. It is not accepted by any execution endpoint
and implements no durable store, permission grant, dispatcher or recovery loop.
The existing authorization endpoint rejects both model requests and observation
DTOs, even if the workspace-tool gate is enabled. The production router has no
reference to a tool execution service. Normal planning workers offer no tools.

A future adapter must load trusted runtime state and check reference ownership,
active actor/RBAC, lease fencing, current attempt/version, emergency stop and the
bounded workspace policy. It must persist the exact validated request before
review, map only existing allowed tool schemas, and obtain the existing separate
operator approval for the exact durable plan hash. Model-supplied actor IDs,
approval flags, scope, hashes or prose cannot replace those checks. Authorization
must be rechecked immediately before every side effect; audit/outbox must use the
existing transaction boundaries. No shell, code execution, network tools,
automatic repository modification or cloud fallback is added here.

Required recovery semantics for that future adapter:

| Durable position | Required action |
| --- | --- |
| Inference interrupted before any validated response is persisted | No tool may run; a bounded new inference attempt may be made under existing runtime policy |
| Validated response persisted, no trusted approval | Reload exact response/call IDs and await explicit approval |
| Approved execution started, acknowledgement uncertain | Block and reconcile existing execution/step/checkpoint records; never infer again or blindly replay a side effect |
| Result durably recorded, publication interrupted | Reload result and use existing transactional outbox recovery |
| Conflicting/missing digest, identity or lineage | Require recovery review; reject execution |

These are integration requirements, not a claim that this DTO adds durable
recovery. A hash cannot establish that an external effect did or did not happen.
Fresh inference is not deterministic replay and must never authorize a second
effect after uncertain acknowledgement. Existing provider retries remain bounded
generation retries only; the router performs no tool side effects.

## Validation and live acceptance

`tests/test_model_tool_calls.py` exercises production routing with deterministic
HTTP transports, malformed data, credential echoes, limits, budgets and the real
existing authorization endpoint backed by an isolated temporary database. These
tests prove wire mapping and rejection boundaries, not arbitrary-model ability.

The live Ollama test performs actual inference through `ModelRouter` and the
native production adapter. It uses the existing loopback endpoint, a single
bounded request, no downloads, and no tool execution. Opt in only with an installed
tool-capable model:

```powershell
$env:JARVIS_TEST_OLLAMA_TOOL_MODEL = '<installed-model-id>'
python -m pytest tests/test_model_tool_calls.py -k live_ollama -q
```

Run from `apps/api` with repository dependencies. Without that environment value,
CI explicitly skips the live test. A passing fixture is never substituted for a
passing live test. There is no required OpenAI credential and the remote execution
gate remains closed. Model/provider generation, autonomous execution and workspace
tool execution remain independently disabled by default.

For a PR, require independent review and successful required checks on its exact
final head SHA. Older-head or local checks do not establish remote CI status.
Human approval controls pushing and merging; no automated merge is configured.
