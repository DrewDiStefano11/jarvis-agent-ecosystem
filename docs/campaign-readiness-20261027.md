# Jarvis integration campaign: current readiness

Status: **IN PROGRESS; production readiness is not established.** This is the cross-branch checkpoint for the 2026-10-27 campaign, not a production acceptance certificate. Starting remote main: `d8c90576f9fa04a56ff938b7deee005dbff32f72` (#144 integration). The four feature trees remain separate, uncommitted and unmerged at this checkpoint. No production activation or paid inference occurred.

## Acceptance matrix

| Requirement | Current evidence | Remaining requirement |
|---|---|---|
| A: Recoverable continuous runtime | Existing supervisor process smoke verifies owned API crash recovery, duplicate prevention, backup/integrity, restart and stop. Remote branch adds one API lifespan for HTTP/TLS and proves both listeners recover under native backoff. | Permanent-host logon/autostart, real shutdown/reboot, sustained operation and backup restoration acceptance. |
| B: Autonomous useful work | Actual API, separate worker, browser/Office and three-specialist decomposition/synthesis workflows pass using deterministic transport. Local-development branch connects independently approved loopback code proposals and native source mutation fixtures. | Persistent routine goal admission/resource scheduling, complete protected validation/correction integration, and actual installed-model quality and useful-deliverable acceptance. Queued records alone do not prove execution. |
| C: Secure remote operation | Default-off sessions, exact HTTPS host/origin, CSRF, live native RBAC, goals, cancellation, emergency controls, versioned runtime controls and exact-hash workspace reservation approval. Real standalone/supervised TLS/browser workflows pass; seeded goals are identified as demonstrations. | Other protected source/development approval UI, deployment configuration, trusted certificate/private-network access from a separate device and sustained host operation. |
| D: Self-diagnosis and correction | Native diagnosis/evidence/triage/baseline machinery is retained; capacity-control conditions no longer masquerade as software weaknesses. Real proposal/source mutation fixtures exercise exact approval and replay fences. | Connect diagnosis through independent protected test collection and measured correction review. A correction proposal is not a tested repair. |
| E: Isolated development | Actual disposable Git/workspace/source changes and independent approval/replay/stop checks are exercised. Durable native leases/checkpoints/outbox remain authoritative. | Protected collector integration and actual supported OS containment are unverified. Shell/code execution remains unavailable under contributor policy; do not present a validation contract or supplied receipt as actual sandbox execution. |
| F: Scheduled improvement | Scheduler branch persists immutable bounded evidence windows, resumes after process interruption and optionally admits one advisory task under permissions, resource limits and emergency stop. Native tests and actual restart controls pass. | Merge capacity taxonomy first; connect controlled measured experiments through E, then verify repeated permanent-host cycles. Advisory scheduling does not grant inference or execution. |

All six remain **PARTIAL**. Some remaining work is software integration; it must not be mislabeled as solely an external-host blocker.

## Branches and integration order

1. `codex/final-readiness-20261027`: truthful evaluation identity/exit status, capacity attribution, scheduled-task restart ownership, permanent-host acceptance and runbooks. Details: [acceptance handoff](handoffs/final-readiness-20261027.md).
2. `codex/local-development-proposals-20261027`: default-off loopback development routing and endpoint-bound approval, cloud-alias rejection, pre-dispatch revocation checks. Its handoff is `docs/handoffs/local-development-20261027.md` on that branch.
3. `codex/guarded-improvement-scheduler-20261027`: persistent advisory scheduling. Depends operationally on branch 1's capacity classification. Its handoff is `docs/handoffs/guarded-improvement-scheduler-20261027.md` on that branch.
4. `codex/remote-browser-operation-20261027`: browser/operator controls, supervised shared-state TLS and demonstration provenance. Its handoff is `docs/handoffs/remote-browser-20261027.md` on that branch.

Each starts at the same main SHA. Preserve all shard-manifest additions, contracts and native authority checks when integrating overlapping configuration/lifespan changes. An isolated integration branch must preserve the exact feature ancestry and receive its own verification. No branch is merged into main automatically. PR URLs and final commit IDs must be added after publication; there are none to report at this checkpoint.

## Verification provenance

Evidence lives in ignored primary-checkout `.local/acceptance-20261027`. Source-byte manifests distinguish uncommitted trees; an uncommitted branch name is not an exact tested revision.

- Acceptance tree: affected capacity/improvement/evaluation/provider package289 pass/1 installed-model skip; identity/host/evaluation59 pass/1 skip; real supervisor and worker/browser/decomposition controls pass. Fresh frontend234 tests/typecheck/ESLint/build pass.
- Local proposal tree: final native42 pass, provider/dispatch232 pass with1 native case intentionally deselected from that second package; earlier85-test development regression is retained earlier-tree evidence. No actual Ollama installation/start/download/inference occurred.
- Scheduler tree:197 tests pass; actual process restart after capture/admission passes. Fresh frontend234 tests/typecheck/ESLint/build pass.
- Remote supervised backend tree:161 final supervisor/remote tests pass; prior remote/workspace124-test package retained separately. Source-byte identity of the13 changed/new API files was checked before and after the frontend provenance increment.
- Remote current frontend:247 tests across34 files, typecheck, ESLint and build pass. Two actual TLS/browser cases pass in18.04 seconds,93 native requests each, zero page errors, layouts1280/390/320. Actual seeded completed output is labeled; new operator work is not. Supervised case verifies owned API death, native restart/backoff, both listeners and durable goal recovery.
- Current backend collection:3865 cases. Runtime420 pass; autonomy503 pass. Models1301 pass/3 explicit skips after exact replacement of7 setup-error nodes. The system1638-case exact union is still pending:58 complete passes retained,1580 remaining cases running in8 isolated local batches. No aggregate full-suite pass is claimed yet.
- Interrupted baseline72990 stopped at93% without result XML. Initial long-temp-path runtime/system/models errors are environment-invalid runs, not application failures or passes. Native reproduction gave `fatal: '$GIT_DIR' too big`; short temporary roots resolved the affected setup. Keep their evidence separate.

The local batch orchestrator records source stability, complete retained phases, unique collected node coverage, per-batch outcomes and explicit skips. Existing GitHub CI jobs/security gates are unchanged. Skipped checks are not passing acceptance evidence. Final branch commits and their exact CI/review results still require publication and inspection.

## Practical acceptance status

| Scenario | Development evidence | Production verdict |
|---|---|---|
| A: Ordinary objective | Real coordination, specialists, synthesis and recovery with deterministic transport | NOT TESTED with actual installed models |
| B: Seeded software defect | Real proposal, independent exact approval and native source mutation; no protected candidate test execution | INCOMPLETE: collector/containment and tested correction remain |
| C: Self-diagnosis | Native failure classification/provenance and correction machinery; complete tested repair chain missing | INCOMPLETE |
| D: Restart recovery | Real supervisor, worker/workflow and TLS process recovery controls | PARTIAL; permanent-host interruption/reboot acceptance remains |
| E: Measured improvement | Native comparison/backlog plus persisted advisory scheduler; no complete protected experiment | INCOMPLETE |
| F: Remote operation | Real TLS/browser submission, inspection, controls and recovery | PARTIAL; separate device/certificate trust remains |

## Start and operate on the prepared host

After normal approved setup, build once with `pnpm --dir apps/web build`, then from repository root:

```powershell
.\scripts\jarvis.ps1 doctor
.\scripts\jarvis.ps1 start
.\scripts\jarvis.ps1 status
```

Use `scripts\jarvis.ps1 backup` before approved maintenance and `scripts\jarvis.ps1 stop` for graceful shutdown. Supervisor logon integration is not a boot service. The owner manages Ollama separately. Worker, local/cloud providers, scheduler and remote access retain their explicit activation requirements; these commands do not grant execution permissions.

Read [permanent-host acceptance](permanent-host-acceptance.md) for offline preparation, discovery, actual local model evaluation and existing authorized-task checks. The default command is `python scripts/permanent-host-acceptance.py`; it does not install/start/download models and must not turn fixture success into host readiness. Live model options are for the permanent host only.

## External conditions and next actions

The owner must provide the permanent host, supported containment boundary, actual installed-model identity/capacity, trusted private-network device access, appropriate independent operators and explicit production activation/promotion approval. Do not change billing, provision paid inference, expose a public listener, register host startup or merge PRs without the required owner action.

Next: finish the exact current backend node union, inspect failures and publish the four reviewed feature PRs with truthful tested-source provenance. Inspect final-head CI/Codex review without idle polling. Then verify a separate integration branch and finish the remaining admission, protected validation/correction and operator-approval UX work. Repeat all mandatory scenarios using actual installed models on the permanent host before considering full operational readiness.
