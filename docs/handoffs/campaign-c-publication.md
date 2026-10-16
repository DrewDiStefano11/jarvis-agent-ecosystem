# Campaign C: Git and GitHub publication

## Audit, 2026-10-13

Fetched `origin/main`: `5749a41bbe2f5a6f3f4f396ab11e5662fc91e047`.
The operator authorized real acceptance publication to
`DrewDiStefano11/jarvis-agent-ecosystem`, base `main`, using an isolated feature
branch and draft PR. Automatic merge, main pushes and force pushes remain forbidden.

Actual Git ancestry, rather than the PR's merged flag, establishes availability:

| PR | Actual base | State | Reachable from audited main |
| --- | --- | --- | --- |
| 107 | main | merged, `d24148b467721cb9bdb16a28faf9a8e4c664d54d` | yes |
| 109 | codex/git-publication-verification | merged, `086b2a3c081c471f01e421aa9954d72fbe5bd5ad` | no |
| 117 | codex/git-commit-preparation | merged, `b4121396e92cbcdd428d3665549fa2a9994e5340` | no |
| 119 | main | open | no |

Main contains native inspection and change verification, but not exact commit
preparation/authorization or applied-source provenance. PR 119 is the integration
dependency, not permission to replay source edits or change Campaign A modules.

The existing `.local/git-native-commit` worktree contains uncommitted private
native commit primitives and tests. They deliberately expose no execution route.
Its documentation identifies the missing full object/index/reference effect
runner and final recovery acknowledgement. Preserve this worktree and its edits;
do not claim these primitives are a production commit service.

The existing `self_improvement.ci.CIRunSource` reads operator-exported run evidence
and marks it incomplete. It is not authenticated live PR observation.
The Actions workflow uses Windows backend shards, Linux Git checks, frontend,
repository-integrity and browser checks. Never narrow this policy for publication.

## Boundaries and interfaces

Campaign C owns publication contracts and services. It does not execute candidate
source code, invoke a coding model, or operate Campaign A/B runners. No model text
or caller-provided verdict establishes authority.

The eventual validation adapter must resolve Campaign B's protected journal and
return: task, runtime run/attempt, workspace ownership, exact source observation
digest, prospective tree/commit SHA, mandatory policy digest, immutable assessment
and final acknowledgement/checkpoint identity, complete test receipt identities,
independent operator approval identity, and current authorization. Missing,
pending, stale, fixture-only or historical-only evidence blocks publication.
An adapter must re-resolve these fields at each publication boundary.

The PR observation evaluator is pure. Its input must come from a separately
authenticated collector that binds repository ID, PR number, base branch and
current remote head, paginates all evidence, and observes head again after
collection. It does not independently establish API provenance or authorization.

## Remaining work

- Integrate reviewed preparation/source provenance dependencies against main.
- Complete fenced native commit execution and restart recovery.
- Implement external credential broker, configured repository/branch scope and
  authenticated GitHub transport, with bounded retries and duplicate reconciliation.
- Bind validation admission, publication journal, outbox and runtime checkpoints.
- Generate draft PR content from verified source and validation receipts.
- Expose authorized APIs/events and exact-head observation/review/correction routing.
- Run full required local checks, real authenticated acceptance, fresh CI and review.

Reference GitHub's [check runs API](https://docs.github.com/en/rest/checks/runs),
[App security practices](https://docs.github.com/en/apps/creating-github-apps/about-creating-github-apps/best-practices-for-creating-a-github-app),
and [rate-limit handling](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api).

## First milestone in this branch

`app.models.github_publication` defines bounded immutable observation and readiness
contracts. `app.github_publication.collector` uses authenticated REST checks,
commit statuses, reviews, legacy protection, active rulesets and GraphQL review
threads. Repository ID, base and owned branch namespace must match. A second PR
read detects changes during collection. Required provider identity is respected;
older success cannot satisfy the current commit. Unresolved old findings remain
visible. The evaluator does not merge or grant publication authority.

The credential broker reads an operator-provisioned Windows generic credential
under `Jarvis/GitHub/`, afresh for each request. The credential blob uses UTF-16LE.
Credentials are not installed, exported, written to repository files, placed in
commands or returned through public contracts. A GitHub App installation-token
broker can implement the same private protocol. API clients disable environment
proxies/netrc, redirects and cookies reused across requests. Responses and
pagination are bounded. Diagnostics omit provider response bodies and HTTP
exceptions containing credential-bearing requests. Rate-limit responses return
a retry delay for a later durable scheduler; this milestone does not retry effects.

Unknown/inaccessible protection blocks readiness. Signature, deployment,
code-owner, last-push and other additional requirements remain explicit blockers
until an authenticated adapter can verify them. This collector has no registered
HTTP or model execution route. It does not claim native commit creation, branch
publication, live runtime admission or completed real acceptance.

Focused tests use HTTP fixtures only and must not be reported as real GitHub
publication acceptance. The read-only live collector was separately verified
against PR 119 using the existing operator session without exporting its token.

Local verification for this milestone: Ruff format/check, blank-database migration
roundtrips, all four backend shards (402 runtime, 503 autonomy, 851 models, 924
system passed with one optional live-Ollama skip), final 70 focused observation tests,
and complete final collection/shard coverage of 2,685 tests. Frontend typecheck,
ESLint, all 234 Vitest tests and the production build passed. Focused tests were
rerun after the final collector changes; remote exact-head CI and independent
review still must run for the published milestone. Real commit/push/PR acceptance
through the Jarvis service remains required and has not been claimed.

Targeted follow-up (2026-10-16): recovery renewal approvals are bound to their
persisted workspace, command, operation, original approval and measured journal
prefix. A renewal cannot create a new command intent. Valid progress after the
measured prefix remains recoverable; original evidence stays append-only.
Frontend sources are unchanged; prior frontend validation evidence is retained.
