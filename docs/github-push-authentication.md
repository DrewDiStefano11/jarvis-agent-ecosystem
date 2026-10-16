# Guarded GitHub HTTPS primitive

`github_publication/git_https.py` is a private host boundary for one smart-HTTP
advertisement or receive-pack operation. It is not a publication service, public
route, validation receipt or task-level operator grant. Production activation
remains unavailable.

The host supplies the configured repository numeric identity, fixed GitHub
owner/name, complete positive publisher identities, external credential broker
and a synchronous live publication fence. Each RPC resolves fresh credentials,
checks the repository and authenticated user with that credential, and confines
Git traffic to its configured `github.com` repository. Redirects, environment
proxies, cookies and credential-bearing URLs are unavailable. Credentials remain
inside the host transport and sanitized errors omit provider/request diagnostics.

This version supports restricted user tokens whose `/user` identity is among
the configured publishers. Installation tokens do not provide this user identity
and are refused; GitHub App support requires a separate protected installation
principal binding. Token provisioning and permission restriction remain operator
deployment responsibilities. No credential is provisioned by this milestone.

An RPC checks the live fence before credential access, before network operations,
during response consumption, every 200 milliseconds while waiting, and before
returning. Responses are bounded to one MiB and the RPC deadline is 30 seconds.
Cancellation or an uncertain POST result is not success evidence. Rate-limit
delays are bounded to one day and returned for durable orchestration; no retry
loop runs here.

The push contains one non-deleting generated feature ref and a bounded PACK v2
payload. The fence receives the complete request SHA-256. The composed service
must bind that digest to protected native commit/checkpoint evidence, fresh exact
committed-state Campaign B validation, source pins, the operator's persisted
publication scope, and current runtime/lease/emergency-stop authority. Merely
supplying a callback does not implement those checks. This primitive must remain
private until that composition exists.

The protected caller must reconcile the actual remote ref before retrying an
ambiguous write. `branch_publication.py` performs one comparison/reconciliation
step and requires a durable request-bound intent before either POST or adoption.
It refuses foreign/concurrently changed refs and reuses an exact previously
intended remote commit without sending a second POST.

`publisher_api.py` reconciles drafts by configured repository, base, branch,
exact commit, publisher identity and stable operation marker. Lost creation
acknowledgement is recovered by listing existing PRs; duplicate, foreign, closed,
merged or non-draft PRs refuse without replacement. A fresh independent PR read
verifies each creation/update acknowledgement. Its internal API permits only
bounded branch/base reads and draft PR metadata operations. Arbitrary writes and
merge endpoints are refused before credential access.

These primitives do not implement a task-level publication journal, genuine B
validation consumer, correction routing or final readiness. Those belong to the
subsequent integration increment. No automatic merge or force push is provided.

Mock-transport tests verify host confinement, credential rotation, revoked-fence
cancellation, exact request digest, publisher/repository identity, bounded
responses, media types, rate limits and sanitized authentication/transport errors.
They do not prove authenticated end-to-end Jarvis publication acceptance.
