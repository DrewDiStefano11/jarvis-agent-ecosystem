# Provider-neutral search foundations

This slice depends on the research destination policy. It adds strict immutable
discovery records, pure normalization and an adapter protocol. No concrete search
provider, credential setting, endpoint, model tool, network dispatch or autonomous
search loop is enabled. An empty provider installation cannot claim search works.

`SearchBudget` expresses operator bounds for queries, results per query, total
results, timeout, allowed provider keys and local/remote policy. Remote use is
disabled by default. The future native dispatcher must enforce those aggregate
budgets transactionally before every dispatch, count failed/uncertain attempts,
reuse durable completed results and apply native approval/RBAC/lease/stop controls.
Defining a budget record does not execute or authorize any provider request.

`SearchProvider` describes one adapter's stable key, explicit local/remote mode
and asynchronous bounded candidate result. Provider credentials belong inside a
trusted operator-configured adapter, never in a query, model context, candidate,
normalized lead, durable error or audit event. Adapters must bound response bytes,
parsing and result construction before normalization; the pure normalizer rejects
non-concrete iterables and candidate sequences over twenty rather than eagerly
consuming unbounded data. No provider fallback is expressed or authorized.

Normalized leads retain query, sequential rank, bounded title, normalized URL,
optional bounded licensed snippet, provider, aware retrieval timestamp, stable
result identity and snapshot digest. URLs pass destination policy and normalize
before deduplication. DNS safety remains a retrieval-time requirement. Stable
identity hashes query/provider/URL; snapshot digest additionally hashes rank,
title, snippet and retrieval time. Stored records revalidate both hashes, chain
their query/provider/rank/timestamp to the containing batch, and survive JSON
serialization/revalidation. Empty results still retain their retrieval timestamp.

Every lead is explicitly `discovery_lead` and `untrusted_external_content`.
Snippets can suggest where to look; they cannot become retrieved sources,
evidence, claims or verified conclusions. Important claims must later reference
actual retrieved source content and independently validated evidence. Provider
snippet retention must respect the provider's content terms and copyright limits;
adapters should omit snippets when retention is not permitted.

The normalizer does not invoke a provider, grant network authority, persist a
checkpoint or enforce a global query budget. Those are pending native integration
requirements, not advertised capabilities. Query/title/snippet/URL content can be
sensitive or adversarial. Do not log raw contract validation errors or input
values, and do not promote discovery text into trusted instructions.

Deterministic tests cover URL safety, normalization/deduplication, result bounds,
local/remote policy, control characters, immutable structural contracts, stable
identities, snapshot digests, tampering, JSON round trips and evidence/trust-tag
separation. No fixture provider is exposed as a product capability.
