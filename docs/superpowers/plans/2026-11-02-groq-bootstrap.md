# Groq single-request acceptance and independent rate limits

User instruction: implement a separately approved single request on the verified
Personal Free account, preserve $0/nonbillable boundaries, never enable ongoing
inference, then capture real response quota evidence. No live request approved yet.

1. Implement four independent durable limits: rolling RPM/RPD and TPM/TPD,
   conservative in-flight token reservations, authoritative response-header clamps
   for RPD/TPM, reset deadlines, bounded 429 pause, no automatic retry. Unknown
   provider history must be labeled; local accounting never claims other clients'
   complete RPM/TPD history. Missing/malformed headers fail closed.
2. Persist an immutable exact request/evidence/profile binding and expiring plan
   hash. Separate native operator permission and explicit approval. Commit one
   dispatch intent before networking; crash/timeout/duplicate/concurrent execute
   cannot issue a second request. Account/model scope cannot reset via aliases.
3. Fixed Groq origin, explicit tiny JSON acceptance payload, one request, no tools,
   no fallback/retry, bounded output/response/time; Windows protected credential
   only inside backend. Audit/outbox contains hashes/status only. Normal activation
   and worker settings remain false; successful bootstrap grants no ongoing access.
4. Wire the rate limiter into each ordinary Groq dispatch and expose safe remaining
   capacity/errors/reset state in the shared dashboard store. Preserve legacy
   contracts for other integrations. Update authoritative models/migrations/docs.
5. Close hosted-only assumptions in decomposition/coordinator/verifier and preserve
   live activation, durable request budgets, leases, checkpoints and emergency stop.
6. Security review, full source-specific checks, then prepare the exact live request
   for owner approval. Only after explicit approval demonstrate the requested real
   task lifecycle, recovery and graceful shutdown. No permanent startup or merge.

The provider-setup baseline is committed as 8586920 and passed its complete 4045-test
collection (4041 passed, 4 environment/opt-in skips). This follow-up passed its own
fresh 4081-test collection (4077 passed, 4 skips), with exact unique coverage and
unchanged source. The remaining local/cloud/hybrid workflow work is isolated in
hosted-coordination-20261102 and requires its own final validation. No worktree
copied the operator runtime database or credential material.
