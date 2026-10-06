# Software validation planning

`app.development_validation` implements deterministic planning and evidence
assessment for Jarvis changes. It performs no source inspection, commands, models,
Git/GitHub calls, evidence import, persistence or execution authorization. The
production command journal and durable workflow integration are subsequent
milestones. A plan is a requirement list, never a claim that tests ran.

The caller supplies a `CodeState` from a trusted native repository observer:
canonical registered repository identity, exact HEAD/base commits and a complete
bounded sorted delta of paths with before/after SHA-256 content hashes. Additions,
untracked files and deletions are explicit; renames are deletion plus addition.
Windows/POSIX escape paths, devices, duplicate case-folded paths and unchanged
hash pairs fail validation. The future observer must verify completeness and
repository identity; accepting model-written snapshots would violate this boundary.

Iteration plans select backend Ruff/tests or frontend type/lint/tests/build based
on affected paths. A trusted impact map may supply explicit backend test files,
never a shell string, executable, pytest argument or model-selected node selector.
Without a mapping, backend validation is broad. Unknown code/policy changes fall
back to all publication gates. Documentation iterations need only integrity.
Migration/database changes add a migration gate; frontend source/public changes
add browser acceptance automatically. Operators may add browser acceptance to
other changes; they cannot disable a derived requirement.

Publication always requires backend Ruff, full pytest, frontend typecheck,
ESLint, full Vitest, build, repository integrity and the contributor-required blank
migration roundtrip. Focused test passes cannot substitute for the full suite.
CI and independent code review remain separate exact-head handoff requirements;
this module alone never establishes PR merge readiness.

Plans and component scope fingerprints bind repository/HEAD/base, canonical changes,
selected test targets and policy/implementation digest. The policy digest covers
both planner and contract source as well as the explicit gate/scope definition.
Changed implementation therefore invalidates old evidence without relying only
on a human version bump. Assessment rederives minimum requirements from the current
code state, checks fingerprints and blocks a self-rehashed plan that removed gates
or protected paths. Protected credentials/configuration, Git authority, runtime
SQLite sidecars and generated/dependency directories cannot become acceptable
because tests passed.

A future trusted persisted command journal supplies `ValidationEvidence`; no HTTP
or model-output importer exists. Evidence records exact state/scope/targets/policy,
command-run ID, UTC-aware finish time and measured outcome. Failure classification
is explicit: product, test, infrastructure, CI environment, watchdog, dependency,
migration, lint/type/build or unknown. Classification does not infer root cause
or waive a gate. Cancelled/unmeasured work is missing evidence. The newest matching
observation wins; simultaneous contradictory outcomes fail conservatively.

Iteration may reuse unchanged component evidence across an unrelated uncommitted
component edit. Publication requires the exact current full code-state hash.
Changed HEAD/base/repository, relevant source, target suite or policy invalidates
reuse. Assessment handles at most 512 observations and reports missing, failed,
stale and blocked requirements separately. It grants no merge/command authority
and accepts no implicit infrastructure waiver.
