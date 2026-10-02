# Main workspace smoke integration repair

Base: `f130e47e61000120d10e62adcf286d12b8140bdb`.

Production team selection adds a capability inference request before workspace
planning. The smoke provider now returns the capability schema for that request
and verifies exactly one capability request and one workspace-plan request across
API restart. The evidence directory is resolved to its canonical Windows path so
the filesystem safety checks see the same path as the file handle.

Validation on 2026-10-02: backend Ruff format/lint pass; complete backend suite
1,290 passed, 2 skipped; frontend typecheck/lint pass, 101 tests pass, build passes;
blank upgrade to `20260906_10`, downgrade to `20260729_04`, re-upgrade pass.
Twelve autonomy fixture scenarios and model evaluation/qualification fixtures pass.
Office/browser, workforce and repaired workspace execution/restart smokes pass.
Windows filesystem tests require unsandboxed execution. Fixture evidence is not
installed-model evidence. No reachable Ollama service was available.

The repair changes smoke evidence only. Do not merge automatically.
