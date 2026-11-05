# Runtime and local browser hardening

The local/cloud/hybrid source is frozen in the sibling hosted-coordination worktree
under its complete 4129-test run. This branch contains source only, with no copied
operator database or credential material. Keep inference/startup disabled.

1. Close the demonstrated local browser boundary gaps: direct loopback HTTP and
   WebSocket requests must validate Host and browser Origin before dispatch or
   snapshots. Preserve configured UI and CLI access, model-configuration errors,
   and the separately authenticated remote gateway. Add ASGI regressions first.
2. Add explicitly configured bounded autonomous concurrency while retaining a
   default of one, native worker registrations/leases, runtime checkpoints, budgets,
   emergency stop and graceful cancellation. Idle loops must make no model calls.
3. Review late planning-result persistence and native task fences; strengthen any
   demonstrated race without introducing alternate task state or model authority.
4. Run security review and all source-specific checks. Live inference and real
   generated-code execution remain subject to separate exact operator approval
   and the existing contained execution boundary. No push, merge or startup enable.
