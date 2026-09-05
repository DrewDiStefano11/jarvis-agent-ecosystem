# Model provider routing foundation

## Purpose and boundary

`apps/api/app/model_providers` defines the provider-independent foundation used by the explicitly
authorized Phase 2C local planning worker. It provides contracts, adapter mappings,
registration, deterministic routing, retry/fallback policy, per-execution budgets, safe error
normalization, and configuration factories.

Construction remains side-effect-free and does not create an HTTP client or contact a provider.
Application startup builds safe provider metadata and a router; only the separate, explicitly
enabled autonomous worker invokes it. Ordinary API handlers, the simulator, tools, and the office
do not generate model traffic.

This change does **not** add:

- OpenHands
- tool calling
- vision payload mapping
- streaming
- persistent or cross-process budget accounting
- general-purpose autonomous orchestration
- remote model execution
- office integration
- memory integration

## Current phase gate

`JARVIS_MODEL_EXECUTION_MODE` is `disabled` by default. In that mode:

- adapter execution raises `ProviderExecutionDisabledError` before client creation;
- provider health and model-list network access return configuration-only/unknown results;
- lowest-level network helpers independently enforce the same phase gate before client creation;
- enabling provider configuration registers safe metadata only; and
- application startup does not contact a provider.

`local_only` is the only enabling mode. It requires a structurally loopback provider, disables
redirect following and proxy-environment inheritance in production clients, and is usable only
through the disabled-by-default autonomous worker. Remote URLs, private LAN hosts, Docker names,
and deceptive localhost names remain ineligible; `JARVIS_MODEL_ALLOW_REMOTE` does not override
the worker boundary. Health and model-list traffic use the same gate. Tests use controlled mock
transports and never require a live provider.

## Contracts

`ModelExecutionRequest` accepts exactly one of:

- `prompt`, which is normalized into one `user` message; or
- `messages`, using `system`, `user`, `assistant`, or `tool` roles.

Requests may select a model, temperature, maximum output tokens, timeout, task/correlation IDs,
required capability, and bounded metadata. Streaming is rejected. The capability vocabulary also
contains future `tool_calling` and `vision` values, but built-in adapters reject configuration
claiming those values because their string-only contracts do not map them. `structured_output`
can be explicitly declared for a compatible local server supporting JSON-schema response
formatting; default compatible capabilities remain unchanged.

Requests may carry a bounded, application-supplied `output_schema` and a
`prefer_no_reasoning` preference. Ollama maps these to `format` and `think: false`.
Compatible adapters send `response_format` only when configured with `structured_output`;
they omit the nonportable reasoning preference. These are generation aids, not permission or
validation bypasses. Final output still passes canonical Pydantic validation and deterministic
review, with existing token, time and request limits. See the
[Ollama chat contract](https://docs.ollama.com/api/chat) and
[thinking behavior](https://docs.ollama.com/capabilities/thinking).

`ModelExecutionResponse` contains generated text, provider and model names, normalized token
usage and quality, latency, finish reason, identifiers, optional exact-price cost estimate, and
sanitized provider/routing metadata. It never contains a raw provider response.

The provider protocol exposes stable identity/type/locality/capabilities/default-model fields,
health and model-availability checks, normalized execution, and a secret-safe summary.

## Adapters

### Ollama

`OllamaProvider` maps future non-streaming chat requests to relative `/api/chat` and model
enumeration to `/api/tags`. Temperature maps to `options.temperature`; the output-token limit
maps to `options.num_predict`. Usage and finish reason are normalized and malformed values fail
closed.

Locality is derived from the parsed hostname. Only `localhost` (with an optional terminal dot)
and IP addresses classified as loopback are local. Private LAN addresses, public names, Docker
service names, `host.docker.internal`, and names such as `localhost.example.com` are remote.

### OpenAI-compatible HTTP

`OpenAICompatibleProvider` uses `httpx` directly and posts to relative `chat/completions`, so
versioned prefixes such as `/v1` and `/v1beta/openai` remain intact. It sends only mapped fields
and bearer authorization from `SecretStr` configuration. Custom headers must be explicit and
cannot override secret-bearing header names. Plain HTTP is accepted only for structurally
loopback endpoints; remote keyed providers require HTTPS.

Health strategies are:

- `models`: call relative `models` and check exact model IDs;
- `root`: call the configured service root; and
- `configuration`: validate local configuration without a network request.

Health checks never generate text. The generic interface is compatible with a future local
LiteLLM proxy, but this repository adds no LiteLLM dependency, container, connection, URL,
credential, or runtime integration.

## Normalized errors

Provider-independent categories cover:

- provider unavailable
- authentication failure
- temporary rate limit
- hard quota exhausted
- timeout
- invalid model request
- model unavailable
- budget exceeded
- malformed provider response
- unknown or duplicate provider
- provider configuration failure
- execution disabled by phase policy
- transient provider/server failure

HTTP status translation distinguishes authentication, invalid requests, unavailable endpoints or
models, timeouts, temporary throttling, bounded structured hard-quota signals, and transient
server errors. Remote bodies are not retained or returned. Numeric `Retry-After` is bounded by
the retry maximum. Normalized errors do not retain credential-bearing HTTP exceptions or request
objects in their cause/context chains.

## Retry versus fallback

Retry repeats the same provider operation. It is bounded (maximum ten configured attempts), uses
deterministic exponential backoff, honors bounded numeric `Retry-After`, and has injectable sleep
for deterministic tests. Every attempt consumes request budget. Only network/unavailable,
timeout, temporary rate-limit, and transient-server errors retry.

Fallback moves to the next statically eligible provider. It is separately enabled and bounded.
Authentication, invalid request, budget, malformed response, and phase-policy failures do not
fall back. Hard quota falls back only when explicitly allowed. There are no background retries
or unbounded loops.

## Routing precedence

Registry insertion order is stable. Configured priority reorders only known enabled providers;
unlisted providers remain in stable registration order.

Eligibility is evaluated deterministically:

1. establish requested-provider/fallback scope;
2. apply capability, allowlist, denylist, and local/remote policy;
3. health-check only survivors;
4. resolve the model as routing `preferred_model`, request model, then provider default;
5. check exact model availability for every survivor; and
6. prefer local survivors when requested and no provider was explicitly selected.

Unknown availability remains eligible because not all compatible services enumerate models.
Known unavailability removes the candidate. Each fallback uses its own default when neither
routing nor request selected a model.

Successful routing metadata contains only selected provider/model, locality, fallback count,
attempted provider names, and normalized failure categories. It contains no prompt or output.

## Budgets and pricing

`TaskBudget` applies to one router execution and may cap:

- provider requests;
- input tokens;
- output tokens;
- total tokens; and
- estimated cost.

Failed attempts consume request budget. Explicit output requests are checked before the call
where possible. Unknown usage is never treated as zero when a relevant token cap is active.

When a cost cap is active, the exact routed model must have explicit pricing before execution,
and the response must contain complete input/output usage. A returned model alias must have its
own exact price. No fuzzy matching, free defaults, or unknown-cost acceptance occurs. Without a
cost cap, unpriced models and unknown usage may produce an unknown estimate.

Accounting is deliberately in memory for one call. Persistent multi-process accounting is
deferred.

## Secrets and metadata

API keys come only from environment-backed settings and use `SecretStr`. There is no real
default. Provider `repr`, summaries, normalized errors, logs, responses, and routing metadata do
not expose keys.

Metadata rejects secret-bearing keys recursively, including variants of `api_key`,
`authorization`, `token`, `password`, `secret`, and `credential`. Key count, serialized bytes,
and nesting depth are bounded with recursive-container protection. Exception sanitization
redacts bearer tokens and key/value assignments.

Logs contain only safe operational identifiers: task/correlation IDs, provider, model, latency,
normalized category, and attempt number. Prompts, outputs, headers, raw bodies, keys, private
paths, and public tracebacks are excluded.

## Configuration

All providers are disabled by default:

| Variable | Default | Purpose |
| --- | --- | --- |
| `JARVIS_MODEL_OLLAMA_ENABLED` | `false` | Register Ollama metadata |
| `JARVIS_MODEL_OLLAMA_NAME` | `ollama` | Stable registry name |
| `JARVIS_MODEL_OLLAMA_BASE_URL` | loopback placeholder | Future endpoint |
| `JARVIS_MODEL_OLLAMA_MODEL` | `configure-a-model` | Default model |
| `JARVIS_MODEL_OLLAMA_TIMEOUT_SECONDS` | `30` | Request timeout |
| `JARVIS_MODEL_OLLAMA_CAPABILITIES` | `chat,text_generation` | Honest capabilities |
| `JARVIS_MODEL_OPENAI_COMPATIBLE_ENABLED` | `false` | Register compatible metadata |
| `JARVIS_MODEL_OPENAI_COMPATIBLE_NAME` | `openai-compatible` | Stable registry name |
| `JARVIS_MODEL_OPENAI_COMPATIBLE_BASE_URL` | `https://example.invalid/v1` | Placeholder endpoint |
| `JARVIS_MODEL_OPENAI_COMPATIBLE_API_KEY` | unset | Required secret when enabled |
| `JARVIS_MODEL_OPENAI_COMPATIBLE_MODEL` | `configure-a-model` | Default model |
| `JARVIS_MODEL_OPENAI_COMPATIBLE_TIMEOUT_SECONDS` | `30` | Request timeout |
| `JARVIS_MODEL_OPENAI_COMPATIBLE_CAPABILITIES` | `chat,text_generation` | Honest capabilities |
| `JARVIS_MODEL_OPENAI_COMPATIBLE_HEALTH_STRATEGY` | `models` | `models`, `root`, or `configuration` |
| `JARVIS_MODEL_PROVIDER_PRIORITY` | `ollama,openai-compatible` | Stable priority |
| `JARVIS_MODEL_ALLOW_REMOTE` | `false` | Default remote routing policy |
| `JARVIS_MODEL_PREFER_LOCAL` | `true` | Default local preference |
| `JARVIS_MODEL_RETRY_MAXIMUM_ATTEMPTS` | `2` | Bounded attempts |
| `JARVIS_MODEL_RETRY_INITIAL_BACKOFF_SECONDS` | `0.25` | Initial delay |
| `JARVIS_MODEL_RETRY_MAXIMUM_BACKOFF_SECONDS` | `5` | Delay and `Retry-After` bound |
| `JARVIS_MODEL_DEFAULT_MAXIMUM_REQUESTS` | `2` | Default request cap; must cover retries |
| `JARVIS_MODEL_DEFAULT_MAXIMUM_INPUT_TOKENS` | unset | Optional input cap |
| `JARVIS_MODEL_DEFAULT_MAXIMUM_OUTPUT_TOKENS` | unset | Optional output cap |
| `JARVIS_MODEL_DEFAULT_MAXIMUM_TOTAL_TOKENS` | unset | Optional total cap |
| `JARVIS_MODEL_DEFAULT_MAXIMUM_COST_USD` | unset | Optional cost cap |
| `JARVIS_MODEL_PRICING_JSON` | `{}` | Exact provider-and-model pricing map |

Pricing JSON is strict:

```json
{
  "example-provider": {
    "example-model": {
      "input_per_million_usd": 0.0,
      "output_per_million_usd": 0.0
    }
  }
}
```

This placeholder illustrates schema only. Real local URLs, keys, `.env` contents, passwords, and
developer-specific configuration must not be committed.

## Adding a future provider

1. Implement the provider protocol and `ProviderBase` safe summary.
2. Declare only capabilities mapped through both request and response contracts.
3. Normalize all provider data and typed failures; retain no raw response objects.
4. Put the phase gate before client creation and every network operation.
5. Add disabled-by-default, secret-safe settings and a side-effect-free factory entry.
6. Add only mocked transport tests for success, malformed data, security, health, availability,
   retry, fallback, and budget interaction.
7. Update this document and seek an explicit repository-policy change before enabling traffic.

## Deferred work

Remote-provider threat modeling, streaming, tools, vision,
persistent accounting beyond one durable execution result, production telemetry, general
autonomous scheduling, memory, office controls, and external gateway operations remain outside
this phase. See `docs/autonomous-worker.md` for the one real local execution path.
