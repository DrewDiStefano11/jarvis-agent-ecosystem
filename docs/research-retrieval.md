# Research retrieval foundation

Research execution remains unavailable. This first slice of milestone 1 adds pure
URL and resolved-address policy in `app.research.url_policy`; it does not register
a tool, expose an endpoint, resolve DNS, fetch a page, or grant runtime authority.

Only HTTP/HTTPS on their default ports are accepted. Hosts normalize to lowercase
with a single trailing DNS dot removed. Paths and queries retain their original
encoding; URLs are bounded to 4096 ASCII bytes. Unicode paths must be explicitly
percent-encoded. Unicode hostnames must be supplied as ASCII IDNA labels.
Credentials, fragments, whitespace, controls, malformed escapes, backslashes,
ambiguous numeric hosts, scoped IPv6 and special local suffixes are rejected.
An accepted hostname is only a candidate; it is not an approved network target.

Every DNS answer must be a public unicast address. Empty, oversized (over 32),
malformed, private, reserved, multicast, metadata, shared-space and mixed-safe/unsafe
answers fail closed. IPv4-mapped IPv6, NAT64, 6to4, Teredo and other conservative
special-purpose exclusions are rejected consistently across Python versions.
No private-address exception is supported in this initial campaign.

Redirects are bounded to three by default (hard maximum five); every Location is
validated before joining with its base and the resulting URL is revalidated.
HTTPS-to-HTTP downgrades are rejected. The caller must validate DNS for each target
and must enforce the cumulative hop count. Cross-origin redirects grant no authority.

Errors contain fixed policy codes and a fixed message, never the URL, credentials,
resolver errors or response content. URL queries can themselves contain secrets;
acceptance by this pure policy does not make a URL suitable for durable logging.
Future admission must reject sensitive resources rather than recording their secrets.

## Required transport boundary

Do not connect with an ordinary client after validating DNS: the client could
resolve again and connect to a different address. A transport must pin the actual
connection to the validated address, preserve the original Host/TLS name and
certificate verification, and revalidate each new connection and redirect.
Disable proxies from the environment, ambient credentials, cookies, automatic
redirects and decompression. Bound DNS time, total time, response and header bytes,
connections and decoded content. Cancellation must close resources.

Before any model-facing execution is added, native operator opt-in, exact reviewed
scope, RBAC, leases/fencing, emergency stop, durable request/result identity,
transactional audit/outbox and recovery must be enforced. Existing workspace-only
tool authority does not authorize network access. External page text remains
untrusted content and cannot change destination policy or execution authority.

## Validation

Deterministic tests cover malicious URL forms, direct and mixed-DNS private/metadata
targets, answer budgets, public literal consistency, redirect limits, downgrade
rejection and page-derived policy bypass attempts. These are policy tests, not
end-to-end research or network retrieval acceptance evidence.

References: Python's [URL parsing security documentation](https://docs.python.org/3/library/urllib.parse.html#url-parsing-security)
and [address classification documentation](https://docs.python.org/3/library/ipaddress.html).
