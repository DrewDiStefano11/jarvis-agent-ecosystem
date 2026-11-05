# Secure browser operation
Implement criterion C's authenticated browser workflow using existing native remote services. Independent branch at d8c9057. No public listener activation, production configuration or provider activation.

- RED integration tests: explicit browser opt-in, exact HTTPS host/origin, short-lived host-only HTTPOnly cookie, CSRF for all cookie-authenticated operations, login/restore/logout/expiry, credential ambiguity, native revocation.
- Add bounded in-memory transport sessions (not domain state), reset on API restart, immutable typed response contracts. Keep CLI bearer support and default browser rejection.
- Serve only explicitly configured trusted built remote HTML and hashed JS/CSS, behind existing gateway TLS/host/rate/body bounds plus CSP/no-store. Block local API and WebSocket routes.
- Build a separate remote web entry backed exclusively by apps/web/src/state. Native goal submit/list/inspect/graph/audit/result, cancel and emergency controls. Do not imply queued tasks execute without native authorization. Bounded visible refresh; no background inference or endless retry.
- Run adversarial HTTP and actual TLS/browser smoke, frontend tests and standard local checks. Independent review before publication. Retain full baseline evidence separately; do not claim permanent-host PASS.



Runtime/approval increment: connect existing versioned pause/resume/cancellation and inspected output; add typed remote read-only workspace preview and exact-hash reservation approval UI. Preserve uncertain native command replay while allowing explicitly refreshed intent after definitive version conflict. Bind approval callbacks to the review session/order. Test native hash/stop/revocation/schema fences and actual TLS browser controls with fixture-prepared worker acknowledgement; assert no worktree files are created. Broader source/development approvals and supervised TLS remain explicit next work.

Supervised TLS increment: keep one native application instance/lifespan and one owned API PID with two listeners. An internal listener-created ASGI marker permits loopback HTTP; the TLS listener always overwrites it and retains the dedicated gateway. Require explicit private bind, matching origin/port, certificate/key and verified trust. Strip remote credentials/flags from web and worker children, and disable remote API mode unless supervised TLS was explicitly selected. Pin credential-free TLS health checks to the owned bind IP, verify the origin certificate, and require the typed authentication rejection. TLS failure uses the existing API restart policy. Verify configuration rejection, listener isolation, single lifespan/graceful shutdown, trusted TLS and real child restart; do not activate host configuration.
