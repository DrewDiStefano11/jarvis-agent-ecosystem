# Authenticated browser operation

This adds a separately enabled browser entry at `/remote` to the existing direct-TLS remote listener. It uses the existing native remote services and transaction checks. It does not expose the local Mission Control API or enable execution.

## Deployment preparation

Keep the remote listener disabled while preparing a trusted web build:

```powershell
pnpm --dir apps/web install --frozen-lockfile
pnpm --dir apps/web build
```

Use the existing [remote operator configuration](remote-operation.md), including an active operator, strong randomly generated credential, exact HTTPS origin, an existing certificate/private key and native permissions. Full browser monitoring needs `remote.read` and `remote.runtime`; submission needs `remote.submit`; cancellation/system controls need `remote.control` and the separate native task/system permissions. Each operation reuses existing checks. The application never grants these permissions during login.

For an explicitly authorized deployment, set `JARVIS_REMOTE_BROWSER_ENABLED=true` and `JARVIS_REMOTE_BROWSER_ASSETS` to the absolute trusted `apps/web/dist` directory. Browser activation requires existing remote TLS configuration and built `remote.html`. Restart the dedicated remote listener, then open the configured HTTPS origin followed by `/remote`. The default listener binding remains `127.0.0.1`.

Use an approved private network and a certificate trusted by the operator's devices. No VPN installation, DNS provisioning, public binding, firewall opening or production credential issuance is performed by this feature. Reverse-proxy TLS assertions remain unsupported.

For supervision, explicitly set `JARVIS_SUPERVISOR_REMOTE_ENABLED=true` alongside the existing remote activation. Set `JARVIS_SUPERVISOR_REMOTE_CERTIFICATE` and `JARVIS_SUPERVISOR_REMOTE_PRIVATE_KEY` to existing absolute files. `JARVIS_SUPERVISOR_REMOTE_HOST` defaults to `127.0.0.1`; only an explicit private or loopback IP is accepted. The TLS port comes from `JARVIS_REMOTE_ORIGIN` (443 if omitted), must differ from the local API/web ports, and must match `JARVIS_SUPERVISOR_REMOTE_PORT` if supplied. For a private CA, set `JARVIS_SUPERVISOR_REMOTE_CA_FILE` to its absolute certificate file; otherwise system trust is required. Invalid certificates, keys and trust configuration reject startup. No configuration is enabled by this change.

The existing supervised API PID serves both loopback HTTP and dedicated TLS with one application instance and one lifespan. Workflow recovery, emergency stop, outbox and Office state stay shared. The loopback listener retains local peer checks and Office reconciliation; remote routes and sign-in remain HTTPS-only. Web and worker children receive disabled remote flags and an empty remote credential. When supervised TLS is off, the supervised API also receives those overrides, preventing inherited standalone settings from converting its HTTP listener into remote mode.

Health checks pin the connection to the configured bind IP, verify the origin hostname and certificate, check owned port/process identity, and require a typed authentication rejection without sending an operator credential. Typed rate-limit saturation is available/degraded and cannot force API restarts. Other TLS failures use the existing API restart/backoff and stop policy. Restart clears browser sessions; durable domain records remain authoritative.

## Sessions and credentials

Enter the configured operator credential only on the trusted HTTPS page. It is sent once in the login Authorization header, cleared from the form immediately, and never written to localStorage, sessionStorage or a URL. The client uses same-origin fetch, rejects redirects, disables response caching and bounds each request to ten seconds.

The server issues an opaque random `__Host-jarvis-session` cookie: Secure, HTTPOnly, SameSite=Strict, Path=/, no Domain, maximum age 900 seconds. The server stores only a digest of its value; the bounded session table has at most 32 entries. A separate unpredictable CSRF value is retained in client memory and required on every cookie-authenticated API request, including reads. Login and session restoration require the exact canonical configured Origin. Restoration additionally requires its custom header. Cross-site fetch metadata and duplicate credentials/cookies/CSRF headers are rejected.

Sessions expire after 15 minutes absolutely or five idle minutes. Activity never extends the absolute deadline. Logout revokes the current opaque value. All sessions are invalidated by an API restart; this deliberately supports the existing single-process remote deployment. Re-authentication and permissions use the current native identity. Session transport never approves a source plan, workspace operation, model provider or runtime completion.

Browser cookie attributes follow [MDN's cookie guidance](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Set-Cookie); the response implementation uses [Starlette's cookie API](https://starlette.dev/responses/).

## Operational interface

The remote entry provides operator sign-in/out and session restoration, native goal submission, paged accessible goals, recorded task status/output/decomposition/recent audit, active identity names, system recovery status, goal cancellation, native runtime request-pause/resume/cancellation, inspected run results, exact-plan workspace reservation review/approval, and emergency stop/resume. Confirmation precedes each control. Submission records a native queued goal; it does not assert that execution was authorized or completed.

Data lives in `apps/web/src/state/RemoteOperatorStore.tsx`; pages retain only form drafts and presentation state. No local AppStore, Office, local WebSocket or local-only page is mounted by this separate entry. It shares the existing web build and styling conventions. Dynamic output is rendered as text.

Visible connected pages refresh every 15 seconds. Errors stop periodic refresh until an operator refresh or valid operation restores state. Denied optional system/agent views remain unavailable while accessible goals can still be inspected. Polling makes no inference calls. The UI distinguishes missing results and queued work from completed deliverables.

The same form reuses its idempotency key after an uncertain submission acknowledgement. This key is held in memory: after a page reload, inspect existing goals before submitting again. Cookie-changing login/logout transitions are serialized. Session-generation and read-order guards discard late responses from older sessions or reads; current control failures invalidate reads that could otherwise erase the failure.

Runtime controls bind the displayed run version, a stable command ID and timestamp. A lost acknowledgement preserves the exact native request for explicit replay. A definitive native version conflict clears that rejected intent; refresh and confirm a new request against current state. The browser cannot confirm pause/cancellation, complete a run or fabricate a worker checkpoint. Visible run lists show up to 20 accessible runs for the selected goal or global view; they are not complete fleet totals.

Workspace review uses POST `/api/remote/self-build/workspaces/preview` with the registered repository ID, runtime run ID and exact base SHA. The server derives the existing native `WorkspacePlan` under remote/task read permissions. The operator reviews every field and retypes its exact hash before POST to the existing approval route. Approval lasts 900 seconds and retains independent actor, native execute/workspace permissions, configuration/hash and emergency-stop checks. It creates only the native approval record, not a worktree, source change, Git action or execution grant. New previews and session changes invalidate older review callbacks. Unknown approval outcomes require audit inspection and fresh preview; approval is never retried automatically.

Only the explicitly configured built remote HTML and existing hashed JavaScript/CSS assets are served. No directory listing, local index, service worker, docs, local APIs or WebSockets are exposed. TLS/Host/rate/body bounds remain outside CORS. No-store, restrictive CSP, frame exclusion, no-referrer and nosniff headers apply.

## Verification and remaining work

The four reserved built-in task IDs from the native seed are labeled **Seeded demonstration** in the goal list. Their evidence view explicitly identifies their example results. Operator-submitted goals have generated IDs and keep their normal recorded status; completion alone does not establish accepted output provenance. This presentation label does not change native tasks, permissions, recovery or scheduling.

Run `python scripts/remote_browser_acceptance.py -q` after building the web UI and installing the repository's existing development dependencies/Playwright browser. It uses isolated temporary databases, fixture credentials/certificates, an actual loopback TLS API process, independently CA-verified HTTP, and Chromium. It exercises login, native submission, restored session, native stop/resume/cancel, runtime resume/request-pause/result inspection, exact-hash workspace approval without filesystem effects, logout, credential storage protections, local route isolation and desktop/mobile layouts. Chromium accepts only the test's self-signed certificate in its isolated context; this is not proof of production certificate trust. The existing runtime-browser Actions job runs the same acceptance after building the UI.

The application-side remote experience remains partial: other development/source exact-plan approval workflows still use their existing authenticated API/native contracts. Actual separate-device/private-network acceptance, production certificate trust, operator role configuration and sustained host operation require the permanent deployment. Do not describe a browser goal record as autonomous completion or report criterion C as production PASS from fixture evidence.
