# Code Review Checklist

This checklist is strictly for reviewers and repository owners. Reviewers must complete these checks prior to approving or merging any code into the repository.

## Base and Scope Verification

- [ ] Verify the PR base branch matches the intended target (usually `main`).
- [ ] Ensure the exact base SHA is recorded and current.
  - *Warning:* Obsolete base branches can make isolated prototypes appear to modify the entire application.
- [ ] Independently verify the claimed file list against the actual PR diff.
  - *Warning:* Claimed file lists must be independently verified; authors or agents may incorrectly summarize their changes.
- [ ] Confirm no unrelated or excluded domains are modified.

## Architecture and Ownership Boundaries

- [ ] Ensure changes respect logical separation (e.g., control-plane, filesystem, runtime-supervisor, and frontend).
- [ ] Validate that no temporary workarounds cross established boundaries without documentation.

## API and Schema Compatibility

- [ ] Verify changes to HTTP/event contracts do not break backward compatibility unless intended.
- [ ] Ensure any OpenAPI schema or data contract modifications are fully covered by updated tests.

## Persistence and Transaction Boundaries

- [ ] Ensure transactions are properly scoped and explicitly defined.
- [ ] Verify that audit logs and event publishing occur correctly within transactional boundaries (e.g., transactional outbox).

## Concurrency and Idempotency

- [ ] Validate that state transitions are safe under concurrent execution.
- [ ] Ensure operations intended to be idempotent actually are.

## Security

- [ ] Scrutinize filesystem operations, process launching, and shell execution.
  - *Warning:* Path normalization alone is not a complete filesystem race defense.
  - *Warning:* Process launch before durable registration can create unmanaged children.
  - *Warning:* PID existence alone is not process identity.
- [ ] Verify no secrets, tokens, or sensitive context payloads are logged, exposed, or committed.
- [ ] Ensure authentication boundaries and authorization checks remain intact.

## Migrations and Recovery

- [ ] Ensure all schema changes are handled via valid migrations (no `create_all` at runtime).
- [ ] Confirm the rollback/recovery plan is logical and sound.
- [ ] Check that durability and recovery positions are strictly managed by proper workflow checkpoints.

## Test Quality

- [ ] Verify tests use isolated environments (e.g., temporary databases) and clean up correctly.
- [ ] Assess the edge cases tested.
  - *Warning:* Passing tests may miss an untested failure window. Reviewers must evaluate race conditions and missing assertions manually.

## CI Interpretation

- [ ] Review CI output directly; do not rely solely on summarized "green" indicators.
- [ ] Ensure format, lint, typecheck, build, and specific backend/frontend test suites have all successfully run.

## Final Pre-Merge Verification

- [ ] Re-verify the exact head SHA immediately before merge to ensure no late pushes occurred.
- [ ] Confirm that "no unresolved threads" is backed by actual, independent human review.
  - *Warning:* "No unresolved threads" does not mean an independent review occurred.
- [ ] Ensure that documentation matches the final repository state.
  - *Warning:* Documentation claims must match repository state.
