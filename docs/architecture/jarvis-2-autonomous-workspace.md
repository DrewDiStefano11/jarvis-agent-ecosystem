# Jarvis 2.0 autonomous workspace architecture

Baseline: main `6b38d4c61b4eae67ba4a0f3c9231cc4a8f26d31b`, fetched and independently verified through GitHub on November 6, 2026. The owner supplied the master specification, detailed UI specification, continuous execution requirements, final acceptance requirements and six-screen reference. Those requirements authorize implementation without another design approval cycle.

## Approach and preservation

Extend the existing React/FastAPI application in complete vertical slices. A frontend-only redesign cannot satisfy durable intake/review/recovery. A replacement task engine would discard native authorization and recovery guarantees. Reuse native task IDs, runtime commands, context assembly, leases, checkpoints, provider dispatch, RBAC, audit and transactional outbox. No inference, production data mutation, deployment or merge is authorized.

## Feature parity and navigation

| Existing entry / capability | Jarvis 2.0 destination | Source / preservation boundary |
| --- | --- | --- |
| Overview | Home and Mission Control | Dashboard.tsx; preserve operational projections |
| Tasks, task drawer, corrections and dependencies | Work and conversation task links | Tasks.tsx, Details.tsx, task_creation.py; native TaskRow |
| Business Lab, project objectives | Work / Projects | BusinessLab.tsx; retain unique objective controls |
| Planning, team selection, decomposition and coordination | Inline task flow; Settings / Advanced | Runtime.tsx; context, team_selection, decomposition, coordination services |
| Agent management and catalog | Mission Control / Agents | Agents.tsx, catalog and identity services; distinguish simulation from workers |
| Approvals | Inbox | Approvals.tsx; each native authorization remains distinct |
| Model result review | Inbox and conversation | autonomous_worker result records and native run/checkpoint transitions; missing operator decision integration |
| Activity and runtime history | Task history; Settings / Advanced | Audit.tsx, runtime ledger; append-only audit |
| Provider routing, quotas and credentials | Settings / AI and budgets; Mission Control resources | Models.tsx, provider_configuration, native Groq acceptance/rate states |
| Workspace tools, source editing and validation | Library sources; task details; Advanced | tool_execution, source_change, source_mutation, validation_runner; exact plan hash and containment |
| Reports and artifacts | Library | Existing ArtifactRow and native tool artifacts; enforce source permissions |
| Office | Office | Existing lazy-loaded Office.tsx and shared state; preserve artwork/geometry/movement |
| Self-improvement | Settings / Advanced | Existing advisory evidence/backlog; scheduling disabled |
| System administration and emergency controls | Mission Control; Settings / Advanced | System.tsx, system controls; persistent emergency stop |
| Recovery, supervisor and backups | Settings / Storage and Advanced | runtime_supervisor; never start production services |
| Remote access | Settings / Advanced | Existing separate TLS/authenticated gateway; no new public exposure |

Primary navigation: Home, Work, Inbox, Mission Control, Library, Office; Settings at the bottom. Preserve /tasks, /agents, /approvals, /runtime, /lab, /audit, /system and /models as compatible destinations until their replacements pass acceptance. Mobile prioritizes Home, Work, Inbox, Mission Control, with accessible More. Heavy pages remain lazy-loaded.

## First functional slice: durable draft intake

A paragraph creates a durable conversation, original user message, explicit goal record and exactly one native task in one transaction. Store the original text unchanged. A bounded title excerpt is a display label only, not AI interpretation. The initial state is saved draft: no plan, selected worker, model request, reservation or execution is implied. An emergency stop still permits saving a draft, but grants no execution authority.

POST /api/goals requires an idempotency key and strict request validation. Replaying the exact request returns its original linked IDs; altered content with that key conflicts. Use the existing idempotency lease and commit it with task/conversation records and a task.created audit/outbox event. GET /api/goals provides bounded pagination; GET /api/goals/{id} restores the conversation. These are trusted local operator endpoints protected by existing loopback Host/Origin/peer middleware; the remote gateway must not implicitly expose them. No model text supplies permissions.

Frontend domain state belongs in apps/web/src/state. Home owns only unsent input and local display state; persisted conversations/goals/tasks are held in the shared store. Keep failed submission keys stable for explicit retries, including a lost acknowledgement/reload, to prevent duplicate tasks. Never auto-resubmit on navigation or reconnect.

## Native state projection and subsequent execution

Goal intake is not a second task state machine. Its draft disposition describes intake only. Task/run state remains authoritative: queued without a run means saved work, active attempt means running, completed generation with paused review means awaiting review, succeeded native transition means accepted completion. Render actual results and assumptions, never just execution IDs or fabricated progress.

Before automatic admission, persist a versioned stage budget covering selection, decomposition, planning, execution, critique, synthesis, validation, retries, repairs and corrections. Reserve a conservative aggregate envelope, then recheck native provider eligibility/current evidence, RPM/RPD/TPM/TPD and outstanding reservations before every dispatch. Unknown/stale capacity blocks. A simple request can have a single planning call; navigation and idle cause zero inference. Owner activation remains separate from setup preferences.

## Authorization boundary

Operator request -> strict HTTP contract -> local boundary / native RBAC -> immutable plan and aggregate budget -> admission -> native queue -> lease/checkpoint -> exact scope/hash recheck -> provider/tool dispatch -> durable result -> operator review -> native completion.

Untrusted files/model text enter context only. Neither may grant authority. New revisions fence old scopes. Review decisions require actor permissions, expected run version, matching validated checkpoint/result and idempotency. Accepting output never authorizes workspace mutation or another provider call.

## Additive migration and deletion policies

Add conversation/message/goal linkage tables without rewriting existing tasks or runtime history. No startup create_all. Test Alembic upgrade, downgrade safety, re-upgrade, foreign keys, restart and transaction rollback on disposable databases. Refuse downgrade when new user records exist unless exported/removed through a reviewed policy. Existing live database and encrypted credentials remain untouched; obtain/verify an isolated backup before testing a populated-copy migration.

Later archive/trash metadata is distinct from runtime state. Trash defaults to 30 days; cleanup starts disabled. Active tasks/descendants require native cancellation, lease fencing and resolution of uncertain commands before content erasure. Conversation deletion cannot orphan work. Permanent deletion removes eligible user content and artifacts while retaining minimal redacted append-only safety records; disclose retained backups and never promise immediate physical erasure everywhere.

## Delivery stages and acceptance gates

1. Architecture, parity inventory and isolated baseline screenshots.
2. Durable draft intake plus functional conversational Home, actual API restart, duplicate/failed submission tests and responsive browser verification.
3. Aggregate budgeting and disabled-by-default standing admission policies; 8000 TPM fixture rejects oversized graphs before first call.
4. Native result review and unified Inbox, including a faithful five-recommendation fixture and permission/version/replay failures.
5. Complete Work/projects and archive/trash/restore/eligible erasure with descendant/recovery tests.
6. Bounded attachments, Library, previews and explicit authorized sources; traversal/symlink/untrusted authority regressions.
7. Mission Control, Settings, notifications and recurring controls; retain optional Office.
8. Actual API/browser offline acceptance A-M at 320,390,768,1024,1440,1920; record operator interactions, task rows, fixture calls, reservations, final states and restart recovery. No live acceptance without separate authorization.

Every source milestone includes focused tests, required quality gates, browser interaction evidence where applicable, a reviewable commit/PR and ledger update. Existing passing historical checks are not claimed for changed sources.
