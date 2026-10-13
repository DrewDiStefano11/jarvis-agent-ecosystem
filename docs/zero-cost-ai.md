# Campaign D: zero additional AI spending

The paid-inference authorization ceiling is permanently **$0**. There is no paid
execution mode, paid fallback, billing activation, credit purchase, upgrade, or
recharge operation. Advertised free tiers, local zero-price entries, trial credit
balances, API keys, and unknown quota estimates never authorize spending.

See the [activation and acceptance checklist](campaign-d-activation.md) for
provider-specific independent evidence, human gates and safe shutdown procedures.

## Deployment state and discovery

Execution stays disabled by default. No operator cloud account has been verified
for this campaign. Live acceptance is pending account-specific verification;
automated acceptance uses mock HTTP/JSON-RPC, signed OAuth fixtures and isolated
databases. A mock completion does not establish live access.

Run `.venv/Scripts/python.exe scripts/ai-capacity-status.py` for read-only discovery.
Installed executables are not entitlements. Gemini CLI, Antigravity, and Jules are
discovery-only; no execution adapter is claimed. Implemented inference routes are
Codex app-server, ChatGPT OAuth Responses, Gemini compatible HTTP, Groq, exact
OpenRouter `:free` models, Cloudflare Workers AI compatible HTTP, and optional
loopback Ollama. No large locally running model is required for hosted planning.

## Operator-owned eligibility

`JARVIS_MODEL_EXECUTION_MODE` accepts `disabled`, legacy `local_only`, or explicit
`free_only`. `JARVIS_MODEL_FREE_ALLOWANCES_JSON` defaults to `[]`. Only trusted
operator configuration can establish `app.models.ai_capacity.FreeAllowance`
records. Model text, manifests, and planning HTTP requests cannot establish them.
Each record requires a provider name, supported integration, exact models,
credential environment-variable name, canonical actual account ID/shared bucket,
account-specific evidence reference, verification expiry, quota window/reset,
remaining request count, and these verified provider-enforced safeguards:

```json
{
  "nonbillable": true,
  "billing_enabled": false,
  "automatic_recharge": false,
  "credit_consumption": false
}
```

This fragment is not a usable allowance. `billing_enabled` means usage-based
inference billing, not the existing prepaid subscription. If included-only usage
cannot be enforced without purchased credits, leave the provider absent.
Credentials belong in protected local storage/environment, never allowance JSON.
Hosted API evidence also requires `credential_fingerprint` (SHA-256 of the verified
key); changing credentials invalidates evidence before client creation.

Use the same canonical `account_id` and `shared_bucket` across integrations sharing
an allowance. `session_identity` binds the integration's authenticated identity
(native Codex email or OAuth subject) without creating another bucket. Verify its
association with the actual account/workspace. Never rotate accounts to bypass
limits. Distinct legitimately authorized providers may use their own allowances.
Configuration permits one canonical account per provider family, grouping Codex
and ChatGPT together, so automatic fallback cannot rotate accounts within a provider.

Gemini evidence must cover the key's actual project and absence of usage billing;
Groq must remain on its actual free tier. OpenRouter requires exact `:free` models
without paid routing, BYOK fees or recharge. Cloudflare requires a nonbillable
account boundary: request caps cannot prevent overage charges on a paid account.
These are verification criteria, not claims about the operator's current accounts.
See [Gemini billing](https://ai.google.dev/gemini-api/docs/billing/),
[Groq billing](https://console.groq.com/docs/billing-faqs), and
[OpenRouter support](https://openrouter.ai/support/).

Hosted endpoint origins/paths are fixed by integration. `free_only` never enables
generic remote API-key endpoints, ordinary paid OpenAI inference, consumer
ChatGPT backends, or browser-cookie reuse. Legacy loopback contracts remain intact.

## Supported subscription integration

The ChatGPT public-client flow uses dynamic registration, a stable host ID,
loopback callback, fresh state/nonce, PKCE, issued client ID, signature-verified
OIDC identity and explicitly granted Responses permission. Identity-only consent
cannot infer. Run `scripts/chatgpt-signin.py` to authorize, or `--refresh` to renew;
these commands make no inference request and never enable execution.

Sessions are stored atomically under ignored `.local/chatgpt/`, using user-bound
DPAPI encryption on Windows and owner-only files on Unix. An exclusive lock
serializes token rotation. A crash-stale lock needs operator inspection/removal.
Tokens are excluded from diagnostics. A ChatGPT allowance's `credential_env`
names an environment variable pointing to this protected file. The authenticated
subject must match `session_identity` or canonical account ID. Expired sessions
disable inference until renewed. The Responses adapter uses OAuth at the public
API, collects SSE and accepts only completed output, while runtime token caps,
deadlines and canonical output validation remain authoritative.

Sources: [sign-in contract](https://developers.openai.com/siwc/token-sharing-open-source/sign-in),
[inference contract](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference),
[preview requirements](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations),
[renewal and credential security](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions).

Native Codex uses the existing ChatGPT-authenticated CLI through app-server stdio,
checks the account and selected allowance, and rejects API-key/custom-provider
login, unknown/exhausted quotas, inherited integrations/hooks and tool authority.
It disables command tools, starts outside the workspace in a temporary read-only
directory, and makes no credit/billing RPC. Incompatible effective configuration
fails closed. Its credential environment-variable field is not used for an API key.
[Official app-server reference](https://learn.chatgpt.com/docs/app-server).

Both routes additionally require verified prevention of purchased-credit usage;
OAuth success and model catalogs are insufficient. No account currently passes
this campaign's verification.

## Switching and recovery

Alembic `20261013_free_ai` adds `free_ai_quotas`; startup never uses `create_all`.
Every inference attempt atomically reserves shared account capacity. Failed,
cancelled or uncertain attempts are not refunded. Router recreation and model
changes cannot refill the bucket. A hard quota signal disables all entries sharing
it. A new window needs fresh verified evidence rather than an assumed reset.
Startup/reload initialization is atomic and cannot refund concurrent reservations;
a new window label cannot refill capacity before the stored actual reset boundary.

The router filters eligibility before health/model traffic and rechecks capacity
before dispatch. It skips entries whose shared account just exhausted and selects
the next eligible provider within existing request/fallback bounds. Explicit
model names are preserved; omit a global model name to use each provider's verified
default. Adjust `JARVIS_MODEL_PROVIDER_PRIORITY` and `JARVIS_MODEL_PREFER_LOCAL`
for task suitability, preserving included Codex for complex coding and selecting
qualified smaller/faster free models for simple work. These preferences never
override capability or eligibility checks.

Each worker physical dispatch rechecks RBAC, current lease and emergency stop,
persists request evidence and the existing required checkpoint, and consumes the
durable request limit. Switching retains context, source changes and execution
evidence. It grants no source-edit, shell, external integration or workspace-tool
authority; exact approved plan hashes and marked scopes still govern existing tools.
Independent critics can use verified hosted capacity.

No verified capacity produces the existing safe runtime pause and task review
state with `free_capacity_unavailable`, a recovery checkpoint, and no provider
request. Native operator-authorized recovery after allowance verification remains
required. No automatic resume, duplicate uncertain dispatch, purchase prompt or
paid fallback is introduced.

`tests/test_free_ai_capacity.py` covers four mocked HTTP routes, subscription
Responses/Codex, OAuth and credential safety, persistent shared quotas, concurrent
reservations, restart/exhaustion, bounded switching, and durable worker completion
with hosted mocks or pause without verified capacity. Live acceptance is deferred.
