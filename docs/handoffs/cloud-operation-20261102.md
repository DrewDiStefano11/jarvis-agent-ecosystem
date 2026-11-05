# Cloud operation — 2026-11-02

Branch `codex/cloud-operation-20261102`, base integration `1c0ea8e94254c89587b490e1301d0805b63dd9bd`. The separate operational-readiness worktree is frozen while its remaining full backend shards run (session 47488, `.local/exact-1c0ea8e`). Do not modify it or duplicate those suites.

Implement inline under the supplied campaign brief. First add hosted code proposals through existing Campaign D adapters: explicit default-off development selection, independently verified account/model/capabilities, current activation, a separate one-attempt router, durable shared quota, exact route digest in operator-approved admission, and rechecks before physical dispatch. Preserve historical limit serialization and local-only protections. Generic paid OpenAI and subscription development routes remain unavailable. No generated-code execution, source approval bypass, provider activation or external calls are authorized.

Next complete secure operator provider configuration/dashboard using existing settings/RBAC/runtime authority, then dashboard task acceptance. Recover remote browser implementation at `8fccb752a607f6f120b5a917097a498386380c7f` rather than rebuilding. Live inference requires separate activation and account-specific nonbillable evidence; validated candidate execution requires real containment, absent on this development host.

Verification: start with failing hosted route/capability/admission regressions; exercise real router HTTP through deterministic transport, durable quota and pre-dispatch revocation; preserve local/paid quarantine tests; extend policy source coverage and shard manifest; run relevant required checks before commit. No production inference success is claimed from fixtures.

Operator selected **Groq** for the first cloud-only acceptance test. No key has
been requested, supplied or inspected, and no provider has been activated.
Account-specific nonbillable evidence and separate activation approval remain
required. Finish a secure backend-controlled provider setup path before asking
for credential entry.

Implemented: separate verified hosted text-proposal router, declared text model
capabilities, native admission route digest, current credential/endpoint/account
and activation checks, one request/no fallback/no tools, durable quota and replay.
The initial native fixture passed admission/proposal/replay without source changes
(65.31 s). A coherent 153-case hosted/local/provider/policy package passed in
263.26 s before the review correction. These use fixtures, not real Groq inference.

Independent review found one compatibility issue: OpenRouter/Cloudflare model IDs
with `/` or `@` were rejected. RED tests reproduced it; both settings and admission
now permit bounded namespaced IDs only for explicitly approved hosted routes.
Local and historical unbound restrictions are unchanged. Two namespace regressions
pass; final hosted/native/local-controls package is running in session 46277.
Ruff format/lint passed for 550 files. Full collection before the namespace cases
was 3,982; final collection must account for the two added cases.

Separate integration commit 1c0ea8e: runtime 402 passed, autonomy 503 passed,
models 1,345 passed/3 explicit skips. System shard (1,713 cases) remains running
under session 47488 in the frozen operational-readiness worktree. Do not duplicate
its execution or infer a complete backend pass yet.

Final namespace correction verification: **57 passed, 2 deselected** in 72.28 s
(`.local/hosted-final.xml`/log). Includes all hosted regressions, native hosted
admission/replay, namespace admission/local rejection and fast local controls;
the two real local HTTP/source-mutation cases were already covered by the earlier
153-case package. The final package rechecks the credential/endpoint guards added
after the exploratory native fixture. Do not treat the deselections as a full
backend pass. Frontend is byte-identical to integration commit 1c0ea8e, whose
typecheck/ESLint/234 Vitest/build checks passed in this session; no frontend checks
were redundantly rerun for this backend-only change. No migration changes.

Groq primary documentation rechecked: billing-faqs distinguishes the Free tier
from paid Developer billing; rate-limits states limits are organization-wide and
the account console gives exact limits. Public docs do not prove this account's
tier or remaining nonbillable capacity. Sources:
https://console.groq.com/docs/billing-faqs and
https://console.groq.com/docs/rate-limits.
