# Authenticated GitHub PR observation

This milestone supplies private observation services; it does not register a model
tool or publication HTTP route. Callers must resolve task/runtime authority before
instantiating the configured repository reader. The collector is not a credential
exporter, test runner, coding engine or authorization importer.

## Configured repository and credentials

`RepositoryTarget` binds a stable numeric repository ID, owner/name, approved base
branch and `codex/` feature namespace. Repository transfer or mismatch refuses.
The feature ref must stay inside that namespace and cannot equal the base. The
operator policy additionally requires at least one independent current-head
approval and configured mandatory checks. Defaults retain `backend`, `frontend`,
`repository-integrity` and `runtime-browser` from the existing Actions workflow.

`WindowsCredentialBroker` resolves a pre-provisioned generic Credential Manager
entry named `Jarvis/GitHub/<operator-selected-name>`. Its native blob is UTF-16LE
and never a repository file. The service does not provision or export credentials.
A trusted broker for repository-restricted GitHub App installation tokens can
implement `CredentialBroker`; token refresh remains inside that boundary.

Tokens are resolved anew for every API call. Authentication expiry/revocation
returns `GITHUB_AUTHENTICATION_REQUIRED`; a missing credential returns
`GITHUB_CREDENTIAL_UNAVAILABLE`. Provider payloads and credential-bearing HTTP
exceptions are excluded from domain error diagnostics. Native credential buffers
are cleared before returning them to Windows. No candidate/test subprocess is
given a broker or token.

The private transport uses only `https://api.github.com`, disables redirects,
environment proxy/netrc credentials and reused cookies, limits response size and
pagination, and validates the configured repository identity independently.
Server retry instructions are represented as `GitHubRetry.retry_after_seconds`.
This service does not sleep or replay mutations; a durable scheduler must persist
retry position and re-resolve authority before another call.

## Evidence and readiness

The collector binds REST check runs and commit statuses to the current remote PR
head. It retains only the latest attempt for each provider/context. Required App
identity is preserved. Duplicate or ambiguous evidence, missing checks, cancelled,
skipped, stale, unknown and failed results cannot satisfy a required check.

Reviews and GraphQL review threads retain original source association. PR-author
and configured publisher identities cannot count as independent approvals. The
collector independently checks current repository write/admin/maintain permission
for each reviewer; outsiders and unavailable permission evidence cannot count. Old
approvals never count for new source. An unresolved old finding or outstanding
change request remains visible. A later comment cannot erase a change request.

REST PR metadata is read again after collection. The collector also resolves the
live base branch directly: `PR.base.sha` can be older than the actual branch tip.
Remote-head drift or a base differing from the approved base blocks readiness.
The observation is a point-in-time result; a later consumer must collect fresh
evidence rather than reusing an old readiness result as mutation authority.

Legacy protection and active branch rules are combined with the operator's
mandatory policy. Inaccessible protection is unknown and blocks readiness. A 404
alone proves nothing; authenticated live branch metadata must positively report
an unprotected branch before absence can be accepted. Active rules and operator
checks and review still apply. Unsupported deployment, code-owner, last-push approval, signature or
other rules produce explicit blockers. The service never reports success by
ignoring a requirement it cannot verify.

`PullRequestReadiness` identifies the repository, PR and exact SHA, reports
blockers and stale evidence references, and always sets
`automatic_merge_allowed=false`. A PR must be open, non-draft and positively
mergeable before it can be reported ready for manual merge. GitHub's merge-policy
state must also be `clean`; conflict-free source alone cannot override blocked,
behind, unstable or unknown provider policy state.

## Validation scope

Unit tests cover stale heads, failed checks, attempt ordering, provider mismatch,
identity/branch escapes, collection bounds, partial GraphQL errors, unresolved
findings, credential redaction, rate limits and live versus cached base identity.
A read-only authenticated observation was also run against real PR #119, finding
12 check/status records, one review and two threads. After resolving the live base
and positively unprotected branch, readiness still refused: the mandatory
`backend` aggregate was unconfirmed and an independent current-head review was
missing. Stale review and thread evidence remained identified explicitly.

This is not the required real commit/push/draft-PR acceptance test. Native commit
execution, durable publication, approved validation admission and correction
orchestration remain subsequent milestones.

Protocol references: [checks](https://docs.github.com/en/rest/checks/runs),
[effective branch rules](https://docs.github.com/en/rest/repos/rules#get-rules-for-a-branch),
[protection](https://docs.github.com/en/rest/branches/branch-protection), and
[rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api).
