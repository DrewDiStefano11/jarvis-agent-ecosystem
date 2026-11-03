# Runtime hardening and native workflow validation

This branch preserves local Ollama and approved hosted/hybrid planning. It adds:

- Direct local HTTP/WebSocket Host and browser Origin checks, preserving configured
  UI/CLI access, provider-configuration errors and the remote gateway boundary.
- Explicit sidecar concurrency of 1–4, default 1, with distinct native workers,
  task leases and cancellation awaited before worker shutdown.
- Team-result authorization inside its native task/outbox transaction. Proposed
  cloud-capable selections use a copy; context assembly reloads native task state
  after refusal, preventing rejected selections from leaking into context commits.

Read-only security review found the late context persistence escape; the fix and
endpoint regression were reviewed with no remaining actionable finding. Review
also found no actionable worker-pool/cancellation or browser-boundary issue.

The native two-task integration uses a migrated temporary database and real task,
context, runtime, worker, lease and execution services. Two intercepted model calls
overlap on distinct workers, both tasks/runs complete once, both workers reach
idle without further inference, and graceful shutdown stops both workers. This is
native lifecycle evidence with intercepted models, not live cloud inference.

## Validation status

The complete 4149-test run was attempted in `.local/full-hardening-20261102`.
Frozen source digest:
`672da635cc03f53afa459bad93469b929e003f2063b7f294e2b994934a2f1497`.
It used eight isolated batches, exact unique node coverage, fresh temporary
databases and the existing 300-second per-test limit, without retry. Do not claim
it passed: all eight batches ended on inactivity timeout (code 124), complete
JUnit files were absent, and the ad hoc aggregate failed on missing JUnit. The
process log reports roughly 10512 seconds elapsed. The precise inactivity cause
is not established. Rerun the committed CI shards on the next machine; do not
weaken assertions or increase the existing test timeout to conceal this outcome.

Ruff, frontend typecheck, ESLint, all 253 Vitest tests and production build passed.
Earlier focused worker/coordination checks passed 96 tests; the new native two-task
test passed separately, and the final three late-result regressions passed.
The final combined transfer subset passed all 50 tests across hosted coordination,
worker pool and existing team selection after the review fix.

The original personal Groq runtime/profile has not been enabled or used for live
inference. Fresh independently observed account evidence is still needed before
preparing the expiring one-request plan, followed by separate explicit approval.
The one-request test cannot enable ongoing inference. A real cloud task lifecycle
requires its own bounded activation/authority; intercepted tests do not supply it.

Public Groq documentation was rechecked on November 2: the model's published Free
limits remain 30 RPM, 1000 RPD, 8000 TPM and 200000 TPD. Exact private workspace
limits can differ. Current evidence must also check the console's TPM hover for
optional separate input/output limits rather than assuming none exist. The request
headers describe RPD and TPM, not RPM or TPD. Public docs are not private account
or credential-association evidence.

Browser QA remains pending because automatic approval review rejected opening the
synthetic local page after the approval service hit its usage limit. That action
was not executed and no alternate UI automation was used. Protected execution also
requires an operator-enrolled VM; no OS feature or generated-code host execution
was enabled. No push, merge or permanent startup was performed.
