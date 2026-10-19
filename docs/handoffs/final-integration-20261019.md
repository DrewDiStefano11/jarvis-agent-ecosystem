# Final integration — 2026-10-19

## Integration decision

PR #143 head d4984a92c66e59a184d7181db4653314f4a738bb is an exact Git ancestor
of PR #144 head 393aaaef1c95bd3a8f7c67911ecbe96b4e6ea6a1. The descendant contains
all native commits plus native state-reader, recovery command approval and
validation-disable fixes. Use #144 as the combined integration; do not merge
#143 independently. Leave closing the superseded PR to the maintainer.

Merge authoritative main 5073e56c0eb5969b13378b68497d6809430757eb into #144
without rewriting published history. Resolve application wiring by retaining
both development admission/review and native/publication services and routers,
including every authenticated operator router. Preserve main's shared provider
persistence, development executor quarantine and migration join. Retain both
API documentation sections. Remove a duplicate commit-preparation construction.

The combined tree adds native/publication modules without removing or changing
existing main implementation modules beyond application composition. Frontend,
manifests, workflows and migrations remain identical to main. The shard manifest
retains all tests; collection must prove the exact union. A regression checks
combined services and routes with remote control enabled and disabled. Remote
schema inspection uses the protected application schema rather than bypassing
the HTTP access middleware.

The historical review finding about 15 unused generated Stitch ZIP exports is
resolved by removing those exports and ignoring that exact export name pattern.
Canonical frontend assets and build inputs are unchanged; original local copies
in the primary checkout are preserved.

## Required evidence

Run Ruff, backend pytest, frontend typecheck/ESLint/Vitest/build, isolated migration
round-trip, repository-integrity and browser checks. Record exact final-head
hosted CI and fresh Codex review separately; old success is not current approval.
Historical #143 system failure included a completion-checkpoint rejection test
and a timeout in the large native execution approval/staging test. Reproduce
these against the composition before attributing either to an implementation defect.

## Manual acceptance limits

Ordinary local simulator/planning acceptance can use an isolated runtime and
explicit loopback planning. Native execution/publication remain default-off.
Full autonomous coding acceptance still requires approved development-provider
integration, actual-host isolation/containment acceptance, independently protected
Campaign B receipts and native continuation, publication credentials/profile,
and approved hosted-CI cost policy. Fixture gates cannot supply these authorities.
No live provider, credential, VM, production database or external integration is
activated by this integration.

## Manual disposition

Once exact-head CI and independent review pass, merge #144 manually into main,
then close #143 as superseded. Do not merge #143 afterward. If either gate fails,
retain both PRs and repair #144; no automatic merge or branch deletion is authorized.
