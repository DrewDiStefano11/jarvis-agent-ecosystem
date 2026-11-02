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
`JARVIS_MODEL_VERIFIED_CLOUD_ENABLED=true`. This work has not granted that approval
or verified the user's account. Keep Ollama disabled on this machine. Restart
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

Official Groq references: [model catalog](https://console.groq.com/docs/api-reference#models),
[billing](https://console.groq.com/docs/billing-faqs), and
[organization rate limits](https://console.groq.com/docs/rate-limits).
