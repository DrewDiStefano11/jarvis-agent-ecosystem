# Model providers and routing

The model-provider subsystem defines an opt-in infrastructure boundary between a validated
provider-neutral request and a future LLM service. It does not make autonomous decisions, execute
tools, alter task persistence, or become a default production path.

## Current phase policy

`AGENTS.md` states that Phase 1 prohibits real models and external integrations. The provider
contracts, routing policy, budgets, retries, adapters, and offline mocks exist as a future-facing
boundary, but all built-in live execution is blocked by the non-configurable
`phase_1_no_live_models` policy before a client or network request is created. Built-in health and
model-availability checks are configuration-only and make no network requests. There is no
environment setting that can enable execution or network health during Phase 1.

Enabling an adapter in configuration registers its metadata only; it does not bypass the phase
gate. Live execution requires a future repository-policy/milestone change and a corresponding code
change. OpenHands and all other external workers remain deferred.

## Architecture

`ModelRouter.execute()` accepts a `ModelExecutionRequest`, `RoutingRequirements`, and `TaskBudget`.
It selects an eligible provider, enforces policy and budget boundaries, and delegates through
`RetryExecutor`. Fake providers exercise this path in Phase 1. Built-in adapters reject execution
with `ProviderExecutionDisabledError`.

The package has these focused responsibilities:

- `contracts.py`: messages, requests, responses, capabilities, health, and the provider protocol.
- `errors.py`: stable provider-independent error categories and safe diagnostic fields.
- `ollama.py` and `openai_compatible.py`: provider request/response and error translation.
- `registry.py`: ordered registration, lookup, health, and capability filtering.
- `budget.py`: per-execution request, input, output, total-token, and optional cost limits.
- `retry.py`: bounded exponential retry with `Retry-After` support and injected sleeping.
- `router.py`: deterministic eligibility, locality preference, and explicitly bounded fallback.
- `factory.py`: side-effect-free construction from application settings.

No provider client or health check is created at import time. The existing application does not
construct the router during startup, and the phase gate prevents built-in network activity.

## Contracts

Messages support `system`, `user`, `assistant`, and `tool` roles. Callers provide either messages
or a plain prompt; a prompt becomes one user message. Empty content, invalid temperatures,
nonpositive timeouts or token limits, secret-bearing metadata, conflicting inputs, and streaming
requests are rejected.

Responses contain generated content, stable provider and model names, optional exact usage,
latency, finish reason, task/correlation IDs, provider request ID, estimated cost, and sanitized
routing metadata. Total tokens are derived when both input and output counts exist. Raw provider
responses and SDK objects are never returned.

Streaming is deliberately deferred. A request cannot set `streaming=true` and have it silently
ignored.

## Providers

### Ollama

After the Phase 1 restriction is lifted, the Ollama boundary is designed for nonstreaming chat
requests to `/api/chat`. Temperature maps to
`options.temperature`, maximum output tokens map to `options.num_predict`, and configured
keep-alive is optional. `/api/tags` provides a no-token health and configured-model availability
check.

Ollama locality is derived from the parsed endpoint hostname. `localhost`, `127.0.0.1`, `::1`, and
other IP addresses classified as loopback are local. Private LAN addresses, Docker service names,
`host.docker.internal`, public hostnames, and deceptive names such as `localhost.example.com` are
conservatively remote. Docker deployments using those names must explicitly allow remote routing
after the phase restriction is lifted. `allow_remote=false` excludes them before health or model
availability is checked.

### OpenAI-compatible HTTP

After the Phase 1 restriction is lifted, the generic adapter is designed to post the minimal
nonstreaming chat-completion shape to relative `chat/completions`.
It is not coupled to OpenAI's SDK and can target compatible vendor APIs, local servers, or an
internal gateway. Optional temperature and maximum tokens are sent only when requested.

The future network health strategy is configurable:

- `models` calls relative `models` and checks the configured model without consuming tokens.
- `root` calls the service root without generating text.
- `configuration` validates local configuration and performs no remote request.

Compatibility varies by vendor. A minimal completion health check is intentionally not provided,
because routine health probes must not consume paid tokens.

## Errors and retries

Adapters normalize authentication, temporary rate limit, hard quota exhaustion, timeout, invalid
request, model unavailable, provider unavailable, transient server, and malformed response
failures. Registry, configuration, and budget failures have their own categories. Safe response
fields are inspected to distinguish temporary per-minute throttling from hard/daily/billing
quota exhaustion; full remote bodies are neither retained nor exposed.

Only errors marked retryable are retried: network unavailability, timeout, temporary rate limit,
and transient server failures. Authentication, invalid request, hard quota, budget, malformed
response, and same-provider model-unavailable failures receive one attempt. Backoff is bounded
exponential delay, respects numeric `Retry-After` up to the configured maximum, has no jitter, and
is deterministic in tests. Every attempt, including a failed attempt, consumes request budget.

Fallback is separate from retry and must be explicitly enabled and bounded. Authentication,
invalid request, and budget failures never fall back. Hard quota can fall back only when
`allow_quota_fallback` is explicitly enabled and request budget remains. An explicitly requested
provider is not bypassed unless fallback is allowed.

## Routing

Routing first applies static policy: explicit provider/fallback scope, capability, allowlist,
denylist, and local/remote permission. Only the remaining candidates are health-checked, in stable
registry order, so prohibited providers are never contacted. Registry-wide administrative health
inspection remains available separately.

The effective model precedence is `RoutingRequirements.preferred_model`, then
`ModelExecutionRequest.model`, then each provider's `default_model`. Service health is separate
from default-model availability. The effective model is checked for every candidate; known
unavailability excludes it, while unknown availability is currently allowed for compatible
providers that cannot list models. Fallback providers compute their own effective default when
there is no explicit model. The exact routed model is sent to execution and reported in routing
metadata.

Safe routing metadata reports the selected provider/model, whether it is local, fallback count,
attempted provider names, and normalized failure categories. It contains no prompt or output.

## Budgets and pricing

`TaskBudget` can limit requests, input tokens, output tokens, total tokens, and estimated USD cost.
Request and requested-output limits are checked before a provider call. If a cost cap is active,
the selected provider/model must have exact configured pricing before execution. A response model
alias must also have its own explicit pricing entry; aliases are never matched fuzzily. Missing
pricing fails closed with `BudgetExceededError`, including independently for fallback providers.
Without a cost limit, missing pricing is permitted and estimated cost remains unknown.

Unknown token usage is never treated as zero. With token limits configured, the default
`reject_unknown_usage=true` fails closed. A cost limit always requires complete input and output
token counts, regardless of `reject_unknown_usage`; missing or partial usage makes the tracker
unaccountable and blocks that response plus every later attempt. Explicit zero counts are valid.
Unknown pricing is never treated as zero under a cost limit. Budget state is currently in-memory
for one router execution; durable cross-process task accounting is deferred.

## Configuration

All providers are disabled by default. Enabling Ollama requires no credential. Enabling the remote
provider requires a nonempty environment-provided API key, but neither setting enables live
execution during Phase 1.

| Variable | Default |
| --- | --- |
| `JARVIS_MODEL_OLLAMA_ENABLED` | `false` |
| `JARVIS_MODEL_OLLAMA_NAME` | `ollama` |
| `JARVIS_MODEL_OLLAMA_BASE_URL` | `http://127.0.0.1:11434` |
| `JARVIS_MODEL_OLLAMA_MODEL` | `qwen3.5:4b` |
| `JARVIS_MODEL_OLLAMA_TIMEOUT_SECONDS` | `30` |
| `JARVIS_MODEL_OLLAMA_CAPABILITIES` | `chat,text_generation` |
| `JARVIS_MODEL_OPENAI_COMPATIBLE_ENABLED` | `false` |
| `JARVIS_MODEL_OPENAI_COMPATIBLE_NAME` | `openai-compatible` |
| `JARVIS_MODEL_OPENAI_COMPATIBLE_BASE_URL` | placeholder invalid domain |
| `JARVIS_MODEL_OPENAI_COMPATIBLE_API_KEY` | none |
| `JARVIS_MODEL_OPENAI_COMPATIBLE_MODEL` | `configure-a-model` |
| `JARVIS_MODEL_OPENAI_COMPATIBLE_TIMEOUT_SECONDS` | `30` |
| `JARVIS_MODEL_OPENAI_COMPATIBLE_CAPABILITIES` | `chat,text_generation` |
| `JARVIS_MODEL_OPENAI_COMPATIBLE_HEALTH_STRATEGY` | `models` |
| `JARVIS_MODEL_PROVIDER_PRIORITY` | `ollama,openai-compatible` |
| `JARVIS_MODEL_ALLOW_REMOTE` | `false` |
| `JARVIS_MODEL_PREFER_LOCAL` | `true` |
| `JARVIS_MODEL_RETRY_MAXIMUM_ATTEMPTS` | `2` |
| `JARVIS_MODEL_RETRY_INITIAL_BACKOFF_SECONDS` | `0.25` |
| `JARVIS_MODEL_RETRY_MAXIMUM_BACKOFF_SECONDS` | `5` |
| `JARVIS_MODEL_DEFAULT_MAXIMUM_REQUESTS` | `1` |
| `JARVIS_MODEL_DEFAULT_MAXIMUM_TOTAL_TOKENS` | unset |
| `JARVIS_MODEL_DEFAULT_MAXIMUM_COST_USD` | unset |
| `JARVIS_MODEL_PRICING_JSON` | `{}` |

Example local configuration:

```dotenv
JARVIS_MODEL_OLLAMA_ENABLED=true
JARVIS_MODEL_OLLAMA_BASE_URL=http://127.0.0.1:11434
JARVIS_MODEL_OLLAMA_MODEL=qwen3.5:4b
JARVIS_MODEL_OLLAMA_CAPABILITIES=chat,text_generation,code_generation
```

This registers provider metadata only during Phase 1.

Built-in Ollama and OpenAI-compatible adapters support exactly `chat`, `text_generation`,
`code_generation`, `code_editing`, and `reasoning`. Those capabilities use the existing
string-message and string-response mapping end to end. Whitespace around comma-separated values is
ignored, while capability names are case-sensitive lowercase enum values. Configuration containing
`tool_calling`, `structured_output`, `vision`, or any other unsupported value is rejected. The
broader shared capability enum remains available to fake providers and future adapters. Tool
calling, structured output, and vision request/response mappings are deferred.

Example compatible endpoint configuration (placeholder only):

```dotenv
JARVIS_MODEL_OPENAI_COMPATIBLE_ENABLED=true
JARVIS_MODEL_OPENAI_COMPATIBLE_BASE_URL=https://provider.example/v1
JARVIS_MODEL_OPENAI_COMPATIBLE_API_KEY=replace-from-secret-environment
JARVIS_MODEL_OPENAI_COMPATIBLE_MODEL=provider-model-id
JARVIS_MODEL_ALLOW_REMOTE=true
JARVIS_MODEL_PRICING_JSON={"provider-model-id":{"input_per_million_usd":1.00,"output_per_million_usd":2.00}}
```

API keys use Pydantic `SecretStr`, never have a real default, and do not appear in provider repr,
settings repr, safe diagnostics, normalized errors, or logs. Recursive redaction covers key names
containing API key, authorization, token, secret, password, or credential. INFO logs contain IDs,
provider/model names, latency, and failure category but no message or generated content.

## Adding a provider

Implement the `ModelProvider` protocol, normally by extending `ProviderBase`; explicitly declare
stable name, type, locality, capabilities, and default model. Keep native payload mapping and
exception translation inside the adapter, implement a no-token health check, return only normalized
contracts, add mocked-network tests, and register it in `factory.py`. New providers must honor the
current phase gate. Do not infer capability from the model name or perform network activity during
construction.

## Limitations and deferred work

- Persistent task budget accounting across executions and processes is deferred.
- Streaming, tool calling, structured output, vision, custom organization/project options,
  advanced model scoring, aliases, and jitter are deferred.
- Provider health is queried for each routing decision; a future bounded cache may be appropriate.
- Pricing is operator configuration, not authoritative billing.
- Live built-in provider execution and network health require a future phase-policy change.
- OpenHands is a separate future specialist coding worker, not the default model-provider
  abstraction.
- Office visualization integration is outside this task.
