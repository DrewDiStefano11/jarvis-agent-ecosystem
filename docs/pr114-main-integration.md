# PR #114 integration into main

## Scope and comparison

The integration starts at main `dc63ed2`, after #112. PR #114 was merged
into `codex/source-mutation` as `7d802481`, with head `0481329`.
The common ancestor is `8325fe8`; main already contains the source mutation
foundation, durable original identity claims, native read coordination, and
index admission fences. Those existing changes are not copied again.

Missing from main are the four recovery/artifact models, protected history
reader, expected artifact manifest reader, native inspector/verifier, their
regression tests, and associated documentation. An ancestry-aware merge of
`7d802481` adds exactly #114's 14 changed files (2,832 insertions, one deletion)
before this integration note and a trailing blank-line correction. It has no
conflicts. Main's newer validation journal, runner, repair evidence, and frontend
remain intact. No HTTP routes or migrations are introduced.

## Dependency on #119

#119 targets main and depends on #112 and this integration. Its protected
applied-source binding consumes `MutationHistoryReader`, `ArtifactManifestReader`,
and `MutationInspector`; its prospective Git preparation and independent
operator authorization foundations (#109/#117) remain part of #119.

An uncommitted merge-tree rehearsal of #119 head `750d368` with main `dc63ed2`
is conflict-free. Its recovery/artifact modules and shared source tests match
#114 exactly. This proves merge compatibility at those refs, not final-head
validation or merge readiness after this integration reaches main.

Merge this integration through human review first. After it reaches main,
fetch main and #119's current head, merge main into #119's feature branch,
resolve any conflicts while retaining newer main functionality, and rerun
local backend and frontend validation before pushing the synchronized branch.
Review the resulting smaller diff and current review status before reporting
merge readiness. Neither this PR nor #119 may be merged automatically.

## Remote CI boundary

The repository's single Actions workflow was active with cache and artifact
storage enabled. It was disabled through GitHub before publishing this branch.
The operator explicitly approved cancelling the seven identified outstanding
runs, including main and unrelated branches. GitHub confirmed no in-progress or
queued runs remained, and the workflow remained `disabled_manually`.
No paid services or artifact downloads were enabled. Keep Actions
disabled until the operator verifies the $0 spending boundary. Local results do
not imply hosted CI or review approval.

## Local validation

Validation results are recorded in the integration PR description after the
checks finish. Test databases and build outputs remain untracked and isolated.
