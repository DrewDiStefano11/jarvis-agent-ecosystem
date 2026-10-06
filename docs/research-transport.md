# Internal read-only research transport

This slice depends on the destination-policy and retrieval-contract branches.
`app.research_transport.ReadOnlyTransport` is an internal adapter, disabled by
default with an empty origin allowlist. No endpoint, runtime execution type,
browser session or model tool exposes it. Native admission and persistence are
required before it can become a Jarvis research capability.

Trusted operator configuration selects exact normalized origins and bounded
limits. The transport performs GET only, never accepts caller-supplied headers,
uses HTTP/1.1 with no connection retries, and checks origin scope on every hop.
Each request shares an instance-level connection semaphore (default two, maximum
four). The total timeout includes waiting for that semaphore and every redirect.
The native worker must reuse one bounded transport instance rather than creating
one per model action, and apply total query/source/request budgets above it.

The HTTPCore network-backend interface is used to resolve a hostname under a DNS
deadline, reject the entire DNS answer set if unsafe, and connect to a canonical
numeric IP. The connected peer must match the chosen address and port before
TLS or HTTP bytes are sent. The original hostname remains the Host header and
TLS certificate-verification name. A new pool for each hop prevents stale
connections and accidental reuse across origins. There is no hostname resolution
by the HTTP layer after validation. The connector and resolver are internal
test seams, never fields in a model plan or request.

HTTPCore does not implicitly consume environment proxy configuration, cookies,
browser profiles, credentials, redirects or decompression. Fixed outbound headers
request identity encoding. Set-Cookie is ignored. Redirect bodies and remote
error bodies are not retained. Redirect targets are normalized, revalidated,
scoped, cycle-checked and bounded before the next connection.

Response headers are bounded before the HTTP parser sees them. Unsolicited
interim/upgrade responses are rejected. Wire bytes have an independent bound
(body budget plus header budget plus 4096 bytes for transfer framing), so an
endless stream of tiny chunk frames cannot bypass the decoded body budget.
Only status 200 and textual MIME types (plain text, HTML, XHTML) are accepted.
Compression and unknown/ambiguous charset parameters are rejected. UTF-8 or
ASCII decoding must be valid; binary control bytes are rejected. Both declared
and streamed body size are bounded. All response/pool contexts close on failure
or cancellation. Caller cancellation propagates as `CancelledError` so the native
runtime can record its authoritative cancellation state.

Returned `TransportText` contains exact source URL, normalized final URL, aware
retrieval timestamp, media type/encoding, bounded bytes, SHA-256 and redirect
history. It is ephemeral untrusted content, not an independently verified claim,
durable artifact or evidence of successful end-to-end research. Parsing visible
page text, durable provenance and claim grounding are later stages.

Policy/transport errors have fixed codes and messages. Network exception chains
are suppressed so resolver error text cannot leak through a formatted traceback.
Do not log raw input URLs or remote bodies. Sensitive resources must be rejected
by native admission before retaining exact provenance or copied content.

## Native integration requirements

Use the existing runtime worker/lease/checkpoint authority instead of a second
execution engine. Admit an immutable operator-reviewed plan and bounded origin
scope under native RBAC; model text must never approve it. Persist a request
intent before dispatch with request/run identity and exact scope hash. Check
emergency stop, identity state, fencing and cancellation before every operation
and during waits; revoked authority must cancel the active transport task.

Persist content in a bounded artifact with matching digest and provenance, and
append audit/outbox entries within the authoritative transaction. A committed
result must be reused after restart. An in-flight operation with uncertain
completion must enter explicit recovery rather than be silently replayed.
Configure content retention separately from model interpretation and verification.
Until these requirements are implemented, do not add the transport to model tools
or describe Jarvis as having autonomous network research capability.

## Deterministic evidence

Tests exercise the real HTTP parsing/pool path over instrumented fake streams:
numeric-IP connection, original Host/TLS name, peer mismatch before request,
mixed private DNS, redirect re-resolution and scope, cookie isolation, cycle and
hop limits, giant headers/body, invalid text, compression, truncated/chunked
responses, DNS timeout, connection concurrency, cancellation cleanup, sanitized
error tracebacks and prompt-injection bytes treated only as source content.
They do not contact the public internet or prove native admission/recovery.

A separate operator smoke test on 2026-10-06 at 14:00:07 UTC performed one actual
GET of `https://example.com/` with the default resolver, connector and verified
TLS. It returned 577 bytes of HTML with SHA-256
`25ddf2c883e0d1958ea971d279a7e4f0fd446724ee3db7db19dadabd4a62e484`.
Only provenance metadata was printed; the page was not retained in the repository.
This proves one real transport retrieval, not autonomous goal execution or durable
provenance/recovery. Public-network behavior remains outside deterministic tests.

References: [HTTPCore network backends](https://www.encode.io/httpcore/network-backends/)
and [asynchronous streaming API](https://www.encode.io/httpcore/async/).
