# Hosted provider configuration

Open **AI providers** in the local dashboard. Choose **Configure and select model
operator** to create or reuse a narrowly scoped configuration identity. Existing
denials and inactive identities remain in force. Select the planning worker again
in Planning when operating tasks; the configuration identity has no runtime or
workspace-tool authority.

Groq is the selected first hosted acceptance target. Add an exact model, provider
priority and optional implemented text-category assignments. Enter the API key
only in the local password field, never in chat. Windows DPAPI protects stored key
bytes for the current Windows user; plaintext persistence is unavailable on other
platforms. A blank password preserves the current key. The remove checkbox clears
it. Keys clear from the form on submission and never enter shared state, browser
storage, response views, audit or outbox events.
The operator view exposes only the credential's SHA-256 fingerprint to bind
independently verified evidence without reopening or copying the secret key.

Saving a profile does not activate hosted inference, start a worker, register
startup or send a provider request. **Check Groq model access** explicitly makes
one authenticated GET to the official model-catalog endpoint. It reports catalog
access and whether the selected exact model was listed. It is not an inference
test or proof of nonbillable use. No automatic retry occurs after an interrupted
check. Credentials and provider response/error bodies are never displayed.

Before enabling a profile, attach current independently verified account/model
allowance JSON matching `FreeAllowance` in OpenAPI. Account IDs, exact models,
capabilities, credential fingerprint, quota window and evidence references must
correspond to the actual account. Public pricing pages, an API key, available quota
or a free label alone are insufficient. The account must prohibit billing, credit
consumption, automatic recharge and paid fallback. Reattach current evidence for
each enabled change. Never put API keys in the evidence file.

Separate owner approval is still required before setting
`JARVIS_MODEL_EXECUTION_MODE=free_only` and
`JARVIS_MODEL_VERIFIED_CLOUD_ENABLED=true`. A saved profile or completed acceptance
does not grant that approval. Keep Ollama disabled on this machine. Restart
API/worker/supervisor after configuration changes; saved keys and verified evidence
load from the native database, without exporting keys to environment variables.
Changed/disabled profiles immediately fence older provider instances. Explicit
task/development model choices take precedence over category defaults. Source/tool
permissions and exact development admission remain independently required.

The full aggregate startup Settings contract is checked before each save. Profile
names cannot shadow local or subscription integrations; account rotation and
overlarge combined priority/evidence configurations are rejected atomically.
Backups retain encrypted credential bytes; restoring under another Windows user
does not make those credentials usable. Migration downgrade refuses to discard a
populated profile store.

The dashboard does not yet change the global execution mode/activation flags.
Metadata checks for Gemini/OpenRouter/Cloudflare are not implemented. No live
provider acceptance, generated-code containment or 24/7 production readiness is
claimed by this setup feature.

## Groq first-request bootstrap

Use `openai/gpt-oss-120b` for the first model. Save its protected credential with
the profile disabled and the allowance upload empty. The separate **One-request
Groq acceptance** form accepts `GroqAccountEvidence`, whose exact JSON schema is
published in OpenAPI. It requires the canonical account ID, exact model, stored-key
fingerprint and confirmed key/account association; `plan: "Free"`; explicit
nonbillable/billing/credit/recharge flags; independent `rpm`, `rpd`, `tpm`, `tpd`
limits; timezone-aware `verified_at`/`valid_until`; and evidence references/digest.
Verification must be no older than fifteen minutes at each authorization fence. Do not supply guessed
remaining counts or reset times. Public rate limits do not establish the account's
Free plan or the saved key's association with it.

Preparation makes no provider request and grants no inference permission. Review
the exact fixed payload, endpoint, source policy digest, profile revision, request
limit, token reservation and five-minute plan expiry. Explicit approval of that
plan hash grants only one scoped request: POST to Groq's fixed chat-completions
endpoint, strict JSON `{"ok":true}`, at most 512 completion tokens, no tools, no
fallback, $0 paid ceiling. The disabled profile, cloud flag, worker state and
startup registration are unchanged. An atomic native dispatch intent precedes
HTTP; concurrent calls, replay, revocation and uncertain outcomes cannot send a
second request. An uncertain/failed result requires operator review.

A successful response initializes RPD/TPM tracking only from valid provider
response headers. Its first observed counts and reset times are visible in the
recorded result; neither unused RPM nor unused TPD is inferred. RPM/RPD/TPM/TPD
are enforced independently against durable application reservations; Groq remains
authoritative for usage from other clients. Missing/malformed/older headers,
uncertainty, clock rollback and conflicting native history fail closed. Concurrent
429 responses preserve the longest active Retry-After deadline, and Groq requests
are never transparently retried. Ordinary calls reserve conservative UTF-8 input
and framing bounds plus bounded completion tokens (default 512); known unused
tokens can be refunded without exceeding the latest applicable header snapshot.

Groq profile enablement additionally requires current native capacity and current
nonbillable account evidence. The legacy `FreeAllowance` upload does not bypass
the native ledger. Its old single quota-window expiry cannot refill or replace
the four-limit ledger. Global activation and each task's native runtime/workspace
permissions remain separately required. No live acceptance is claimed by
intercepted tests. Older valid profile/quota projections cannot roll back their
append-only native proof history, and emergency stop is rechecked before dispatch.

Official Groq references: [model catalog](https://console.groq.com/docs/api-reference#models),
[billing](https://console.groq.com/docs/billing-faqs), and
[organization rate limits](https://console.groq.com/docs/rate-limits).
