# Internal retrieval contracts

This slice depends on `codex/research-retrieval-policy`. It defines immutable,
strict internal records in `app.models.research_retrieval`. There is no HTTP route,
model tool, provider call, persistence adapter or OpenAPI surface yet.

Operator limits bound redirects, response and header bytes, DNS answer count,
concurrent connections, DNS/connection timeouts and total duration. They are
separate from request fields; a request cannot add authority, headers, cookies,
private-address exceptions or increased limits.

Requests retain caller-generated request, task and runtime run identities plus
the exact source URL. Destination policy validates the URL without replacing its
original representation. Native admission must validate those identities and
RBAC, operator approval, leases and emergency stop before transport execution.
A structurally valid request is not proof of any of these permissions.

Successful metadata retains result/request/task/run identity, exact source URL,
normalized final URL, an aware retrieval timestamp, media type/encoding, SHA-256,
byte count, bounded redirect history and an artifact identity. Raw page content
is not a field in durable metadata. External content carries an explicit
`untrusted_external_content` trust tag that cannot be replaced with system policy.
Retrieval and failure timestamps require whole-minute timezone offsets so JSON
storage preserves the represented instant. Sub-minute offsets are rejected.

Redirect history must begin at the normalized source, remain contiguous, avoid
cycles and HTTPS downgrade, and finish at the final URL. Failure records permit
only a fixed code and identity/timestamp fields. They cannot carry remote error
messages, response headers, credentials, cookies or exception payloads.

The records validate structure and consistency, not truth: the native persistence
service must verify actual artifact bytes/digest/length, link identities to the
authoritative run, apply configured per-request limits and create append-only
audit/outbox events in the same transaction. Pending requests/results must survive
restart and uncertain acknowledgements. These guarantees are still pending.

Never log raw Pydantic exceptions, `errors()` input values, source URL queries,
remote text or chained resolver/transport exceptions. Public destination policy
cannot prove a query or path is secret-free. Native admission must reject sensitive
resources before storing exact provenance. Contract validation alone does not
make a URL safe for logging or an artifact safe for long-term retention.

Tests cover bounds, strict fields, immutability, timezone presence, contiguous
redirect chains, fabricated authority fields and sanitized failure structure.
No fixture result is described as actual research capability.
