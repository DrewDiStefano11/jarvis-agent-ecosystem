# Campaign D activation and acceptance gates

All six cloud integrations are **disabled and unverified**. This checklist does
not authorize sign-in, inference, provisioning, billing changes or resource creation.
Implementation acceptance, local validation and live account acceptance are
separate gates. Tests use synthetic identities and mocks, never billing evidence.

## Private evidence record

The account owner and an independent verifier must capture these items in a
protected evidence store outside Git. Publish only a redacted reference/status.

1. Provider, canonical account/project/workspace identifier, authenticated session
   identity and the exact shared quota bucket. Do not publish email, tokens or keys.
2. Account-specific free/included entitlement and exact model identifiers, with
   timestamped console evidence and current official billing/limit documentation.
3. Whether a payment method is required or attached, the actual billing tier,
   purchased-credit eligibility, automatic recharge and paid fallback settings.
   Merely having an existing subscription is insufficient.
4. Provider-enforced rejection of billable operations, including concurrent and
   in-flight requests. Alerts and locally estimated costs are insufficient. If
   the boundary cannot be enforced, classify the integration as **unsuitable for
   guaranteed-zero-cost operation** and leave it disabled.
5. Requests, input/output tokens, shared daily/weekly limits, actual window/reset,
   and any compute, runtime, storage or bandwidth limits. Application request
   counters do not represent every provider meter or use by other clients.
6. Exact credential/account association and minimum permissions. Hosted APIs bind
   evidence to a locally computed SHA-256 key fingerprint; OAuth/native sessions
   bind to the authenticated identity. Never paste credentials into evidence JSON,
   prompts, PRs, logs or frontend state.
7. Independent verifier identity, evidence reference, verification timestamp,
   expiry, conservative remaining request count and account-owner approval of
   the exact integration/models/bounds. `FreeAllowance` is a trusted operator
   record, not an automated proof of the attached evidence.

No complete account evidence exists for this campaign. Missing, ambiguous, stale,
or unverifiable evidence leaves the provider disabled. Do not alter billing or
subscription settings as part of this checklist; report required owner action.

## Provider-specific owner checks

| Integration | Owner inspection and enforceable boundary required | Limits, permissions and excluded operations |
| --- | --- | --- |
| Gemini compatible API | In AI Studio inspect the key's actual project, free entitlement and billing link. Independently establish absence of chargeable billing/prepay/trial-credit routing; reject a paid project. | Record account/model RPM, TPM, RPD and reset from the actual limits console. Restrict the key to the intended API/project where supported. Exclude Vertex AI, grounding, batch/file storage and other services; this adapter implements chat completion only. |
| Groq compatible API | Inspect the organization Billing page and confirm actual Free tier, with no Developer/Enterprise usage billing or automatic upgrade. Payment information for a paid upgrade does not prove current free eligibility. | Record per-model request/token windows from the organization limits page. Use an organization-bound inference key. Exclude paid service tiers, batch processing, training and storage. |
| OpenRouter compatible API | Inspect account credit/recharge state and key permissions. Require exact `:free` models and evidence that requests cannot route to paid models or paid BYOK paths. Reject credit-enabled or fee-bearing configurations. | Record current per-minute/daily free limits and reset; they depend on account state. Use a dedicated restricted key where supported. No purchased credits, BYOK, paid routes, web search or paid transforms. |
| Cloudflare Workers AI API | Inspect the exact account's Workers subscription and AI Gateway billing. Require Workers Free and rejection above free allocation without paid-plan or prepaid-credit routing. Any paid-only model is unsuitable. | Official pricing currently describes a daily 10,000-neuron free allocation resetting at 00:00 UTC; verify actual model availability and account limits. Token must permit Workers AI inference only for this account. No Worker deployment, R2, KV, gateway unified billing, storage, paid model or other resource provisioning. |
| ChatGPT OAuth Responses | Inspect the actual ChatGPT account/workspace's included allowance, purchased-credit use and automatic reload. Require an enforceable included-only boundary for the exact OAuth resource/model. If the provider cannot enforce credit exclusion, classify as unsuitable. | Existing subscription payment is separate from inference-credit authorization. Record shared included usage/reset and model access. Only official sign-in with Responses scopes; identity-only consent cannot infer. No paid API keys, tools, stored responses, consumer backend or cookie reuse. |
| Native Codex app-server | Inspect the CLI's existing ChatGPT login and the same account's usage/billing controls. Require included-only use and disabled purchased-credit consumption/recharge. A read-only quota response is an additional check, not billing proof. | Record exact `limitId`, all applicable usage windows and account association. Adapter rejects unknown or positive credit status, unknown plans, API-key login and custom providers. No native tools, workspace access, hooks, MCP/apps/plugins, billing or credit RPC. |

Public references checked on 2026-10-13; these do not verify an account:
[Gemini billing](https://ai.google.dev/gemini-api/docs/billing),
[Gemini limits](https://ai.google.dev/gemini-api/docs/rate-limits),
[Groq billing](https://console.groq.com/docs/billing-faqs),
[Groq limits](https://console.groq.com/docs/rate-limits),
[OpenRouter limits](https://openrouter.ai/docs/api_reference/limits),
[OpenRouter support](https://openrouter.ai/support/),
[Cloudflare pricing](https://developers.cloudflare.com/workers-ai/platform/pricing/),
[OpenAI subscription pricing](https://learn.chatgpt.com/docs/pricing), and
[OAuth preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations).

Gemini CLI, Antigravity and Jules are discovery-only. NVIDIA NIM, Mistral,
Hugging Face and other providers have no execution adapter in this change; they
remain disabled and need separate integration/security review. Ollama requires a
trusted loopback runtime and locally resident models; cloud model variants are
excluded in `free_only`. Never download or provision hardware automatically.

## Future activation procedure (requires separate explicit approval)

1. Complete private account evidence and independent verification above. Owner
   approves one integration at a time, exact models, capacity and a bounded test.
2. Keep execution disabled while installing protected credentials and validating
   the allowance schema, endpoint, fingerprint/session identity and migration.
   Apply Alembic through the normal deployment process; never startup `create_all`.
3. Only after separate owner activation approval set `free_only` with that verified
   allowance and `JARVIS_MODEL_VERIFIED_CLOUD_ENABLED=true`. Records alone cannot
   enable hosted routing; the default switch is `false`. Worker enablement,
   task queuing, RBAC and exact-plan workspace approvals remain separate controls.
4. Run a single minimal text inference with no tools or sensitive context. Compare
   native usage/billing before and after: no credit use, no charge and expected
   quota debit. Record real request evidence privately, never token values.
5. Independently confirm exhaustion, expired evidence, missing/wrong credentials,
   revoked permission, shared-bucket depletion, restart recovery and bounded
   fallback. Exercise exhaustion locally or with a provider-supported nonbillable
   mechanism; never burn an entire allowance to demonstrate it.
6. Confirm emergency stop and policy revocation prevent further dispatch. Check
   durable pause/checkpoint/audit/outbox and operator-controlled recovery. Inspect
   unknown in-flight outcomes before resuming; never refund uncertain reservations.
7. Re-disable execution after the bounded test. Live acceptance may be signed off
   only with genuine independent evidence for each tested account/integration.

Emergency disable: use the existing emergency stop, set
`JARVIS_MODEL_VERIFIED_CLOUD_ENABLED=false` and execution mode `disabled`,
remove the affected allowance and stop/restart the worker through the normal
operator procedure. Existing provider objects retain configuration until restarted.
Revoke a compromised credential through the owner; no billing changes are needed.
An in-flight request may finish, so the verified provider boundary must cover it.

## GitHub publication boundary

The repository is public and uses standard runners. GitHub documents free standard
runner execution, but artifact/cache storage has separate billing considerations.
Account-specific artifact storage safeguards are **unverified**. Remote Actions CI
must remain skipped for this branch/PR, including applicable triggers, until the
owner independently verifies an enforceable $0 boundary. No paid review is requested.
Local validation and a draft PR do not constitute remote CI acceptance.
[GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions).

## Narrow follow-up engineering tasks

- Add read-only account evidence collectors only where official APIs expose a
  sufficient nonbillable boundary, without granting activation authority.
- Add verifier-backed evidence revocation/expiry monitoring and redacted quota
  observability; maintain manual activation and recovery.
- Test supported CLI/OAuth protocol versions against genuine nonbillable accounts
  only after the corresponding gates pass. Reject unsupported versions safely.
