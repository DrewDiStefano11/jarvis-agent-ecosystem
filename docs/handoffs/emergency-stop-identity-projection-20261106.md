# Emergency-stop manager projection repair

Base: `bbcad1d59056170b82a21181ad5b5995e4cd8c93`.
Branch: `codex/emergency-stop-identity-projection`.

The actual host's emergency-stop request returned HTTP500 after loading a native
planning manager from preserved task history. `tasks.assigned_manager_id` has a
foreign key to legacy simulator `agents`, while the validated task payload can
legitimately name a native identity. A global/system save copied the native ID
into that legacy foreign key and rolled back the stop transaction.

The repair preserves the native task payload and team-selection assignment. An
instance persistence wrapper projects only actual simulator-agent IDs into the
legacy manager column; native managers project to null. Global persistence,
updated-task events, task insertion and context commits use this boundary. Foreign
keys, native authorization, transaction/outbox behavior, response envelopes and
schema/event versions remain unchanged. No migration is required.

The original static `_task_row` factory remains compatible with PR165's class-level
correction creation and bounded-tool callers. Fresh review identified that
integration requirement; a failing compatibility regression reproduced the first
implementation's TypeError before the static factory was restored.

Observed local validation on the final working-tree content:

- Native-manager stop/context cases failed HTTP500 before the repair; legacy stop
  control passed. Four final focused cases pass, including static correction rows.
- Final persistence/context/team-selection/API/intake/worker/tool regression:
  163 passed. Earlier 162-case evidence predates the compatibility adjustment.
- Ruff lint and formatting pass across 593 Python files. Collection: 4182 nodes;
  collection is not a full backend execution pass.
- Candidate frontend: Node22 typecheck, ESLint, 262 Vitest tests and build pass.
- Final consistent host-backup copy: stop200, stop survives app restart, resume200;
  all 11 task IDs and manager payloads plus all 5 execution hashes/JSON preserved;
  integrity and foreign-key checks pass. Original backup hash remains unchanged.
- Fresh whole-diff review closed the integration P1 and found no further actionable
  findings. It did not certify deployment or independently rerun every check.

Sanitized receipts and commands are under `C:/Jarvis/host-campaign-20261106`:
`repair-final-green.xml`, `repair-final-regression.xml`,
`repair-host-copy-verification-final.json`, frontend/static logs and `CAMPAIGN.md`.
The initial broader invocation referenced a nonexistent test filename and ran no
tests; that setup error remains recorded separately from passing runs.

The permanent installation remains on unchanged reviewed main with inference,
Ollama integration and its worker disabled. The owner separately approved the
current-user logon task for that contained management configuration; a manual
scheduled launch passed. This does not approve repair deployment or reboot/soak
acceptance. Original full baseline system tests remain running in a separate
checkout; runtime437/autonomy510/models1378 passed with three explicit skips.

Next: obtain explicit push approval, publish a reviewable repair PR and complete
its applicable checks. Owner review/merge and exact-revision promotion precede
live stop verification and any reactivation. Integrate this repair before the
164/165/166 UI stack. Preserve all old data/worktrees; never clear the defect by
editing live manager IDs, disabling foreign keys or resetting history.
